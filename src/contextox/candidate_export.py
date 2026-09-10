"""A local candidate handoff, with exact locators and no raw source rows."""
import html
import json
import re

from contextox.models import CandidateExportV1


def text(value) -> str:
    safe = html.escape(" ".join(str(value).splitlines()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", safe)


def candidate_markdown(export: CandidateExportV1) -> str:
    draft = export.draft
    lines = [f"# {text(export.mission_title)} · 候选草案", "",
        "> 本文为可继续完善的候选，不是正式 Contract、语义批准或 Mission 完成凭证。", "",
        f"- 工作区：`{export.workspace_id}`", f"- 任务：`{export.mission_id}`",
        f"- 草案：`{draft.draft_id}` · v{draft.version}", f"- 内容 SHA256：`{draft.sha256}`",
        f"- 语义批准：{draft.semantic_approval}", f"- 导出时间：{export.exported_at.isoformat()}", "", "## 字段候选", ""]
    names = {source.revision_id: source.original_name for source in export.sources}
    for field in draft.fields:
        lines.extend([f"### {text(field.name)}", "", text(field.meaning or "含义待补充"), ""])
        for key, value in field.model_dump(mode="json").items():
            if key not in {"name", "meaning", "source_refs", "source_columns"} and value is not None:
                encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                lines.append(f"- {text(key)}：{text(encoded)}")
        for ref in field.source_refs:
            lines.append(f"- @{text(names.get(ref.revision_id, '来源'))} · revision `{ref.revision_id}` · locator {text(json.dumps(ref.locator.model_dump(), ensure_ascii=False))}")
        lines.append("")
    lines.extend(["## 关系候选", ""])
    for relation in draft.relationships:
        lines.extend([f"### {text(relation.relationship_key)}", "", text(relation.join_rule or "连接规则待补充"), "",
            f"- 观测基数：{relation.observed_cardinality}", f"- 粒度：{text(relation.grain_notes or '待补充')}",
            f"- 左表：@{text(names.get(relation.left.source_ref.revision_id, '来源'))}/{text(relation.left.table_id)}",
            f"- 右表：@{text(names.get(relation.right.source_ref.revision_id, '来源'))}/{text(relation.right.table_id)}", ""])
    lines.extend(["## 未决项", "", *[f"- {text(item)}" for item in draft.unresolved_items], "", "## 澄清及回答记录", "",
        "以下保留各问题的最新回答与批准状态，不代表当前草案已全部采纳。", ""])
    for case in export.clarifications:
        lines.extend([f"### {case.request.clarification_id}", "", f"状态：{case.review_state}", ""])
        for index, question in enumerate(case.request.questions):
            lines.extend([f"{index + 1}. {text(question.question)}", f"   影响：{text(question.why_needed)}"])
        if case.latest_answer:
            lines.append(f"回答 v{case.latest_answer.version} · SHA256 `{case.latest_answer.sha256}`")
        if case.latest_approval:
            lines.append(f"该回答批准记录：`{case.latest_approval.approval_id}`")
        lines.append("")
    lines.extend(["## 完整结构化记录", "", "含字段维度、关系风险、未决项、证据 locator、来源版本和回答版本；不含来源原始行。", "",
        "```json", json.dumps(export.model_dump(mode="json"), ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)
