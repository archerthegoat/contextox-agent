"""Draft scope delivery and historical receipt compatibility regressions."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from threading import Event
from unittest.mock import patch
from contextox import agent
from contextox.store import WorkspaceStore, Path2StateError, WorkspaceStoreUnavailableError
from contextox.provider import ProviderCompletion
from contextox.models import SourceIdentity
from test_agent import FakeProvider, _usage

OLD = 'e588954b3d0d260327ef1ca503a1d4cba9f9044e60ec48d4055985ba5b1310ea'
NEW = '8bdff48c69e1367b83be5e57ed25caf0a3e73b8c3d08252757bc9077d5641ce3'

class DraftScopeTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(agent.P0_DRAFT_SHA256, NEW)
        self.temp = tempfile.TemporaryDirectory(prefix='contextox-draft-scope-check-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore.open(self.temp.name)
        self.ws = self.store.create_workspace('Synthetic draft scope').workspace_id
        self.candidate = agent.MissionDraftPayload(title='Synthetic task', goal='Describe fields',
            completion_criteria=['Return requested candidate fields'], scope_notes=[])

    def attempt_receipt(self, status, prompt_hash):
        attempt = self.store.create_mission_draft_attempt(self.ws, 'Describe synthetic fields.')
        self.store.mark_mission_draft_running(self.ws, attempt.attempt_id)
        receipt = agent._make_receipt(provider=FakeProvider([]), workspace_id=self.ws,
            attempt_id=attempt.attempt_id, mission_id=None, run_id=None, turn_index=1,
            status=status, p0_sha256=prompt_hash, usage=_usage() if status == 'succeeded' else None,
            error_code=None if status == 'succeeded' else 'provider_timeout_unknown')
        return attempt, receipt

    def save(self, attempt, receipt):
        if receipt.status == 'succeeded':
            return self.store.save_mission_draft_result(self.ws, attempt.attempt_id, self.candidate, receipt)
        return self.store.fail_mission_draft_attempt(self.ws, attempt.attempt_id,
            'failed', 'provider_timeout_unknown', receipt)

    def test_legacy_success_and_failure_replay_exactly_and_tampering_is_rejected(self):
        for status in ('succeeded', 'failed'):
            with self.subTest(status=status):
                attempt, receipt = self.attempt_receipt(status, OLD)
                with patch.object(agent, 'P0_DRAFT_SHA256', OLD):
                    original = self.save(attempt, receipt)
                self.store = WorkspaceStore.open(self.temp.name)
                self.assertEqual(self.store.get_mission_draft_attempt(self.ws, attempt.attempt_id), original)
                self.assertEqual(self.save(attempt, receipt), original)
                if status == 'succeeded':
                    revision, _ = self.store.import_source_revision(self.ws, 'synthetic.csv', 'text/csv', b'id\n01\n')
                    identity = SourceIdentity.model_validate(revision.model_dump(include=set(SourceIdentity.model_fields)))
                    mission = self.store.confirm_mission_draft_attempt(self.ws, attempt.attempt_id,
                        original.candidate_version, original.candidate_sha256, [identity])
                    confirmed = self.store.get_mission_draft_attempt(self.ws, attempt.attempt_id)
                    self.assertEqual(confirmed.status, 'confirmed')
                    self.assertEqual(self.save(attempt, receipt), confirmed)
                    self.assertEqual(self.store.confirm_mission_draft_attempt(self.ws, attempt.attempt_id,
                        original.candidate_version, original.candidate_sha256, [identity]), mission)
                for bad in (receipt.model_copy(update={'p0_sha256': '0' * 64}),
                            receipt.model_copy(update={'error_code': 'changed_error'})):
                    with self.assertRaises(Path2StateError):
                        self.save(attempt, bad)
                if status == 'succeeded':
                    with self.assertRaises(Path2StateError):
                        self.store.save_mission_draft_result(self.ws, attempt.attempt_id,
                            self.candidate.model_copy(update={'goal': 'Changed scope'}), receipt)

    def test_new_write_rejects_legacy_hash_and_accepts_current_hash(self):
        for status in ('succeeded', 'failed'):
            with self.subTest(status=status):
                attempt, old_receipt = self.attempt_receipt(status, OLD)
                with self.assertRaises(Path2StateError) as error:
                    self.save(attempt, old_receipt)
                self.assertEqual(error.exception.code, 'provider_receipt_invalid')
                self.assertEqual(self.store.get_mission_draft_attempt(self.ws, attempt.attempt_id).status, 'running')
                ready = self.save(attempt, old_receipt.model_copy(update={'p0_sha256': NEW}))
                self.assertEqual(ready.status, 'ready' if status == 'succeeded' else 'failed')

    def test_unknown_historical_hash_remains_unreadable(self):
        attempt, receipt = self.attempt_receipt('succeeded', NEW)
        self.save(attempt, receipt)
        with closing(sqlite3.connect(self.store.db_path)) as db:
            with db:
                db.execute('UPDATE provider_receipts SET p0_sha256=? WHERE receipt_id=?', ('0' * 64, receipt.receipt_id))
        with self.assertRaises(WorkspaceStoreUnavailableError):
            self.store.get_mission_draft_attempt(self.ws, attempt.attempt_id)

    def test_prompt_is_delivered_with_original_numeric_request_and_one_entry(self):
        text = 'List exactly 10 synthetic questions; do not choose business thresholds.'
        attempt = self.store.create_mission_draft_attempt(self.ws, text)
        payload = self.candidate.model_copy(update={'completion_criteria': ['List exactly 10 synthetic questions']})
        provider = FakeProvider([ProviderCompletion('synthetic', payload.model_dump_json(),
            None, [], 'stop', _usage())])
        with patch.object(agent, 'get_provider', return_value=provider):
            agent.generate_mission_draft(self.store, self.ws, attempt.attempt_id, Event())
            agent.generate_mission_draft(self.store, self.ws, attempt.attempt_id, Event())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]['messages'], [
            {'role': 'system', 'content': agent.P0_DRAFT}, {'role': 'user', 'content': text}])
        self.assertEqual(provider.calls[0]['kwargs']['max_tokens'], 4096)
        self.assertEqual(self.store.get_mission_draft_attempt(self.ws, attempt.attempt_id).candidate, payload)

if __name__ == '__main__':
    unittest.main()
