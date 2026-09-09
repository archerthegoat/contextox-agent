import json
import tempfile
import unittest
from threading import Event
from uuid import uuid4

import test_sources as source_fixtures

from contextox.profile_interpretation import (
    PROFILE_INTERPRETATION_P0_SHA256,
    PROFILE_CHUNK_MAX_BYTES,
    ProfileInterpretationFailure,
    interpret_profile,
    profile_provider_config,
    profile_chunks,
)
from contextox.models import ProfileInterpretationCreateRequest
from contextox.provider import DeepSeekProvider, ProviderCompletion, ProviderUsage
from contextox.sources import build_profile_pack
from contextox.store import WorkspaceStore, WorkspaceStoreError


class FakeInterpretationProvider:
    config = profile_provider_config()

    def __init__(self):
        self.calls = []

    @staticmethod
    def opaque_user_id(workspace_id):
        return workspace_id

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        payload = json.loads(messages[1]["content"])
        columns = [{
            "table_id": item["table_id"],
            "column_name": item["column"]["name"],
            "category": "numeric" if "integer" in item["column"]["observed_types"] else "text",
            "business_meaning_candidate": None,
            "anomalies": [],
            "unknown_items": ["Business meaning requires owner confirmation."],
        } for item in payload["items"]]
        return ProviderCompletion(
            completion_id=f"profile-{len(self.calls)}",
            content=json.dumps({
                "columns": columns,
                "relationship_hints": [],
                "unknown_items": [],
            }),
            reasoning_content="",
            tool_calls=(),
            finish_reason="stop",
            usage=ProviderUsage(input_tokens=20, output_tokens=10),
        )


class ProfileInterpretationTests(unittest.TestCase):
    def _pack(self):
        content = b"id,status\n1,open\n2,closed\n3,open\n"
        revision = source_fixtures._revision(content, "text/csv")
        return build_profile_pack(revision, content)

    def test_disabled_thinking_payload_omits_reasoning_effort(self):
        provider = DeepSeekProvider(thinking="disabled", reasoning_effort=None, transport=object())
        payload = provider.build_payload(
            [{"role": "user", "content": "profile"}],
            stream=False,
            tools=None,
            max_tokens=4096,
            user_id="ws-test",
        )
        self.assertEqual(provider.config.thinking, "disabled")
        self.assertIsNone(provider.config.reasoning_effort)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("tools", payload)

    def test_profile_interpretation_is_bounded_and_uses_no_tools(self):
        pack = self._pack()
        provider = FakeInterpretationProvider()

        result = interpret_profile(
            provider, pack, user_id="ws-test", cancel_event=Event()
        )

        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result.interpretation.profile_hash, pack.profile_hash)
        self.assertFalse(result.interpretation.partial)
        self.assertEqual(len(result.interpretation.columns), 2)
        self.assertEqual((result.input_tokens, result.output_tokens), (20, 10))
        request = provider.calls[0]
        self.assertFalse(request["kwargs"]["stream"])
        self.assertIsNone(request["kwargs"]["tools"])
        self.assertLessEqual(result.sent_bytes, PROFILE_CHUNK_MAX_BYTES)
        self.assertNotIn("sample_rows", request["messages"][1]["content"])
        self.assertTrue(all(
            len(json.dumps([
                {"role": "system", "content": request["messages"][0]["content"]},
                {"role": "user", "content": json.dumps(chunk, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"))},
            ], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
            <= PROFILE_CHUNK_MAX_BYTES
            for chunk in profile_chunks(pack)
        ))

    def test_profile_interpretation_rejects_incomplete_column_output(self):
        class IncompleteProvider(FakeInterpretationProvider):
            def complete(self, messages, **kwargs):
                completion = super().complete(messages, **kwargs)
                return completion.__class__(
                    **{
                        **completion.__dict__,
                        "content": json.dumps({
                            "columns": [],
                            "relationship_hints": [],
                            "unknown_items": [],
                        }),
                    }
                )

        with self.assertRaisesRegex(
            ProfileInterpretationFailure, "profile_interpretation_invalid"
        ):
            interpret_profile(
                IncompleteProvider(), self._pack(),
                user_id="ws-test", cancel_event=Event(),
            )

    def test_relationship_hints_are_limited_to_supplied_deterministic_stats(self):
        content = (
            b'{"orders":[{"customer_id":"c1"},{"customer_id":"c2"}],'
            b'"customers":[{"customer_id":"c1"},{"customer_id":"c3"}]}'
        )
        pack = build_profile_pack(
            source_fixtures._revision(content, "application/json"), content
        )

        class RelationshipProvider(FakeInterpretationProvider):
            def complete(self, messages, **kwargs):
                completion = super().complete(messages, **kwargs)
                payload = json.loads(messages[1]["content"])
                hints = [{
                    "left_table_id": item["left_table_id"],
                    "right_table_id": item["right_table_id"],
                    "left_columns": item["left_columns"],
                    "right_columns": item["right_columns"],
                    "reason": "Exact profile statistics show a prospective match.",
                } for item in payload["relationships"]]
                body = json.loads(completion.content)
                body["relationship_hints"] = hints
                return completion.__class__(**{
                    **completion.__dict__, "content": json.dumps(body)
                })

        provider = RelationshipProvider()
        result = interpret_profile(
            provider, pack, user_id="ws-test", cancel_event=Event()
        )

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(result.interpretation.relationship_hints), 1)
        relationship_payload = provider.calls[1]["messages"][1]["content"]
        self.assertNotIn(pack.source_ref.revision_id, relationship_payload)
        self.assertNotIn("source_refs", relationship_payload)
        self.assertNotIn("sample_rows", relationship_payload)

    def test_profile_interpretation_marks_four_chunk_cap_partial(self):
        names = [f"column_{index}" for index in range(100)]
        rows = [
            ",".join([f"value_{row}_" + ("x" * 220) for _ in names])
            for row in range(10)
        ]
        content = (",".join(names) + "\n" + "\n".join(rows) + "\n").encode()
        revision = source_fixtures._revision(content, "text/csv")
        pack = build_profile_pack(revision, content)
        self.assertGreater(len(profile_chunks(pack)), 4)
        provider = FakeInterpretationProvider()

        result = interpret_profile(
            provider, pack, user_id="ws-test", cancel_event=Event()
        )

        self.assertTrue(result.interpretation.partial)
        self.assertEqual(result.interpretation.covered_chunks, 4)
        self.assertGreater(result.interpretation.total_chunks, 4)
        self.assertEqual(len(provider.calls), 4)

    def test_succeeded_interpretation_is_cached_by_profile_and_config(self):
        with tempfile.TemporaryDirectory(
            prefix="contextox-profile-cache-", dir="/private/tmp"
        ) as directory:
            store = WorkspaceStore.open(directory)
            workspace_id = store.create_workspace("Profile cache").workspace_id
            revision, _ = store.import_source_revision(
                workspace_id, "input.csv", "text/csv",
                b"id,status\n1,open\n2,closed\n3,open\n",
            )
            first_request = ProfileInterpretationCreateRequest(
                client_request_id=str(uuid4()), provider_send_confirmed=True
            )
            first, created = store.create_profile_interpretation_attempt(
                workspace_id, revision.revision_id, first_request,
                profile_provider_config(), PROFILE_INTERPRETATION_P0_SHA256,
            )
            self.assertTrue(created)
            self.assertEqual(first.status, "queued")
            store.mark_profile_interpretation_running(
                workspace_id, revision.revision_id, first.attempt_id
            )
            result = interpret_profile(
                FakeInterpretationProvider(),
                store.get_source_profile(workspace_id, revision.revision_id),
                user_id=workspace_id,
                cancel_event=Event(),
            )
            first = store.save_profile_interpretation_result(
                workspace_id, revision.revision_id, first.attempt_id,
                result.interpretation, sent_bytes=result.sent_bytes,
                chunk_count=result.chunk_count, input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

            second, second_created = store.create_profile_interpretation_attempt(
                workspace_id,
                revision.revision_id,
                ProfileInterpretationCreateRequest(
                    client_request_id=str(uuid4()), provider_send_confirmed=True
                ),
                profile_provider_config(),
                PROFILE_INTERPRETATION_P0_SHA256,
            )
            self.assertTrue(second_created)
            self.assertEqual(second.status, "succeeded")
            self.assertTrue(second.cache_hit)
            self.assertEqual(second.cached_from_attempt_id, first.attempt_id)
            self.assertEqual(second.interpretation, first.interpretation)
            self.assertEqual((second.sent_bytes, second.chunk_count), (0, 0))
            replay, replay_created = store.create_profile_interpretation_attempt(
                workspace_id, revision.revision_id, first_request,
                profile_provider_config(), PROFILE_INTERPRETATION_P0_SHA256,
            )
            self.assertFalse(replay_created)
            self.assertEqual(replay, first)

            other = store.create_workspace("Other").workspace_id
            with self.assertRaises(WorkspaceStoreError):
                store.get_profile_interpretation_attempt(
                    other, revision.revision_id, first.attempt_id
                )


if __name__ == "__main__":
    unittest.main()
