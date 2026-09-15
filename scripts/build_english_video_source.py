#!/usr/bin/env python3
"""Generate the English product-film source from the reviewed V4 composition.

The composition, timings, mouse coordinates and audio cues stay identical to the
approved Chinese film. Only audience-facing copy is translated, which makes the
English render a language variant rather than a second, drifting storyboard.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIDEO_SRC = ROOT / "video" / "src"


TRANSLATIONS = {
    "公开合成演示 · 三个数字不是产品运行结果": "Public synthetic Demo · the three numbers are not product results",
    "华东 · 公开合成订单": "East China · public synthetic orders",
    "同一份订单，三种规则": "One order, three rules",
    "同一份订单，华东到底多少？": "What is the East China total?",
    "全部状态": "All statuses",
    "只算已支付": "Paid only",
    "再扣退款": "Minus refunds",
    "不是算错，是规则没说清": "The math is not wrong; the rule is unclear",
    "哪些算进去 · 退款怎么处理 · 按哪个时间": "What counts · how refunds work · which time to use",
    "确认资料，说目标": "Confirm sources, state the goal",
    "目标已输入，等待发送": "Goal entered, waiting to send",
    "说说你想弄清什么，也可以先添加资料": "Say what you want to understand, or add sources first",
    "一起把问题弄清楚": "Work through the question together",
    "展开对话": "Expand conversation",
    "资料 · 3": "Sources · 3",
    "发送": "Send",
    "明确目标": "Set the goal",
    "理解资料": "Understand sources",
    "澄清口径": "Clarify the rule",
    "整理成果": "Organize the result",
    "当前进展": "Current progress",
    "正在理解 3 份资料": "Understanding 3 sources",
    "按下发送，开始理解资料": "Press send to understand the sources",
    "待支付订单算不算？": "Should pending orders count?",
    "会影响 199 元是否进入汇总。": "This changes whether 199 CNY enters the total.",
    "只统计已支付订单": "Count paid orders only",
    "业务负责人": "Business owner",
    "本次汇总只看已经完成支付的订单。": "This total includes only orders whose payment is complete.",
    "退款从哪里扣？": "Where should refunds be deducted?",
    "会影响华东是否减去 50 元。": "This changes whether East China subtracts 50 CNY.",
    "从原订单地区扣除": "Subtract from the original region",
    "退款沿用原订单的客户地区。": "A refund keeps the customer region of the original order.",
    "按哪个时间归属？": "Which time assigns the order?",
    "会影响跨日、跨月时落在哪个期间。": "This changes the period for orders crossing days or months.",
    "按支付时间归属": "Assign by payment time",
    "数据负责人": "Data owner",
    "本次口径以 paid_at 的北京时间为准。": "This rule uses the China Standard Time value of paid_at.",
    "为什么需要确认：": "Why confirmation is needed: ",
    "暂时不知道": "Not sure yet",
    "回答卡片中的真实问题结构": "the real question structure in the answer card",
    "刚刚": "just now",
    "我找到了 3 个会改变结果的问题。先确认这些规则，我不会替你猜。": "I found 3 questions that can change the result. Let us confirm the rules; I will not guess for you.",
    "建议下一步": "Suggested next step",
    "回答 3 个会改变结果的问题": "Answer 3 questions that can change the result",
    "填写回答卡片": "Fill the answer card",
    "为什么需要确认": "Why confirmation is needed",
    "需要你确认的业务口径": "Business rules that need your confirmation",
    "请检查回答、来源和仍未知的事项；只有确认后才会采用。": "Check the answer, source and open items; it is used only after confirmation.",
    "会改变结果的问题，先问清楚": "Ask the questions that can change the result",
    "Agent 已把对话整理成卡片。请检查回答、来源和依据。": "The Agent organized the conversation into a card. Check the answer, source and basis.",
    "3 个问题 · 等待填写": "3 questions · waiting for answers",
    "可以直接选择": "Choose directly",
    "你现在能确认吗？": "Can you confirm this now?",
    "我可以确认": "I can confirm",
    "回答": "Answer",
    "回答来源与依据（保存必填）": "Answer source and basis (required to save)",
    "回答来源人或角色": "Person or role providing the answer",
    "回答依据": "Basis for the answer",
    "3 个问题已经填写完整，来源与依据也已补齐。": "All 3 questions are filled; sources and bases are complete.",
    "来源：": "Source: ",
    "依据已填写": "basis provided",
    "仅保存，稍后继续": "Save only; continue later",
    "保存不会调用模型，也不会自动采用业务结论。": "Saving does not call a model or automatically adopt a business conclusion.",
    "回答已保存 · 尚未批准": "Answers saved · not approved",
    "整份回答已确认 · 后续分析已经开始": "All answers confirmed · follow-up analysis has started",
    "回答、来源和依据，都由人确认": "People confirm the answers, sources and bases",
    "按地区统计订单金额": "Report order amounts by region",
    "正在做什么": "What is happening",
    "候选成果已经更新": "Candidate result updated",
    "正在澄清业务口径": "Clarifying the business rule",
    "需要你做什么": "What we need from you",
    "核对本轮结果": "Review this round’s result",
    "回答关键问题": "Answer the key questions",
    "已经得到什么": "What we have",
    "1 个候选字段 · 1 个关系": "1 candidate field · 1 relationship",
    "3 个待确认问题": "3 questions to confirm",
    "继续补充口径，也可以查看本轮变化": "Add more detail, or view this round's changes",
    "当前已明确目标": "Goal currently set",
    "按地区统计订单金额，并把退款和缺失金额的处理规则写清楚。": "Report order amounts by region and clarify how refunds and missing amounts are handled.",
    "已应用这次确认的业务回答，候选方案已经更新；当前没有待回答的业务口径。": "The confirmed answers were applied and the candidate updated; no business rule is waiting for an answer.",
    "确认并继续": "Confirm and continue",
    "核对候选成果和仍未知的事项": "Review the candidate result and open items",
    "查看本轮变化": "View this round's changes",
    "继续补充口径": "Add more detail",
    "业务回答已确认 · 展开查看": "Business answers confirmed · expand to view",
    "业务含义": "Business meaning",
    "地区净订单金额": "Net order amount by region",
    "按客户地区汇总已支付金额，再扣除同一地区的退款金额": "Aggregate paid amounts by customer region, then subtract refunds from that region",
    "值类型": "Value type",
    "人民币金额": "CNY amount",
    "业务粒度": "Business grain",
    "客户地区 × 统计期间": "Customer region × reporting period",
    "业务规则": "Business rule",
    "只统计已支付；退款从原订单地区扣除": "Count paid only; subtract refunds from the original region",
    "时间口径": "Time rule",
    "关系与字段": "Relationships and fields",
    "过程记录": "Activity log",
    "候选成果 · 等待核对": "Candidate result · awaiting review",
    "候选，尚未批准": "Candidate, not approved",
    "字段名称": "Field name",
    "证据状态：候选": "Evidence status: candidate",
    "关系候选": "Candidate relationship",
    "多对一": "many-to-one",
    "资料来源：": "Sources: ",
    "仍未解决": "Still open",
    "跨月退款边界仍待补充": "The cross-month refund boundary still needs detail",
    "本轮更新": "Updated this round",
    "这里展示已采用的业务回答、它带来的变化和仍待确认的事项。": "This shows the adopted business answers, their changes and the items still to confirm.",
    "查看本轮采用的回答 · 3 个问题": "View the adopted answers · 3 questions",
    "这次回答带来的变化": "What these answers changed",
    "人的回答": "Human answer",
    "纳入、退款和时间规则已经确认": "Inclusion, refund and time rules are confirmed",
    "候选变化": "Candidate change",
    "地区净订单金额 · 新增": "Net order amount by region · added",
    "跨月退款边界还要补充": "Cross-month refund boundary needs detail",
    "回答进入候选字段，不是最终批准": "Answers enter a candidate field, not a final approval",
    "人的回答、变化和未知，都能回看": "Answers, changes and unknowns stay reviewable",
    "通用执行型 Agent": "General execution Agent",
    "目标清楚后，": "Once the goal is clear,",
    "把任务做出来": "get the task done",
    "代码 · 查询 · 文档 · 工程交付": "Code · queries · documents · engineering delivery",
    "目标还没说清时，": "When the goal is still unclear,",
    "先把定义问明白": "clarify the definition first",
    "证据 · 人的确认 · 变化 · 仍未知": "Evidence · human confirmation · changes · open items",
    "先用数契说清楚 → 再交给 Codex 或数据团队执行": "Clarify it with ContextOx → then hand it to Codex or the data team",
    "这是默认注重点，不是绝对能力边界": "This is the current focus, not an absolute capability boundary",
    "把表里的": "Make the table's",
    "业务意思说清楚": "business meaning clear",
    "带一个总对不上的业务口径，和数契一起把它说明白。": "Bring a business definition that never quite matches, and make it clear with ContextOx.",
    "公开合成 Demo · 候选结果由人核对": "Public synthetic Demo · candidate results checked by people",
    "联系 @archerthegoat · github.com/archerthegoat/contextox-agent": "Contact @archerthegoat · github.com/archerthegoat/contextox-agent",
    "第 {ruleIndex + 1} 种算法": "Calculation {ruleIndex + 1}",
    "订单": "Order",
    "地区": "Region",
    "状态": "Status",
    "金额": "Amount",
    "华东": "East China",
    "已支付": "Paid",
    "已退款": "Refunded",
    "待支付": "Pending",
    "689 元": "689 CNY",
    "440 元": "440 CNY",
    "390 元": "390 CNY",
    "元": "CNY",
    "数契": "ContextOx",
}


def apply(source: str) -> str:
    output = source
    for chinese, english in sorted(TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        output = output.replace(chinese, english)
    return output


def main() -> None:
    hook = apply((VIDEO_SRC / "v4-hook.tsx").read_text(encoding="utf-8"))
    hook = hook.replace("ContextOxProductFilmV4HookAVisual", "__HOOK_A_VISUAL__")
    hook = hook.replace("ContextOxProductFilmV4HookA", "ContextOxProductFilmV4HookAEn")
    hook = hook.replace("ContextOxProductFilmV4HookB", "ContextOxProductFilmV4HookBEn")
    hook = hook.replace("__HOOK_A_VISUAL__", "ContextOxProductFilmV4HookAVisualEn")

    full = apply((VIDEO_SRC / "v4-full.tsx").read_text(encoding="utf-8"))
    full = full.replace("from './v4-hook';", "from './v4-hook-en';")
    full = full.replace("ContextOxProductFilmV4HookAVisual", "ContextOxProductFilmV4HookAVisualEn")
    full = full.replace("ContextOxProductFilmV4A", "ContextOxProductFilmV4AEn")

    for name, source in (("v4-hook-en.tsx", hook), ("v4-full-en.tsx", full)):
        remaining = sorted(set(re.findall(r"[\u4e00-\u9fff]+", source)))
        if remaining:
            raise SystemExit(f"untranslated CJK in {name}: {remaining}")
        (VIDEO_SRC / name).write_text(source, encoding="utf-8")
        print(f"wrote video/src/{name}")


if __name__ == "__main__":
    main()
