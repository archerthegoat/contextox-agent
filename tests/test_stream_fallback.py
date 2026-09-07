"""Synthetic Provider failures through the real Agent, Store, and message boundary."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from contextox import agent
from contextox.provider import (ProviderCompletion, ProviderToolCall, ProviderUsage,
    ProviderContextBudgetError, ProviderStreamInterruptedError, ProviderProtocolError,
    ProviderTimeoutUnknownError, ProviderCancelledError, ProviderError)
from contextox.store import WorkspaceStore, Path2StateError
from test_runtime import _mission
from test_dialogue import request


def answer(usage=True):
    return ProviderCompletion('synthetic', 'Replacement content', None,
        (ProviderToolCall('finish', 'finish_run', json.dumps({
            'outcome': 'partial', 'reason': 'Replacement answer', 'evidence_handles': []})),),
        'tool_calls', ProviderUsage(2, 3) if usage else None)


class ScriptedProvider:
    config = {'endpoint_id': 'deepseek_chat_completions', 'model': 'deepseek-v4-flash',
              'thinking': 'enabled', 'reasoning_effort': 'high'}

    def __init__(self, actions, cancel=None):
        self.actions, self.calls, self.cancel = list(actions), [], cancel

    def opaque_user_id(self, ws):
        return 'synthetic-user'

    def complete(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), kwargs))
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            if kwargs['stream']:
                kwargs['on_content']('Discarded partial text')
            if self.cancel:
                self.cancel.set()
            raise action
        return action


class StreamFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='contextox-fallback-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore.open(self.temp.name)
        self.ws, self.mission = _mission(self.store)
        self.mid = self.mission.mission_id

    def run_case(self, actions, cancel=None):
        initial, _ = self.store.send_task_message(self.ws, self.mid, request())
        provider = ScriptedProvider(actions, cancel)
        with patch.object(agent, 'get_provider', return_value=provider):
            agent.run_agent(self.store, self.ws, self.mid, initial.run.run_id, cancel or Event())
        return provider, self.store.get_run_snapshot(self.ws, self.mid, initial.run.run_id)

    def continue_message(self):
        mission = self.store.get_mission_snapshot(self.ws, self.mid).mission
        return self.store.send_task_message(self.ws, self.mid, request(mission.state_version))

    def test_wire_fallback_is_same_input_two_receipts_one_terminal_and_resumable(self):
        provider, run = self.run_case([ProviderContextBudgetError(stage='sse_wire'), answer(False)])
        self.assertEqual(run.status, 'partial')
        self.assertEqual(run.final_output, 'Replacement answer')
        self.assertEqual([c[1]['stream'] for c in provider.calls], [True, False])
        self.assertEqual(provider.calls[0][0], provider.calls[1][0])
        for key in ('tools', 'max_tokens', 'user_id', 'max_context_bytes'):
            self.assertEqual(provider.calls[0][1][key], provider.calls[1][1][key])
        self.assertEqual([r.turn_index for r in run.provider_receipts], [1, 2])
        self.assertEqual([r.error_code for r in run.provider_receipts],
                         ['stream_fallback_sse_wire', 'provider_fallback_usage_missing'])
        self.assertEqual([r.usage_status for r in run.provider_receipts], ['missing', 'missing'])
        events = self.store.list_run_events(self.ws, self.mid, run.run_id)
        starts = [e.public_payload for e in events if e.event_type == 'model_started']
        self.assertEqual(starts[1].transport, 'non_stream')
        self.assertEqual(starts[1].fallback_of_turn_index, 1)
        self.assertEqual(sum(e.event_type == 'tool_started' for e in events), 1)
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM context_manifests WHERE run_id=?',
                                        (run.run_id,)).fetchone()[0], 2)
        reopened = WorkspaceStore.open(self.temp.name)
        self.assertEqual(reopened.get_run_snapshot(self.ws, self.mid, run.run_id), run)
        self.assertTrue(self.continue_message()[1])

    def test_interruption_falls_back_with_known_replacement_usage(self):
        provider, run = self.run_case([ProviderStreamInterruptedError(), answer()])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(run.status, 'partial')
        self.assertEqual(run.provider_receipts[1].error_code, 'provider_fallback_succeeded')
        self.assertTrue(self.continue_message()[1])

    def test_failed_fallback_never_third_request_and_stays_unresolved(self):
        provider, run = self.run_case([ProviderStreamInterruptedError(), ProviderStreamInterruptedError(), answer()])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(run.status, 'failed')
        with self.assertRaises(Path2StateError) as error:
            self.continue_message()
        self.assertEqual(error.exception.code, 'previous_outcome_unresolved')

    def test_content_protocol_timeout_permission_and_unknown_stages_never_fallback(self):
        failures = [ProviderContextBudgetError(stage=stage) for stage in
                    ('request_body', 'sse_event', 'stream_content', 'agent_public_output', 'ipc_result', None)]
        failures += [ProviderProtocolError(), ProviderTimeoutUnknownError(),
                     ProviderError('source_permission_denied', 'blocked')]
        for failure in failures:
            with self.subTest(code=failure.code, stage=getattr(failure, 'stage', None)):
                # Each independent failure gets its own Mission, without erasing prior evidence.
                self.ws, self.mission = _mission(self.store)
                self.mid = self.mission.mission_id
                provider, run = self.run_case([failure, answer()])
                self.assertEqual(len(provider.calls), 1)
                self.assertNotEqual(run.status, 'partial')

    def test_cancel_during_stream_error_never_fallback(self):
        event = Event()
        provider, run = self.run_case([ProviderStreamInterruptedError(), answer()], event)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(run.status, 'cancelled')

    def test_missing_usage_success_can_continue_without_zero_claim(self):
        provider, run = self.run_case([answer(False)])
        self.assertEqual(run.status, 'partial')
        self.assertEqual(run.provider_receipts[0].usage_status, 'missing')
        self.assertIsNone(run.provider_receipts[0].input_tokens)
        self.assertTrue(self.continue_message()[1])

    def test_eighth_request_has_no_fallback_capacity_and_tools_execute_once(self):
        reads = [ProviderCompletion(f'c-{i}', '', None,
            (ProviderToolCall(f'list-{i}', 'list_sources', '{}'),), 'tool_calls', ProviderUsage(1, 1))
            for i in range(7)]
        provider, run = self.run_case([*reads, ProviderStreamInterruptedError(), answer()])
        self.assertEqual(len(provider.calls), 8)
        self.assertEqual(len(run.provider_receipts), 8)
        events = self.store.list_run_events(self.ws, self.mid, run.run_id)
        self.assertEqual(sum(e.event_type == 'tool_started' for e in events), 7)
        self.assertEqual(run.error_code, 'provider_stream_interrupted')

    def test_unlinked_fallback_receipt_does_not_unlock_continuation(self):
        _, run = self.run_case([ProviderStreamInterruptedError(), answer()])
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            db.execute("UPDATE run_events SET public_payload_json=? WHERE run_id=? AND event_type='model_started' AND json_extract(public_payload_json,'$.turn_index')=2",
                       (json.dumps({'turn_index': 2}, separators=(',', ':')), run.run_id))
        with self.assertRaises(Path2StateError) as error:
            self.continue_message()
        self.assertEqual(error.exception.code, 'previous_outcome_unresolved')

    def test_known_usage_survives_fallback_public_output_overflow(self):
        provider, run = self.run_case([ProviderStreamInterruptedError(),
                                     replace(answer(), content='x' * 262145)])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(run.error_code, 'context_budget_exceeded')
        self.assertEqual(run.provider_receipts[1].input_tokens, 2)
        self.assertEqual(run.provider_receipts[1].usage_status, 'known')
        self.assertIsNone(run.final_output)

    def test_fallback_rechecks_source_permission_before_resending(self):
        original = self.store.get_context_snapshot
        reads = []

        def permission_revoked(*args):
            reads.append(args)
            if len(reads) == 2:
                raise Path2StateError('source_permission_denied')
            return original(*args)

        with patch.object(self.store, 'get_context_snapshot', side_effect=permission_revoked):
            provider, run = self.run_case([ProviderStreamInterruptedError(), answer()])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(run.error_code, 'source_permission_denied')

    def test_no_fallback_after_run_time_expires(self):
        with patch.object(agent, '_remaining_run_ms', side_effect=[100, 100, 100, 0]):
            provider, run = self.run_case([ProviderStreamInterruptedError(), answer()])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(run.error_code, 'provider_stream_interrupted')

    def test_openapi_includes_readonly_derived_usage_status(self):
        from contextox.api import create_app
        schema = create_app(data_dir=Path(self.temp.name)).openapi()
        receipt = schema['components']['schemas']['ProviderReceipt']
        self.assertIn('usage_status', receipt['required'])
        self.assertEqual(receipt['properties']['usage_status']['enum'], ['known', 'missing'])
        self.assertTrue(receipt['properties']['usage_status']['readOnly'])

    def test_legacy_usage_unknown_receipt_remains_blocked(self):
        provider, run = self.run_case([ProviderError('provider_usage_unknown', 'blocked')])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(run.provider_receipts[0].status, 'blocked')
        self.assertIsNone(run.provider_receipts[0].input_tokens)
        with self.assertRaises(Path2StateError) as error:
            self.continue_message()
        self.assertEqual(error.exception.code, 'previous_outcome_unresolved')
