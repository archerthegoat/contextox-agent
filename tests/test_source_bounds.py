import json
import re
import unittest
from threading import Event
from unittest.mock import patch

import test_agent as fixtures
from contextox import agent
from contextox.models import ReadSourceCall
from contextox.provider import ProviderToolCall
from contextox.store import Path2StateError, WorkspaceStore


class SourceBoundsTests(unittest.TestCase):
    def test_bounds_are_selected_source_only_and_reads_remain_explicit(self):
        cases = [
            ('text/markdown', 'sample.md', b'# Synthetic\n\nText\n\nUnit: yuan\n\nEnd', 'text_lines', 7),
            ('text/plain', 'sample.txt', '甲\r\n乙\r\n'.encode(), 'text_lines', 2),
            ('text/csv', 'sample.csv', b'id\n1\n2\n', 'csv_rows', 2),
            ('text/csv', 'empty.csv', b'id\n', 'csv_rows', 0),
        ]
        helper = fixtures.PersistedAttemptTests()
        for media, name, content, kind, count in cases:
            with self.subTest(media=media, count=count), helper.store_case() as (store, ws, attempt):
                ready = helper.generate(store, ws, attempt)
                revision, _ = store.import_source_revision(ws, name, media, content)
                other, _ = store.import_source_revision(ws, 'unselected.txt', 'text/plain', b'private-marker')
                refs = [fixtures._source_identity_for_test(revision)]
                mission = store.confirm_mission_draft_attempt(ws, attempt.attempt_id, 1, ready.candidate_sha256, refs)
                run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
                store.mark_run_running(ws, mission.mission_id, run.run_id)
                scope = (ws, mission.mission_id, run.run_id)
                start_key, end_key = ('line_start', 'line_end') if kind == 'text_lines' else ('row_start', 'row_end')
                locator = {'kind': kind, start_key: 1, end_key: 50}
                if kind == 'csv_rows':
                    locator['column'] = None
                call = ReadSourceCall(call_id='oversized', name='read_source', arguments={
                    'revision_id': revision.revision_id, 'locator': locator,
                })
                rejected = store.execute_run_tool(*scope, call)
                self.assertEqual((rejected.status, rejected.output.code), ('rejected', 'locator_out_of_bounds'))
                self.assertIn(f'has {count} ', rejected.output.reason)
                self.assertNotIn('private-marker', rejected.output.reason)
                self.assertNotIn(other.revision_id, rejected.output.reason)
                self.assertEqual(rejected.tool_receipt.source_refs, [])
                self.assertIsNone(rejected.terminal_snapshot)
                if count:
                    self.assertIn(f'1..{count}', rejected.output.reason)
                    exact = call.model_copy(deep=True, update={'call_id': 'explicit-valid'})
                    setattr(exact.arguments.locator, end_key, count)
                    result = store.execute_run_tool(*scope, exact)
                    self.assertEqual(result.status, 'succeeded')
                    self.assertFalse(result.output.truncated)
                    self.assertEqual(getattr(result.output.source_ref.locator, end_key), count)
                else:
                    self.assertIn('no valid nonempty range', rejected.output.reason)
                foreign = call.model_copy(deep=True, update={'call_id': 'unselected'})
                foreign.arguments.revision_id = other.revision_id
                with self.assertRaises(Path2StateError) as caught:
                    store.execute_run_tool(*scope, foreign)
                self.assertEqual(caught.exception.code, 'source_permission_denied')

    def test_agent_can_use_returned_bounds_to_read_body(self):
        helper = fixtures.PersistedAttemptTests()
        with helper.store_case() as (store, ws, attempt):
            ready = helper.generate(store, ws, attempt)
            revision, _ = store.import_source_revision(
                ws, 'sample.md', 'text/markdown', b'# Synthetic\n\nExample\n\nUnit: yuan\n\nEnd')
            refs = [fixtures._source_identity_for_test(revision)]
            mission = store.confirm_mission_draft_attempt(ws, attempt.attempt_id, 1, ready.candidate_sha256, refs)
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            test = self

            class BoundsProvider(fixtures.FakeProvider):
                def complete(self, messages, **kwargs):
                    turn = len(self.calls) + 1
                    if turn == 1:
                        end = 50
                    elif turn == 2:
                        rejection = json.loads([m for m in messages if m['role'] == 'tool'][-1]['content'])
                        test.assertEqual(rejection['code'], 'locator_out_of_bounds')
                        end = int(re.search(r'1\.\.(\d+)', rejection['reason'])[1])
                    if turn <= 2:
                        name = 'read_source'
                        arguments = {'revision_id': revision.revision_id,
                                     'locator': {'kind': 'text_lines', 'line_start': 1, 'line_end': end}}
                    else:
                        excerpt = json.loads([m for m in messages if m['role'] == 'tool'][-1]['content'])
                        test.assertIn('Unit: yuan', excerpt['text'])
                        test.assertFalse(excerpt['truncated'])
                        name = 'finish_run'
                        arguments = {'outcome': 'partial', 'reason': 'Synthetic source states Unit: yuan.',
                                     'source_refs': [excerpt['source_ref']]}
                    self.completions = [fixtures._completion('Synthetic source states Unit: yuan.' if turn == 3 else '', (ProviderToolCall(
                        f'call-{turn}', name, json.dumps(arguments)),))]
                    return super().complete(messages, **kwargs)

            provider = BoundsProvider([])
            with patch.object(agent, 'get_provider', return_value=provider):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
            snapshot = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            self.assertEqual(snapshot.status, 'partial')
            self.assertEqual(len(provider.calls), 3)
            self.assertIn('Unit: yuan', snapshot.final_output)
            self.assertEqual(snapshot.terminal_receipt.source_refs[0].locator.line_end, 7)
            self.assertEqual(WorkspaceStore.open(store.data_dir).get_run_snapshot(
                ws, mission.mission_id, run.run_id), snapshot)
