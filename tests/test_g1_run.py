"""Public G1 seam: real Store, provider reads only supplied messages and schemas."""
import json
import sqlite3
import unittest
from contextlib import closing
from threading import Event
from unittest.mock import patch

import test_agent as fixtures
from test_model_tools import unknown_field
from contextox import agent
from contextox.provider import ProviderToolCall


class ScriptProvider(fixtures.FakeProvider):
    def __init__(self, script):
        super().__init__([])
        self.script = script

    def complete(self, messages, **kwargs):
        copied = json.loads(json.dumps(messages))
        self.calls.append({"messages": copied, "kwargs": kwargs})
        packet = json.loads(copied[1]["content"])
        calls = self.script(len(self.calls), packet, copied)
        return fixtures._completion("Synthetic candidate; business approval pending.", tuple(calls))


def call(identifier, name, args):
    return ProviderToolCall(identifier, name, json.dumps(args) if not isinstance(args, str) else args)


def finish(identifier="finish"):
    return call(identifier, "finish_run", {"outcome": "partial", "reason": "Needs business evidence.",
                                           "evidence_handles": []})


def update(packet, key="candidate", identifier="update"):
    return call(identifier, "update_definition_draft", {"draft_token": packet["draft_token"],
                "fields": [unknown_field(key)], "relationships": [], "unresolved_items": ["Owner needed"]})


class G1RunTests(unittest.TestCase):
    def execute(self, script, verify, with_sources=False):
        with fixtures.PersistedRunTests().store_case(with_sources=with_sources) as (store, ws, mission, refs):
            run = store.start_run(ws, mission.mission_id, fixtures._start_request(mission, refs))
            provider = ScriptProvider(script)
            with patch.object(agent, "get_provider", return_value=provider):
                agent.run_agent(store, ws, mission.mission_id, run.run_id, Event())
            result = store.get_run_snapshot(ws, mission.mission_id, run.run_id)
            with closing(sqlite3.connect(store.db_path)) as conn:
                receipts = conn.execute("SELECT call_id, ordinal FROM tool_receipts WHERE run_id=? ORDER BY ordinal",
                                        (run.run_id,)).fetchall()
                events = conn.execute("SELECT event_type, public_payload_json FROM run_events WHERE run_id=?",
                                      (run.run_id,)).fetchall()
                rejected = {json.loads(payload)["call_id"] for kind, payload in events
                            if kind == "tool_failed" and json.loads(payload).get("error_code") in
                            {"tool_arguments_invalid_no_effect", "batch_rejected_no_effect"}}
                self.assertFalse(rejected & {identifier for identifier, _ in receipts})
                self.assertFalse(rejected & {json.loads(payload)["call_id"] for kind, payload in events
                                            if kind in {"tool_started", "tool_completed"}})
            verify(result, provider.calls, receipts)

    def test_rejected_batch_has_zero_effect_then_corrects_and_preserves_upserts(self):
        def script(turn, packet, history):
            if turn == 1:
                return [update(packet, "must-not-exist", "rejected-update"), call("bad", "list_sources", "{")]
            if turn == 2:
                self.assertIsNone(packet["draft"])
                errors = [json.loads(m["content"])["error"] for m in history if m["role"] == "tool"]
                self.assertEqual([e["effect"] for e in errors], ["none", "none"])
                return [update(packet, "first", "first")]
            if turn == 3:
                self.assertEqual(len(packet["draft"]["fields"]), 1)
                return [update(packet, "second", "second")]
            return [finish()]
        def verify(result, calls, receipts):
            self.assertEqual(result.status, "partial")
            self.assertEqual({f.field_key for f in result.draft.fields}, {"first", "second"})
            self.assertEqual(receipts, [("first", 1), ("second", 2), ("finish", 3)])
            for request in calls:
                self.assertEqual(sum(m["role"] == "user" for m in request["messages"]), 1)
                for message in request["messages"]:
                    if message["role"] == "assistant":
                        self.assertEqual(message["reasoning_content"], "hidden reasoning")
            self.assertEqual(len(calls), 4)
        self.execute(script, verify)

    def test_three_rejections_stop_without_tool_receipts(self):
        self.execute(lambda n, p, h: [call(f"bad-{n}", "list_sources", {"extra": True})],
                     lambda r, c, receipts: (self.assertEqual(r.error_code, "tool_arguments_invalid"),
                                              self.assertEqual(len(c), 3), self.assertEqual(receipts, [])))

    def test_rejected_call_id_cannot_be_reused(self):
        self.execute(lambda n, p, h: [call("same", "list_sources", {"extra": True} if n == 1 else {})],
                     lambda r, c, receipts: (self.assertEqual(r.error_code, "tool_arguments_invalid"),
                                              self.assertEqual(len(c), 2), self.assertEqual(receipts, [])))

    def test_rejected_proposals_count_towards_budget(self):
        def script(n, p, h):
            return [call(f"{n}-{i}", "list_sources", {"extra": True} if i == 0 else {}) for i in range(13)]
        self.execute(script, lambda r, c, receipts: (
            self.assertEqual(r.error_code, "tool_call_budget_exceeded"),
            self.assertEqual(len(c), 2), self.assertEqual(receipts, [])))

    def test_stale_token_does_not_upgrade_and_can_be_corrected(self):
        tokens = []
        def script(n, p, h):
            if n == 1:
                tokens.append(p["draft_token"])
                return [update(p, "first", "first")]
            if n == 2:
                return [update({"draft_token": tokens[0]}, "forbidden", "stale")]
            if n == 3:
                self.assertEqual([f["field_key"] for f in p["draft"]["fields"]], ["first"])
                return [update(p, "second", "second")]
            return [finish()]
        self.execute(script, lambda r, c, receipts: (
            self.assertEqual(r.status, "partial"),
            self.assertEqual({f.field_key for f in r.draft.fields}, {"first", "second"}),
            self.assertEqual(receipts, [("first", 1), ("second", 2), ("finish", 3)])))

    def test_unknown_handle_fails_closed(self):
        self.execute(lambda n, p, h: [call("read", "read_source", {"source_handle": "fabricated",
                     "locator": {"kind": "csv_rows", "row_start": 1, "row_end": 1, "column": None}})],
                     lambda r, c, receipts: (self.assertEqual(r.error_code, "source_permission_denied"),
                                              self.assertEqual(receipts, [])), True)

    def test_explicit_join_columns_and_read_coverage_from_public_catalog(self):
        def script(n, p, h):
            left, right = [s["tables"][0] for s in p["sources"]]
            if n == 1:
                self.assertEqual([message["role"] for message in h], ["system", "user"])
                return [call("read", "read_source", {"source_handle": p["sources"][0]["source_handle"],
                    "locator": {"kind": "csv_rows", "row_start": 1,
                                "row_end": left["row_count"], "column": None}})]
            if n == 2:
                self.assertEqual(p["coverage"][0]["status"], "read")
                self.assertFalse(p["coverage"][0]["truncated"])
                self.assertEqual([message["role"] for message in h], ["system", "user"])
                self.assertEqual([item["tool_name"] for item in p["evidence_bundle"]], ["read_source"])
                self.assertTrue(p["evidence_bundle"][0]["result"]["text"])
                return [call("join", "inspect_dataset", {"kind": "relationship",
                    "left_table_handle": left["table_handle"], "right_table_handle": right["table_handle"],
                    "left_column_handles": [left["columns"][0]["column_handle"]],
                    "right_column_handles": [right["columns"][0]["column_handle"]]})]
            self.assertEqual([message["role"] for message in h], ["system", "user"])
            self.assertEqual([item["tool_name"] for item in p["evidence_bundle"]],
                             ["read_source", "inspect_dataset"])
            return [finish()]
        self.execute(script, lambda r, c, receipts: (
            self.assertEqual(r.status, "partial"),
            self.assertEqual(receipts, [("read", 1), ("join", 2), ("finish", 3)])), True)

    def test_repeated_evidence_is_deduplicated_across_fresh_provider_sessions(self):
        def script(n, p, h):
            source = p["sources"][0]
            table = source["tables"][0]
            if n <= 2:
                self.assertEqual([message["role"] for message in h], ["system", "user"])
                self.assertEqual(len(p["evidence_bundle"]), n - 1)
                return [call(f"read-{n}", "read_source", {"source_handle": source["source_handle"],
                    "locator": {"kind": "csv_rows", "row_start": 1,
                                "row_end": table["row_count"], "column": None}})]
            self.assertEqual([message["role"] for message in h], ["system", "user"])
            self.assertEqual(len(p["evidence_bundle"]), 1)
            return [finish()]
        self.execute(script, lambda r, c, receipts: (
            self.assertEqual(r.status, "partial"),
            self.assertEqual(receipts, [("read-1", 1), ("read-2", 2), ("finish", 3)])), True)

    def test_evidence_bundle_overflow_blocks_after_the_read_receipt(self):
        original_adapter = agent.ToolAdapter

        class TinyBundleAdapter(original_adapter):
            def __init__(self, snapshot, store):
                super().__init__(snapshot, store)
                self._evidence_bundle_max_bytes = 1

        def script(n, p, h):
            source = p["sources"][0]
            table = source["tables"][0]
            return [call("read", "read_source", {"source_handle": source["source_handle"],
                "locator": {"kind": "csv_rows", "row_start": 1,
                            "row_end": table["row_count"], "column": None}})]

        with patch.object(agent, "ToolAdapter", TinyBundleAdapter):
            self.execute(script, lambda r, c, receipts: (
                self.assertEqual(r.status, "blocked"),
                self.assertEqual(r.error_code, "context_budget_exceeded"),
                self.assertEqual(len(c), 1),
                self.assertEqual(receipts, [("read", 1)])), True)

    def test_review_and_clarification_use_published_draft_token(self):
        for terminal in ("submit_for_review", "create_clarification"):
            with self.subTest(terminal=terminal):
                def script(n, p, h):
                    if n == 1:
                        return [call("list", "list_sources", {})]
                    if n == 2:
                        return [update(p)]
                    args = {"draft_token": p["draft_token"]}
                    if terminal == "create_clarification":
                        args["questions"] = [{"covers_obligation_handles": [o["obligation_handle"] for o in p["draft"]["clarification_obligations"]],
                            "question": "Please provide decisions for each listed unknown dimension.",
                            "why_needed": "Source evidence does not define it.", "expected_answer_type": "text",
                            "suggested_owner_role": "Business owner", "related_definition_paths": [],
                            "evidence_requested": ["Approved definitions for the listed dimensions"], "examples_or_options": [], "blocking_impact": "blocking",
                            "evidence_handles": []}]
                    return [call("terminal", terminal, args)]
                self.execute(script, lambda r, c, receipts: (
                    self.assertEqual(r.status, "waiting_for_human"),
                    self.assertEqual(r.terminal_receipt.terminal_tool, terminal),
                    self.assertEqual(receipts, [("list", 1), ("update", 2), ("terminal", 3)])))

    def test_wrong_table_column_is_recoverable_without_execution(self):
        def script(n, p, h):
            if n > 1:
                return [finish()]
            left, right = [s["tables"][0] for s in p["sources"]]
            return [call("bad-join", "inspect_dataset", {"kind": "relationship",
                "left_table_handle": left["table_handle"], "right_table_handle": right["table_handle"],
                "left_column_handles": [right["columns"][0]["column_handle"]],
                "right_column_handles": [right["columns"][0]["column_handle"]]})]
        self.execute(script, lambda r, c, receipts: (
            self.assertEqual(r.status, "partial"), self.assertEqual(receipts, [("finish", 1)])), True)

    def test_failed_read_coverage_is_distinct_and_uses_shared_recovery_limit(self):
        def script(n, p, h):
            if n > 1:
                self.assertEqual(p["coverage"][0]["status"], "failed")
            return [call(f"read-{n}", "read_source", {"source_handle": p["sources"][0]["source_handle"],
                "locator": {"kind": "csv_rows", "row_start": 1, "row_end": 20, "column": None}})]
        self.execute(script, lambda r, c, receipts: (
            self.assertEqual(r.error_code, "tool_recovery_budget_exceeded"),
            self.assertEqual(len(c), 3), self.assertEqual(len(receipts), 3)), True)

    def test_store_cas_race_is_revalidated_without_executing_stale_terminal(self):
        original = fixtures.WorkspaceStore.validate_run_tool_batch
        injected = []
        def validate(store, ws, mission, run, calls):
            if calls[0].name == "submit_for_review" and not injected:
                # Fixture-only concurrent committed revision; Provider has no Store access.
                with closing(sqlite3.connect(store.db_path)) as conn, conn:
                    conn.execute("INSERT INTO definition_drafts SELECT workspace_id, mission_id, draft_id, "
                                 "version+1, sha256, status, semantic_approval, fields_json, relationships_json, "
                                 "unresolved_items_json FROM definition_drafts WHERE workspace_id=? AND mission_id=? "
                                 "ORDER BY version DESC LIMIT 1", (ws, mission))
                injected.append(True)
            return original(store, ws, mission, run, calls)
        def script(n, p, h):
            if n == 1:
                return [update(p)]
            return [call(f"review-{n}", "submit_for_review", {"draft_token": p["draft_token"]})]
        with patch.object(fixtures.WorkspaceStore, "validate_run_tool_batch", validate):
            self.execute(script, lambda r, c, receipts: (
                self.assertEqual(r.status, "waiting_for_human"), self.assertEqual(r.draft.version, 2),
                self.assertEqual(len(c), 3), self.assertEqual(receipts, [("update", 1), ("review-3", 2)])))
