import json
import sqlite3
import unittest
from unittest.mock import patch
from threading import Event
import test_agent as t
from contextox import agent
from contextox.store import WorkspaceStore
from contextox.provider import ProviderToolCall

class FieldContractFailureTests(unittest.TestCase):

    def test_field_validation_preserves_prior_relationship_and_known_usage(self):
        for variant in ('missing_unknown', 'extra_unknown', 'duplicate_unknown', 'observed_no_evidence', 'valid_unknown'):
            with self.subTest(variant=variant), t.PersistedRunTests().store_case(with_sources=True) as (store, ws, mission, refs):
                run = store.start_run(ws, mission.mission_id, t._start_request(mission, refs))
                dims = ('meaning', 'value_type', 'grain', 'rule', 'time_basis', 'null_handling')
                relationship = {'relationship_key': 'synthetic_join', 'left': {'source_ref': refs[0].model_dump(mode='json'), 'table_id': store.get_source_artifact(ws, refs[0].revision_id).tables[0].table_id, 'columns': ['id']}, 'right': {'source_ref': refs[1].model_dump(mode='json'), 'table_id': store.get_source_artifact(ws, refs[1].revision_id).tables[0].table_id, 'columns': ['id']}, 'observed_cardinality': 'unknown', 'join_rule': None, 'grain_notes': None, 'evidence_status': 'unknown', 'source_refs': [], 'risks': [], 'unknowns': []}

                class P(t.FakeProvider):

                    def complete(self, messages, **kwargs):
                        n = len(self.calls) + 1
                        draft = store.get_run_snapshot(ws, mission.mission_id, run.run_id).draft
                        if n == 3:
                            name = 'finish_run'
                            args = {'outcome': 'partial', 'reason': 'Synthetic public result.', 'source_refs': []}
                        else:
                            name = 'update_definition_draft'
                            field = {'field_key': 'synthetic_field', 'name': 'synthetic_field', **{k: None for k in dims}, 'source_columns': [], 'evidence_status': 'unknown', 'source_refs': [], 'unknowns': [{'property_path': k, 'reason': 'Synthetic unknown.'} for k in dims]}
                            if variant == 'missing_unknown':
                                field['unknowns'].pop()
                            elif variant == 'extra_unknown':
                                field['unknowns'].append({'property_path': 'other', 'reason': 'Synthetic unknown.'})
                            elif variant == 'duplicate_unknown':
                                field['unknowns'].append(field['unknowns'][0].copy())
                            elif variant == 'observed_no_evidence':
                                field.update({k: 'Supplied synthetic value' for k in dims})
                                field.update(evidence_status='observed', unknowns=[])
                            args = {'expected_version': draft.version if draft else 0, 'expected_sha256': draft.sha256 if draft else None, 'fields': [field] if n == 2 else [], 'relationships': [relationship] if n == 1 else [], 'unresolved_items': ['Synthetic unknown remains.']}
                        self.completions = [t._completion('Synthetic output.', (ProviderToolCall('step-' + str(n), name, json.dumps(args)),))]
                        return super().complete(messages, **kwargs)
                provider = P([])
                with patch.object(agent, 'get_provider', return_value=provider):
                    agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
                final = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
                valid = variant == 'valid_unknown'
                assert final.status == ('partial' if valid else 'failed')
                assert final.error_code == (None if valid else 'tool_arguments_invalid')
                assert final.draft.version == (2 if valid else 1) and len(final.draft.relationships) == 1 and (len(final.draft.fields) == int(valid))
                assert len(provider.calls) == (3 if valid else 2)
                assert bool(final.terminal_receipt) == valid and bool(final.final_output) == valid
                assert WorkspaceStore.open(store.data_dir).get_run_snapshot(ws, mission.mission_id, run.run_id) == final
                with sqlite3.connect(store.db_path) as c:
                    receipts = c.execute('select status,input_tokens,output_tokens from provider_receipts where run_id=?', (run.run_id,)).fetchall()
                    assert len(receipts) == len(provider.calls) and all((s == 'succeeded' and i == 9 and (o == 5) for s, i, o in receipts))
