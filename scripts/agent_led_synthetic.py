"""Synthetic browser fixture. No real Provider or Keychain access."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from threading import Event
from uuid import uuid4

import uvicorn

from contextox import agent, conversation_store, local_settings
from contextox.api import create_app
from contextox.models import (
    CreateClarificationCall,
    FinishRunCall,
    ProviderConfigSnapshot,
    SourceIdentity,
    SubmitForReviewCall,
    UpdateDefinitionDraftCall,
)
from contextox.provider import ProviderCompletion, ProviderError, ProviderUsage


_DIMENSIONS = ("meaning", "value_type", "grain", "rule", "time_basis", "null_handling")
_REFUND_QUESTION = "退款是否从订单金额中扣除？"
_MISSING_QUESTION = "缺失金额如何处理？"
_TIME_QUESTION = "本次统计采用什么时间范围？"
_TIME_BASIS = "使用当前两份资料的全部记录，不设日期筛选"


def _unknown_field(key: str = "amount") -> dict[str, object]:
    return {
        "field_key": key,
        "name": key,
        **{dimension: None for dimension in _DIMENSIONS},
        "source_columns": [],
        "evidence_status": "unknown",
        "source_refs": [],
        "unknowns": [
            {"property_path": dimension, "reason": "Requires business answer"}
            for dimension in _DIMENSIONS
        ],
    }


def _answer_target(question: dict[str, object]) -> list[dict[str, str]]:
    targets = []
    for path in question.get("related_definition_paths", []):
        if path in {"fields.amount.rule", "fields.amount.null_handling", "fields.amount.time_basis"}:
            targets.append({"kind": "field", "key": "amount", "property": path.rsplit(".", 1)[1]})
    return targets


class SyntheticProvider:
    config = ProviderConfigSnapshot(
        endpoint_id="deepseek_chat_completions",
        model="deepseek-v4-flash",
        thinking="disabled",
        reasoning_effort=None,
    )

    def complete(self, messages, **kwargs):
        context = json.loads(messages[1]["content"])
        item = context["input"]
        content = item["content"]
        for _ in range(10 if "慢一点" in content else 2):
            if kwargs["cancel_event"].wait(0.2):
                raise ProviderError("cancelled", "cancelled")

        output = {
            "public_reply": "【合成验收】可以围绕当前资料核对订单金额。请说明你想解决的问题；退款与缺失金额规则仍需业务确认。",
            "next_action": "discuss",
        }
        mission = context.get("mission")
        if mission:
            cases = mission["clarification_cases"]
            if any(
                question["question"] == _TIME_QUESTION
                for case in cases
                for question in case["request"]["questions"]
            ):
                output["public_reply"] = (
                    "【合成验收】当前候选已应用退款与缺失金额规则；两份资料没有时间字段，"
                    "因此统计时间范围仍需业务确认。"
                )
            else:
                output["public_reply"] = (
                    "【合成验收】退款规则会影响统计金额；请核对下方候选卡片。"
                    "未提供的回答来源与依据将保留为空。"
                )
            if "退款" in content and "扣除" in content:
                suggestions = []
                for case in cases:
                    for index, question in enumerate(case["request"]["questions"]):
                        if question["question"] not in {_REFUND_QUESTION, _MISSING_QUESTION}:
                            continue
                        suggestions.append(
                            {
                                "origin_run_id": case["request"]["run_id"],
                                "clarification_id": case["request"]["clarification_id"],
                                "request_sha256": case["request_sha256"],
                                "question_index": index,
                                "disposition": "answered",
                                "answer": "扣除已退款金额" if question["question"] == _REFUND_QUESTION else "缺失金额单独列出",
                                "respondent": "合成业务负责人" if "负责人" in content else None,
                                "basis": "本条合成验收回答" if "依据" in content else None,
                                "evidence_refs": [],
                                "targets": _answer_target(question),
                            }
                        )
                output["answer_suggestions"] = suggestions
            elif "全部记录" in content and "不设日期筛选" in content:
                suggestions = []
                for case in cases:
                    for index, question in enumerate(case["request"]["questions"]):
                        if question["question"] != _TIME_QUESTION:
                            continue
                        suggestions.append(
                            {
                                "origin_run_id": case["request"]["run_id"],
                                "clarification_id": case["request"]["clarification_id"],
                                "request_sha256": case["request_sha256"],
                                "question_index": index,
                                "disposition": "answered",
                                "answer": _TIME_BASIS,
                                "respondent": "合成业务负责人" if "负责人" in content else None,
                                "basis": "本条合成验收回答" if "依据" in content else None,
                                "evidence_refs": [],
                                "targets": _answer_target(question),
                            }
                        )
                output["answer_suggestions"] = suggestions
        elif conversation_store.is_explicit_work_instruction(content) and context["source_refs"]:
            output.update(
                public_reply="【合成验收】目标与资料范围已明确，开始整理候选。",
                next_action="start_task",
                title="按地区统计订单金额",
                goal={
                    "text": content,
                    "message_refs": [{"message_id": item["message_id"], "sha256": item["sha256"]}],
                },
            )
        elif any(phrase in content for phrase in ("怎么做", "如何开始", "下一步", "怎么开始")):
            output["public_reply"] = (
                "【合成验收】下一步只要说出一个具体结果，不需要创建 Mission、Provider 或另点 Run。"
                "例如：按地区统计订单金额，并把退款和缺失金额的处理规则写清楚。"
            )
        elif any(phrase in content for phrase in ("规范一下", "整理业务口径", "规范业务口径")):
            output["public_reply"] = (
                "【合成验收】可以。请再明确要整理的结果；当前案例可以直接选择“按地区统计订单金额”，"
                "之后我会把退款和缺失金额规则整理成确认卡片。"
            )
        elif any(phrase in content for phrase in ("理解本轮", "看看", "了解", "资料", "关联")):
            output["public_reply"] = (
                f"【合成验收】已读取本轮 {len(context['source_refs'])} 份资料：可以看到地区、订单金额和退款金额，"
                "两张表可先按 region 精确关联。退款、缺失金额和时间范围仍需要业务确认。"
            )
        return ProviderCompletion(
            "synthetic",
            json.dumps(output, ensure_ascii=False),
            "",
            (),
            "stop",
            ProviderUsage(20, 20),
        )


def _identity(revision) -> SourceIdentity:
    return SourceIdentity.model_validate(
        revision.model_dump(include=set(SourceIdentity.model_fields))
    )


def _selected_fixture_sources(store, workspace_id: str, selected: list[SourceIdentity]):
    revisions = {
        revision.original_name: revision
        for revision in store.list_source_revisions(workspace_id)
        if _identity(revision) in selected
    }
    if set(revisions) != {"订单.csv", "退款.csv"} or len(selected) != 2:
        raise ValueError("synthetic fixture requires the exact order and refund sources")
    return revisions["订单.csv"], revisions["退款.csv"]


def _table_id_with_columns(store, workspace_id: str, source, required: set[str]) -> str:
    artifact = store.get_source_artifact(workspace_id, source.revision_id)
    matches = [
        table.table_id
        for table in artifact.tables
        if required <= {column.name for column in table.columns}
    ]
    if len(matches) != 1:
        raise ValueError("synthetic fixture table shape changed")
    return matches[0]


def _approved_case(snapshot, questions: list[str]):
    matches = [
        approved
        for approved in snapshot.approved_answers
        if [question.question for question in approved.request.questions] == questions
    ]
    if len(matches) != 1:
        raise ValueError("synthetic continuation requires the expected approved clarification")
    approved = matches[0]
    if approved.answer.version != 1:
        raise ValueError("synthetic continuation requires approved answer v1")
    return approved


def _approved_fixture_answers(snapshot) -> None:
    approved = _approved_case(snapshot, [_REFUND_QUESTION, _MISSING_QUESTION])
    questions = approved.request.questions
    items = {item.question_index: item for item in approved.answer.items}
    if [question.question for question in questions] != [_REFUND_QUESTION, _MISSING_QUESTION]:
        raise ValueError("synthetic clarification changed")
    expected = {
        0: ("扣除已退款金额", "rule"),
        1: ("缺失金额单独列出", "null_handling"),
    }
    for index, (answer, property_name) in expected.items():
        item = items.get(index)
        if (
            item is None
            or item.disposition != "answered"
            or item.answer != answer
            or not any(
                target.kind == "field"
                and target.key == "amount"
                and target.property == property_name
                for target in item.targets
            )
        ):
            raise ValueError("synthetic approved answer changed")


def _approved_time_answer(snapshot) -> None:
    _approved_fixture_answers(snapshot)
    approved = _approved_case(snapshot, [_TIME_QUESTION])
    items = {item.question_index: item for item in approved.answer.items}
    item = items.get(0)
    if (
        item is None
        or item.disposition != "answered"
        or item.answer != _TIME_BASIS
        or item.respondent != "合成业务负责人"
        or item.basis != "本条合成验收回答"
        or not any(
            target.kind == "field"
            and target.key == "amount"
            and target.property == "time_basis"
            for target in item.targets
        )
    ):
        raise ValueError("synthetic approved time answer changed")


def _apply_approved_fixture(store, workspace_id: str, mission_id: str, run_id: str, snapshot) -> None:
    _approved_fixture_answers(snapshot)
    order, refund = _selected_fixture_sources(store, workspace_id, snapshot.source_refs)
    order_ref = _identity(order)
    refund_ref = _identity(refund)
    order_table = _table_id_with_columns(store, workspace_id, order, {"region", "amount"})
    refund_table = _table_id_with_columns(store, workspace_id, refund, {"region", "refund"})
    current = snapshot.draft
    if current is None or current.version != 1:
        raise ValueError("synthetic continuation requires draft v1")

    field = {
        "field_key": "amount",
        "name": "地区净订单金额",
        "meaning": "按地区汇总的净订单金额",
        "value_type": "number",
        "grain": "地区",
        "source_columns": [
            {"source_ref": order_ref.model_dump(mode="json"), "table_id": order_table, "column": "amount"},
            {"source_ref": refund_ref.model_dump(mode="json"), "table_id": refund_table, "column": "refund"},
        ],
        "rule": "订单金额合计减去同地区已退款金额合计",
        "time_basis": None,
        "null_handling": "缺失订单金额不进入净额，并单独列出",
        "evidence_status": "candidate",
        "source_refs": [],
        "unknowns": [
            {"property_path": "time_basis", "reason": "两份合成资料没有时间字段，无法确定统计期间"}
        ],
    }
    relationship = {
        "relationship_key": "orders_refunds_by_region",
        "left": {
            "source_ref": order_ref.model_dump(mode="json"),
            "table_id": order_table,
            "columns": ["region"],
        },
        "right": {
            "source_ref": refund_ref.model_dump(mode="json"),
            "table_id": refund_table,
            "columns": ["region"],
        },
        "observed_cardinality": "one_to_one",
        "join_rule": "订单.region = 退款.region",
        "grain_notes": "两份合成表均按地区一行",
        "evidence_status": "candidate",
        "source_refs": [],
        "risks": ["仅基于两行合成样例；正式资料仍需重新核验重复键和基数"],
        "unknowns": [],
    }
    draft = store.execute_run_tool(
        workspace_id,
        mission_id,
        run_id,
        UpdateDefinitionDraftCall(
            call_id=str(uuid4()),
            name="update_definition_draft",
            arguments={
                "expected_version": current.version,
                "expected_sha256": current.sha256,
                "fields": [field],
                "relationships": [relationship],
                "unresolved_items": ["时间口径尚未定义"],
            },
        ),
    ).output
    store.execute_run_tool(
        workspace_id,
        mission_id,
        run_id,
        CreateClarificationCall(
            call_id=str(uuid4()),
            name="create_clarification",
            arguments={
                "draft_version": draft.version,
                "draft_sha256": draft.sha256,
                "questions": [
                    {
                        "question": _TIME_QUESTION,
                        "why_needed": "两份合成资料没有时间字段，需要明确当前快照是否代表完整统计范围",
                        "expected_answer_type": "text",
                        "suggested_owner_role": "业务负责人",
                        "related_definition_paths": ["fields.amount.time_basis"],
                        "evidence_requested": ["明确的统计期间或无需时间筛选的业务说明"],
                        "examples_or_options": ["使用当前两份资料的全部记录，不设日期筛选"],
                        "blocking_impact": "blocking",
                        "source_refs": [],
                    }
                ],
            },
        ),
    )
    store.save_run_final_output(
        workspace_id,
        mission_id,
        run_id,
        "【合成验收】已应用批准回答 v1，候选草案已更新为 v2；退款与缺失金额规则已写入，时间口径仍待确认。",
    )


def _apply_approved_time(store, workspace_id: str, mission_id: str, run_id: str, snapshot) -> None:
    _approved_time_answer(snapshot)
    _selected_fixture_sources(store, workspace_id, snapshot.source_refs)
    current = snapshot.draft
    if current is None or current.version != 2:
        raise ValueError("synthetic time continuation requires draft v2")

    field = current.fields[0].model_dump(mode="json")
    if field["field_key"] != "amount":
        raise ValueError("synthetic amount field changed")
    field["time_basis"] = _TIME_BASIS
    field["unknowns"] = []
    draft = store.execute_run_tool(
        workspace_id,
        mission_id,
        run_id,
        UpdateDefinitionDraftCall(
            call_id=str(uuid4()),
            name="update_definition_draft",
            arguments={
                "expected_version": current.version,
                "expected_sha256": current.sha256,
                "fields": [field],
                "relationships": [item.model_dump(mode="json") for item in current.relationships],
                "unresolved_items": [],
            },
        ),
    ).output
    store.execute_run_tool(
        workspace_id,
        mission_id,
        run_id,
        SubmitForReviewCall(
            call_id=str(uuid4()),
            name="submit_for_review",
            arguments={"draft_version": draft.version, "draft_sha256": draft.sha256},
        ),
    )
    store.save_run_final_output(
        workspace_id,
        mission_id,
        run_id,
        "【合成验收】已应用时间范围回答 v1，候选草案已更新为 v3 并提交审核；当前没有待回答的业务口径。",
    )


def synthetic_run(store, workspace_id: str, mission_id: str, run_id: str, cancel: Event, **kwargs) -> None:
    store.mark_run_running(workspace_id, mission_id, run_id)
    if cancel.wait(0.7):
        store.cancel_run(workspace_id, mission_id, run_id)
        return
    snapshot = store.get_run_snapshot(workspace_id, mission_id, run_id)
    if not snapshot.draft:
        draft = store.execute_run_tool(
            workspace_id,
            mission_id,
            run_id,
            UpdateDefinitionDraftCall(
                call_id=str(uuid4()),
                name="update_definition_draft",
                arguments={
                    "expected_version": 0,
                    "expected_sha256": None,
                    "fields": [_unknown_field()],
                    "relationships": [],
                    "unresolved_items": [],
                },
            ),
        ).output
        base = {
            "why_needed": "影响统计金额与结果可核对性",
            "expected_answer_type": "text",
            "suggested_owner_role": "业务负责人",
            "related_definition_paths": ["fields.amount.rule"],
            "evidence_requested": ["明确的业务说明"],
            "examples_or_options": [],
            "blocking_impact": "blocking",
            "source_refs": [],
        }
        store.execute_run_tool(
            workspace_id,
            mission_id,
            run_id,
            CreateClarificationCall(
                call_id=str(uuid4()),
                name="create_clarification",
                arguments={
                    "draft_version": draft.version,
                    "draft_sha256": draft.sha256,
                    "questions": [
                        {**base, "question": _REFUND_QUESTION},
                        {
                            **base,
                            "question": _MISSING_QUESTION,
                            "related_definition_paths": ["fields.amount.null_handling"],
                        },
                    ],
                },
            ),
        )
        store.save_run_final_output(
            workspace_id,
            mission_id,
            run_id,
            "【合成验收】已建立金额候选定义。有两项业务口径需要你确认；可直接在右侧回答或追问原因。",
        )
        return
    if snapshot.draft.version == 1:
        try:
            _apply_approved_fixture(store, workspace_id, mission_id, run_id, snapshot)
        except ValueError:
            store.fail_run(workspace_id, mission_id, run_id, "failed", "synthetic_fixture_invalid")
        return
    if snapshot.draft.version == 2:
        try:
            _apply_approved_time(store, workspace_id, mission_id, run_id, snapshot)
        except ValueError:
            store.fail_run(workspace_id, mission_id, run_id, "failed", "synthetic_fixture_invalid")
        return
    store.execute_run_tool(
        workspace_id,
        mission_id,
        run_id,
        FinishRunCall(
            call_id=str(uuid4()),
            name="finish_run",
            arguments={
                "outcome": "partial",
                "reason": "【合成验收】当前候选已是 v2，没有重复写入；时间口径仍待确认。",
                "source_refs": [],
            },
        ),
    )
    store.save_run_final_output(
        workspace_id,
        mission_id,
        run_id,
        "【合成验收】当前候选已是 v2；退款与缺失金额规则已写入，时间口径仍待确认。",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8831)
    parser.add_argument("--reuse-data-dir", type=Path, help="Reopen only a fixture-marked synthetic database")
    parser.add_argument("--empty", action="store_true", help="Exercise first-send local workspace creation")
    parser.add_argument("--missing-model", action="store_true", help="Show connection prompt without reading credentials")
    parser.add_argument("--settings-delay", type=float, default=0, help="Synthetic settings latency for workspace-switch checks")
    return parser


def create_synthetic_app(args):
    root = args.reuse_data_dir or Path(tempfile.mkdtemp(prefix="contextox-agent-led-browser-"))
    if args.reuse_data_dir:
        marker = root / "fixture.json"
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("synthetic") is not True:
            raise ValueError("reuse requires this runner's synthetic fixture marker")

    local_settings.external_key_source = lambda: "environment"
    agent.get_provider = lambda **kwargs: SyntheticProvider()
    agent.run_agent = synthetic_run
    app = create_app(
        data_dir=root,
        static_dir=Path(__file__).resolve().parents[1] / "web" / "dist",
        agent_profile="demo-fast",
    )
    if args.missing_model:
        local_settings.external_key_source = lambda: None
        local_settings.MacKeychain = type("SyntheticEmptyKeychain", (), {"contains": lambda self: False})
    if args.settings_delay:
        @app.middleware("http")
        async def delayed_settings(request, call_next):
            if request.url.path == "/api/local-settings/deepseek":
                await asyncio.sleep(args.settings_delay)
            return await call_next(request)

    workspace_id = None
    if not args.empty and not args.reuse_data_dir:
        store = app.state.workspace_store
        workspace_id = store.create_workspace("合成验收 · 不连接模型").workspace_id
        for name, data in (
            ("订单.csv", "region,amount\n华东,20\n华南,30\n"),
            ("退款.csv", "region,refund\n华东,5\n华南,0\n"),
        ):
            store.import_source_revision(workspace_id, name, "text/csv", data.encode())
        store.create_workspace("范围隔离 · 空工作区")
    if not args.reuse_data_dir:
        (root / "fixture.json").write_text(
            json.dumps({"workspace_id": workspace_id, "synthetic": True}),
            encoding="utf-8",
        )
    return app, root


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        app, root = create_synthetic_app(args)
    except ValueError as error:
        parser.error(str(error))
    print("SYNTHETIC_DATA_DIR=" + str(root), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
