#!/usr/bin/env python3
"""Build the English PDF companions for the public dynamic showcase."""

from __future__ import annotations

import shutil
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from build_showcase_assets import (
    ASSETS,
    BLUE,
    FONT_BOLD,
    INK,
    LINE,
    MUTED,
    PAPER,
    PDF_DIR,
    ROOT,
    SITE,
    WHITE,
    draw_pdf_text,
    draw_wrapped,
    ensure_dirs,
    page_base,
    register_pdf_fonts,
)


DOWNLOADS = SITE / "downloads"


def box(c: canvas.Canvas, x: float, y: float, width: float, height: float, *, fill: str = WHITE, stroke: str = LINE) -> None:
    c.setFillColor(HexColor(fill))
    c.setStrokeColor(HexColor(stroke))
    c.rect(x, y, width, height, stroke=1, fill=1)


def title(c: canvas.Canvas, number: int, kicker: str, heading: str, lead: str = "", *, stage: bool = False) -> None:
    width, height = 960, 540
    page_base(c, width, height, number, stage=stage)
    ink = WHITE if stage else INK
    muted = "#C7D4E5" if stage else MUTED
    accent = "#81B5FF" if stage else BLUE
    draw_pdf_text(c, kicker, 54, 485, 10, accent, True)
    if stage:
        # Stage titles are allowed to breathe across two lines; a single long
        # line would be clipped in the 16:9 export and in browser previews.
        draw_wrapped(c, heading, 54, 438, 820, 34, 42, ink, True)
    else:
        draw_pdf_text(c, heading, 54, 438, 29, ink, True)
    if lead:
        draw_wrapped(c, lead, 54, 407, 820, 11, 16, muted)


def draw_presentation(path: Path) -> None:
    width, height = 960, 540
    c = canvas.Canvas(str(path), pagesize=(width, height), pageCompression=1, invariant=1)

    # 01 — the hook
    title(c, 1, "A REAL QUESTION · PUBLIC SYNTHETIC DATA", "The same order data —\nwhy does it have three answers?", stage=True)
    for index, (value, label) in enumerate((("689", "add every record"), ("440", "paid only"), ("390", "paid minus refunds"))):
        x = 54 + index * 286
        box(c, x, 148, 260, 132, fill="#142840" if value != "390" else "#173F73", stroke="#4D9AFF" if value == "390" else "#4C6077")
        c.setFillColor(HexColor("#81B5FF"))
        c.rect(x, 148, 6 if value == "390" else 4, 132, stroke=0, fill=1)
        draw_pdf_text(c, value, x + 22, 212, 42, WHITE, True)
        draw_pdf_text(c, "CNY", x + 116, 218, 13, "#B8C8DD")
        draw_pdf_text(c, label, x + 22, 174, 11, "#B8C8DD")
    draw_wrapped(c, "All three numbers can be calculated. What is missing is the rule: what counts, how refunds are handled, and which time to use.", 54, 108, 820, 14, 20, "#C7D4E5")
    draw_pdf_text(c, "数契 ContextOx · Demo 1.0.0", 54, 42, 9, "#A9B8CA")
    c.showPage()

    # 02 — data and calculations
    title(c, 2, "01 / HOW THE THREE ANSWERS ARE CALCULATED", "One dataset, three calculations", "Six public synthetic orders. Region comes from joining the customers table.")
    table_x, table_y, table_w, row_h = 54, 105, 510, 38
    headers = ("Order", "Region", "Amount", "Status")
    col_x = (table_x, table_x + 98, table_x + 245, table_x + 345)
    c.setFillColor(HexColor("#E9EEF5")); c.rect(table_x, table_y + row_h * 6, table_w, row_h, stroke=0, fill=1)
    for x, header in zip(col_x, headers, strict=True): draw_pdf_text(c, header, x + 12, table_y + row_h * 6 + 13, 9, MUTED, True)
    rows = (("O001", "East China", "120.00", "paid"), ("O002", "South China", "80.50", "paid"), ("O003", "East China", "50.00", "refunded"), ("O004", "East China", "199.00", "pending"), ("O005", "South China", "60.00", "paid"), ("O006", "East China", "320.00", "paid"))
    for index, row in enumerate(rows):
        y = table_y + row_h * (5 - index)
        if row[3] == "refunded": c.setFillColor(HexColor("#FFF5F4")); c.rect(table_x, y, table_w, row_h, stroke=0, fill=1)
        if row[3] == "pending": c.setFillColor(HexColor("#FFF8EA")); c.rect(table_x, y, table_w, row_h, stroke=0, fill=1)
        c.setStrokeColor(HexColor(LINE)); c.line(table_x, y, table_x + table_w, y)
        for x, value in zip(col_x, row, strict=True):
            color = "#B8493E" if value == "refunded" else "#A76A08" if value == "pending" else INK
            draw_pdf_text(c, value, x + 12, y + 13, 9, color, value in {"paid", "refunded", "pending"})
    for index, (label, formula, value, focus) in enumerate((("A · add all records", "120 + 50 + 199 + 320", "689 CNY", False), ("B · paid only", "120 + 320", "440 CNY", False), ("C · paid minus refunded", "120 + 320 − 50", "390 CNY", True))):
        y = 298 - index * 96
        box(c, 610, y, 296, 78, fill="#EEF5FF" if focus else WHITE, stroke=BLUE if focus else LINE)
        draw_pdf_text(c, label, 628, y + 55, 8, BLUE if focus else MUTED, True)
        draw_pdf_text(c, formula, 628, y + 26, 10, INK, True)
        draw_pdf_text(c, value, 820, y + 24, 16, BLUE if focus else INK, True)
    c.showPage()

    # 03 — ambiguity
    title(c, 3, "02 / WHY THE REWORK KEEPS COMING BACK", "The formula is easy; the rule is still in everyone's head", "One request can silently turn into three different business rules.")
    box(c, 54, 142, 216, 188)
    draw_pdf_text(c, "ONE REQUEST", 74, 298, 9, BLUE, True)
    draw_wrapped(c, "Report order amounts by region", 74, 252, 170, 17, 24, INK, True)
    branches = (("Business", "Include pending orders", "689"), ("Analyst", "Look at paid only", "440"), ("Finance", "Subtract refunds", "390"))
    for index, (owner, rule, value) in enumerate(branches):
        y = 273 - index * 70
        box(c, 306, y, 340, 55)
        draw_pdf_text(c, owner, 322, y + 21, 9, MUTED)
        draw_pdf_text(c, rule, 390, y + 19, 11, INK, True)
        draw_pdf_text(c, value, 592, y + 18, 16, BLUE, True)
    box(c, 680, 142, 226, 188, fill="#FFF9ED", stroke="#D6B46D")
    draw_pdf_text(c, "THE QUESTION LEFT BEHIND", 698, 298, 8, "#A76A08", True)
    draw_wrapped(c, "Who is wrong? Where is the evidence? Which rule should we use next time?", 698, 254, 185, 14, 21, INK, True)
    c.showPage()

    # 04 — product promise
    title(c, 4, "CONTEXTOX", "Make the business meaning clear before delivery starts", stage=True)
    promises = ("Bring the sources in and say what you need to understand", "The Agent finds evidence and asks about rules that can change the result", "A person confirms the key rules and leaves a reviewable candidate definition")
    for index, text in enumerate(promises, 1):
        y = 184 - (index - 1) * 48
        draw_pdf_text(c, f"0{index}", 64, y, 10, "#63A4FF", True)
        draw_wrapped(c, text, 112, y, 760, 14, 19, "#CFDAEA")
        c.setStrokeColor(HexColor("#34475E")); c.line(64, y - 15, 870, y - 15)
    draw_pdf_text(c, "Agent finds evidence · people confirm business facts", 64, 54, 10, "#A9B8CA")
    c.showPage()

    # 05 — source selection / Workbench
    title(c, 5, "03 / STEP ONE", "Tell it which sources to use this time", "Set the boundary first, so the Agent knows what this round can rely on.")
    box(c, 54, 72, 852, 320)
    c.setFillColor(HexColor("#F7F9FC")); c.rect(54, 72, 154, 320, stroke=0, fill=1)
    c.setStrokeColor(HexColor(LINE)); c.line(208, 72, 208, 392); c.line(628, 72, 628, 392)
    draw_pdf_text(c, "数契", 76, 352, 15, INK, True)
    draw_pdf_text(c, "SOURCES", 72, 306, 8, MUTED, True)
    for index, name in enumerate(("✓ orders.csv", "✓ customers.csv", "✓ notes.md")):
        y = 268 - index * 38; c.setFillColor(HexColor("#EDF4FE")); c.rect(68, y, 126, 27, stroke=0, fill=1); draw_pdf_text(c, name, 78, y + 9, 8, "#205FA8", True)
    draw_pdf_text(c, "CURRENT PROGRESS", 230, 352, 8, BLUE, True)
    draw_pdf_text(c, "Start with one question", 230, 321, 19, INK, True)
    draw_pdf_text(c, "1 Set goal    2 Understand    3 Clarify    4 Organize", 230, 285, 8, MUTED, True)
    draw_pdf_text(c, "3 sources are in this round", 230, 228, 12, INK, True)
    draw_wrapped(c, "Orders, customers and notes enter this analysis together.", 230, 200, 350, 10, 15, MUTED)
    draw_pdf_text(c, "ContextOx Agent", 650, 352, 11, INK, True)
    draw_wrapped(c, "What do you want to understand from these three sources?", 650, 298, 220, 10, 15, MUTED)
    box(c, 650, 104, 236, 104, stroke="#BFCBD7")
    draw_wrapped(c, "I want to report order amounts by region.", 664, 180, 205, 10, 15, INK)
    c.setFillColor(HexColor(BLUE)); c.rect(812, 118, 58, 25, stroke=0, fill=1); draw_pdf_text(c, "Send", 828, 126, 8, WHITE, True)
    c.showPage()

    # 06 — questions
    title(c, 6, "04 / STEP TWO", "The Agent asks the questions that can change the result", "It does not guess business facts. Each question explains why it needs an answer now.")
    box(c, 54, 100, 292, 276)
    draw_pdf_text(c, "ALREADY FOUND IN THE SOURCES", 76, 342, 8, BLUE, True)
    for index, finding in enumerate(("Order status: paid, refunded, pending", "Join: customer_id", "Time: paid_at")):
        draw_wrapped(c, finding, 76, 294 - index * 58, 242, 11, 16, INK, index == 1)
    questions = (("01", "Should pending orders count?", "Changes the East China 199 CNY", "population"), ("02", "How should refunds be handled?", "Excluding vs subtracting changes the answer", "amount rule"), ("03", "Which time assigns the order?", "Payment time changes the time window", "time window"))
    for index, (number, heading, body, effect) in enumerate(questions):
        y = 298 - index * 91; box(c, 382, y, 524, 76)
        draw_pdf_text(c, number, 400, y + 29, 10, BLUE, True); draw_pdf_text(c, heading, 444, y + 44, 12, INK, True); draw_pdf_text(c, body, 444, y + 22, 8, MUTED)
        c.setFillColor(HexColor("#FFF0CF")); c.rect(818, y + 24, 70, 24, stroke=0, fill=1); draw_pdf_text(c, effect, 826, y + 32, 7, "#A76A08", True)
    c.showPage()

    # 07 — human confirmation
    title(c, 7, "05 / STEP THREE", "A person makes the decision in plain language", "The Agent organizes answers into rules; a person checks them before the next round.")
    box(c, 54, 88, 292, 314, fill="#F5F7FA")
    draw_pdf_text(c, "AWAITING CONFIRMATION", 76, 365, 8, "#A76A08", True)
    draw_wrapped(c, "I organized your answers into 3 rules", 76, 322, 240, 17, 23, INK, True)
    draw_pdf_text(c, "After confirmation, update", 76, 204, 8, MUTED); draw_pdf_text(c, "Net order amount by region", 76, 174, 11, INK, True); draw_pdf_text(c, "Order–customer relationship", 76, 148, 11, INK, True)
    rules = (("Which orders count", "Count paid orders only", "Pending does not enter the amount"), ("How refunds are handled", "Subtract from original region", "Treat refunded as a deduction"), ("Which time to use", "Assign by payment time", "Pending orders have no payment time"))
    for index, (label, answer, note) in enumerate(rules):
        y = 298 - index * 82; box(c, 372, y, 534, 68)
        draw_pdf_text(c, label, 390, y + 39, 8, MUTED); draw_pdf_text(c, answer, 522, y + 38, 10, INK, True); draw_pdf_text(c, note, 522, y + 17, 8, MUTED)
    c.setFillColor(HexColor(BLUE)); c.rect(780, 102, 126, 34, stroke=0, fill=1); draw_pdf_text(c, "Confirm & continue", 795, 114, 8, WHITE, True)
    c.showPage()

    # 08 — candidate definition
    title(c, 8, "06 / WHAT THE ANSWERS CHANGE", "After the answers, the definition really changes", "390 CNY illustrates the rule difference; the product stops at a candidate definition.")
    box(c, 54, 84, 340, 304, fill="#F0F6FF")
    draw_pdf_text(c, "UPDATED THIS ROUND", 76, 352, 8, BLUE, True); draw_pdf_text(c, "Net order amount by region", 76, 300, 20, INK, True)
    draw_wrapped(c, "Aggregate paid amounts by customer region, then subtract refunds from that same region.", 76, 264, 286, 10, 15, MUTED)
    draw_pdf_text(c, "1 candidate field", 76, 158, 9, BLUE, True); draw_pdf_text(c, "1 candidate relationship", 190, 158, 9, BLUE, True); draw_pdf_text(c, "3 confirmed rules", 76, 124, 9, BLUE, True)
    rows = (("Included records", "status = paid", "Confirmed"), ("Refund handling", "Subtract from original region", "Confirmed"), ("Time assignment", "paid_at · China Standard Time", "Confirmed"), ("Source relationship", "orders.customer_id = customers.customer_id", "Evidence linked"))
    for index, (label, value, status) in enumerate(rows):
        y = 319 - index * 58; c.setStrokeColor(HexColor(LINE)); c.line(426, y - 12, 906, y - 12); draw_pdf_text(c, label, 426, y + 10, 8, MUTED); draw_pdf_text(c, value, 516, y + 9, 9, INK, True); draw_pdf_text(c, status, 838, y + 9, 7, "#147D64", True)
    c.setFillColor(HexColor(PAPER)); c.rect(426, 100, 480, 44, stroke=0, fill=1); draw_pdf_text(c, "notes.md · line 7: inclusion, refunds and time need business confirmation", 442, 116, 8, MUTED)
    c.showPage()

    # 09 — traceability
    title(c, 9, "07 / THE RECORD LEFT BEHIND", "What remains is more than an AI reply", "A new owner can see where the conclusion came from and what is still open.")
    chain = (("01", "Sources this round", "3 public synthetic files"), ("02", "Why we asked", "Choices that change the result"), ("03", "Who decided", "Human answer and confirmation"), ("04", "What changed", "Fields, relationships and rules"), ("05", "Still unknown", "Open items stay visible"))
    for index, (number, label, text) in enumerate(chain):
        x = 54 + index * 170; box(c, x, 154, 154, 196); c.circle(x + 28, 318, 14, stroke=1, fill=0); draw_pdf_text(c, number, x + 20, 315, 8, BLUE, True); draw_pdf_text(c, label, x + 18, 254, 8, MUTED); draw_wrapped(c, text, x + 18, 224, 118, 11, 16, INK, True)
    c.setFillColor(HexColor(INK)); c.rect(54, 88, 834, 44, stroke=0, fill=1); draw_pdf_text(c, "Review without guessing again", 74, 104, 11, WHITE, True); draw_pdf_text(c, "Evidence, answers and changes can be traced through the same record", 500, 104, 8, "#CBD5E1")
    c.showPage()

    # 10 — comparison
    title(c, 10, "08 / WHERE IT FITS", "Different tools solve different stages", "ContextOx focuses on the stage where the business definition is still unclear.")
    headers = ("What you want to do", "Better-fit tool", "ContextOx's place")
    xs = (54, 374, 570); c.setFillColor(HexColor("#E9EEF5")); c.rect(54, 365, 852, 28, stroke=0, fill=1)
    for x, header in zip(xs, headers, strict=True): draw_pdf_text(c, header, x + 14, 375, 8, MUTED, True)
    rows = (("Complete coding, research or making work", "General Agent, such as Codex", "Hand execution to it once the definition is clear"), ("Build catalogs, lineage, permissions and context", "Atlan, DataHub", "Connect later; do not rebuild the data foundation"), ("Ask questions on an existing semantic model", "BI and data-question tools", "ContextOx handles the step before the rule is settled"), ("One request has several possible meanings", "ContextOx focus today", "Find gaps, ask a person, leave a reviewable definition"))
    for index, row in enumerate(rows):
        y = 307 - index * 59; focus = index == 3; box(c, 54, y, 852, 59, fill="#EEF5FF" if focus else WHITE, stroke="#A8C9F7" if focus else LINE); draw_pdf_text(c, row[0], xs[0] + 14, y + 24, 9, "#155DA8" if focus else INK, True); draw_pdf_text(c, row[1], xs[1] + 14, y + 24, 8, "#155DA8" if focus else INK, True); draw_wrapped(c, row[2], xs[2] + 14, y + 31, 320, 8, 12, MUTED)
    c.setFillColor(HexColor(INK)); c.rect(54, 78, 852, 42, stroke=0, fill=1); draw_pdf_text(c, "ContextOx's current bet", 70, 94, 9, "#81B5FF", True); draw_pdf_text(c, "Keep the human decision, evidence and unknowns in one candidate definition.", 206, 94, 8, WHITE); draw_pdf_text(c, "Public references: OpenAI Codex · Atlan CES · DataHub · Microsoft Power BI", 54, 52, 7, MUTED)
    c.showPage()

    # 11 — boundaries
    title(c, 11, "09 / DEMO 1.0.0", "What it can do now—and what it cannot")
    columns = ((54, "YOU CAN TRY NOW", "#E9F7F2", "#147D64", (("Have a continuous Agent conversation", "No complex run concepts first"), ("Set the source boundary", "Use only selected sources"), ("Ask about and confirm rules", "Human decisions stay explicit"), ("Review candidates and evidence", "Changes and unknowns stay visible"))), (494, "DO NOT INTERPRET IT AS", "#FFF0CF", "#A76A08", (("A candidate is a formal approval", "Key definitions still need review"), ("A production database writer", "The Demo does not run SQL or writes"), ("Cloud multi-user collaboration", "Workspace and state stay local"), ("Validated customer value", "Engineering checks do not replace outcomes"))))
    for x, label, tag_bg, tag_color, items in columns:
        box(c, x, 80, 412, 318); c.setFillColor(HexColor(tag_bg)); c.rect(x + 22, 350, 150, 25, stroke=0, fill=1); draw_pdf_text(c, label, x + 34, 359, 8, tag_color, True)
        for index, (heading, body) in enumerate(items):
            y = 306 - index * 62; draw_pdf_text(c, heading, x + 24, y, 10, INK, True); draw_pdf_text(c, body, x + 24, y - 20, 8, MUTED); c.setStrokeColor(HexColor(LINE)); c.line(x + 24, y - 31, x + 388, y - 31)
    c.showPage()

    # 12 — CTA
    title(c, 12, "CONTEXTOX · DEMO 1.0.0", "Bring a business definition that\nnever quite matches,\nand make it clear with ContextOx", stage=True)
    draw_wrapped(c, "Start with the public synthetic example, or bring a redacted set of sources for one round.", 54, 316, 770, 14, 20, "#C7D4E5")
    c.setFillColor(white); c.rect(54, 222, 236, 42, stroke=0, fill=1); draw_pdf_text(c, "Discuss a question or request a trial", 70, 238, 8, INK, True); c.linkURL("https://github.com/archerthegoat/contextox-agent/issues", (54, 222, 290, 264), relative=0)
    draw_pdf_text(c, "GitHub contact", 326, 250, 8, "#A9B8CA", True); draw_pdf_text(c, "@archerthegoat", 326, 226, 13, "#81B5FF", True); c.linkURL("https://github.com/archerthegoat", (326, 218, 466, 256), relative=0)
    draw_pdf_text(c, "Public source · public synthetic Demo · candidate results checked by people", 54, 172, 9, "#A9B8CA")
    draw_pdf_text(c, "github.com/archerthegoat/contextox-agent", 54, 142, 9, "#81B5FF"); c.linkURL("https://github.com/archerthegoat/contextox-agent", (54, 134, 410, 154), relative=0)
    c.showPage(); c.save()


def draw_one_pager(path: Path) -> None:
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4, pageCompression=1, invariant=1)
    c.setFillColor(HexColor(PAPER)); c.rect(0, 0, width, height, stroke=0, fill=1); c.setFillColor(HexColor(BLUE)); c.rect(0, height - 18, width, 18, stroke=0, fill=1)
    draw_pdf_text(c, "ContextOx · Demo 1.0.0", 46, height - 72, 16, BLUE, True); draw_pdf_text(c, "Make every table explain what it means.", 46, height - 136, 31, INK, True); draw_wrapped(c, "Turn tables, notes and human decisions into evidence-backed, reviewable business definitions.", 46, height - 178, width - 92, 14, 22, MUTED)
    draw_pdf_text(c, "WHY", 46, height - 252, 12, BLUE, True); draw_wrapped(c, "The refund, missing-data and time rules that decide a number are often scattered across sources and people's memory.", 46, height - 280, width - 92, 11, 18, INK)
    draw_pdf_text(c, "HOW IT WORKS", 46, height - 360, 12, BLUE, True)
    for i, step in enumerate(("1  Select sources", "2  State the goal", "3  Answer and confirm rules", "4  Review candidate definition and evidence")):
        x = 46 + (i % 2) * 254; y = height - 400 - (i // 2) * 54; box(c, x, y, 230, 38); draw_pdf_text(c, step, x + 14, y + 13, 10, INK, True)
    draw_pdf_text(c, "BOUNDARY", 46, height - 524, 12, BLUE, True); draw_wrapped(c, "The current result is a reviewable candidate definition, not a formal Contract. The workspace stays local; model context is limited to the selected sources.", 46, height - 552, width - 92, 10, 17, MUTED)
    c.drawImage(ImageReader(str(ASSETS / "demo-real.jpg")), 46, 28, width=285, height=160, preserveAspectRatio=True, anchor="c"); c.setFillColor(HexColor(INK)); c.rect(208, 162, 123, 24, stroke=0, fill=1); draw_pdf_text(c, "PUBLIC SYNTHETIC DEMO", 216, 169, 7, WHITE, True); draw_pdf_text(c, "github.com/archerthegoat/contextox-agent", 360, 96, 9, BLUE, True); c.save()


def main() -> None:
    ensure_dirs(); register_pdf_fonts()
    deck = PDF_DIR / "contextox-demo-1.0.0-presentation-en.pdf"; one_pager = PDF_DIR / "contextox-demo-1.0.0-one-pager-en.pdf"
    draw_presentation(deck); draw_one_pager(one_pager)
    shutil.copy2(deck, DOWNLOADS / deck.name); shutil.copy2(one_pager, DOWNLOADS / one_pager.name)
    print(f"built {deck.name} and {one_pager.name}")


if __name__ == "__main__":
    main()
