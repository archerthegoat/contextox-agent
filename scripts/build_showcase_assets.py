#!/usr/bin/env python3
"""Build deterministic PDFs, share cards, Logo PNGs, and the brand kit."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import shutil
import zipfile

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ASSETS = SITE / "assets"
DOWNLOADS = SITE / "downloads"
PDF_DIR = ROOT / "output" / "pdf"
TMP_DIR = ROOT / "tmp" / "pdfs"

BLUE = "#1674F3"
INK = "#13243A"
MUTED = "#607086"
PAPER = "#F5F7FA"
LINE = "#DCE3EB"
WHITE = "#FFFFFF"

FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
FONT_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
MARK_SOURCE = ROOT / "web" / "src" / "assets" / "contextox-mark.png"


def ensure_dirs() -> None:
    for path in (DOWNLOADS, PDF_DIR, TMP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def pil_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size=size, index=0)


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    while size > 18:
        font = pil_font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return pil_font(size, bold)


def draw_mark(image: Image.Image, x: int, y: int, size: int) -> None:
    with Image.open(MARK_SOURCE) as source:
        mark = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    image.alpha_composite(mark, (x, y))


def make_card(path: Path, size: tuple[int, int], layout: str) -> None:
    width, height = size
    image = Image.new("RGBA", size, PAPER)
    draw = ImageDraw.Draw(image)
    step = max(36, width // 28)
    for x in range(0, width, step):
        draw.line((x, 0, x, height), fill="#E4E9F0", width=1)
    for y in range(0, height, step):
        draw.line((0, y, width, y), fill="#E4E9F0", width=1)

    pad = int(min(width, height) * 0.08)
    mark_size = int(min(width, height) * 0.12)
    draw_mark(image, pad, pad, mark_size)
    brand_font = pil_font(max(22, mark_size // 3), True)
    draw.text((pad + mark_size + 18, pad + mark_size * 0.18), "数契 ContextOx", fill=INK, font=brand_font)

    if layout == "landscape":
        title = "让每一张表，\n都说清自己代表什么。"
        title_size = int(height * 0.105)
        title_y = int(height * 0.38)
        sub_y = int(height * 0.73)
    else:
        title = "让每一张表，\n都说清自己\n代表什么。"
        title_size = int(width * 0.09)
        title_y = int(height * 0.34)
        sub_y = int(height * 0.69)

    title_font = pil_font(title_size, True)
    draw.multiline_text((pad, title_y), title, fill=INK, font=title_font, spacing=int(title_size * .28))
    sub = "有证据、可确认的业务定义"
    sub_font = fit_text(draw, sub, width - pad * 2, int(min(width, height) * .042))
    draw.text((pad, sub_y), sub, fill=MUTED, font=sub_font)
    label_font = pil_font(max(17, int(min(width, height) * .021)), True)
    label = "DEMO 1.0.0  ·  公开合成演示"
    bbox = draw.textbbox((0, 0), label, font=label_font)
    box_width = bbox[2] - bbox[0] + 34
    box_height = bbox[3] - bbox[1] + 24
    box_y = height - pad - box_height
    draw.rounded_rectangle((pad, box_y, pad + box_width, box_y + box_height), radius=9, fill=BLUE)
    draw.text((pad + 17, box_y + 8), label, fill=WHITE, font=label_font)
    image.convert("RGB").save(path, quality=94)


def make_logo_pngs() -> list[Path]:
    outputs: list[Path] = []
    with Image.open(MARK_SOURCE) as source:
        source = source.convert("RGBA")
        for size in (32, 64, 128, 256, 512):
            output = DOWNLOADS / f"contextox-mark-{size}.png"
            source.resize((size, size), Image.Resampling.LANCZOS).save(output)
            outputs.append(output)

    wordmark = Image.new("RGBA", (1200, 320), WHITE)
    draw_mark(wordmark, 64, 64, 192)
    draw = ImageDraw.Draw(wordmark)
    draw.text((292, 82), "数契", font=pil_font(94, True), fill=INK)
    draw.text((296, 188), "CONTEXTOX", font=pil_font(35, True), fill=MUTED)
    output = DOWNLOADS / "contextox-wordmark.png"
    wordmark.convert("RGB").save(output, quality=96)
    outputs.append(output)
    return outputs


def register_pdf_fonts() -> None:
    pdfmetrics.registerFont(TTFont("ContextOxRegular", FONT_REGULAR))
    pdfmetrics.registerFont(TTFont("ContextOxBold", FONT_BOLD, subfontIndex=0))


def draw_pdf_text(c: canvas.Canvas, text: str, x: float, y: float, size: float, color: str = INK, bold: bool = False) -> None:
    c.setFillColor(HexColor(color))
    c.setFont("ContextOxBold" if bold else "ContextOxRegular", size)
    c.drawString(x, y, text)


def draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, max_width: float, size: float, leading: float, color: str = INK, bold: bool = False) -> float:
    font = "ContextOxBold" if bold else "ContextOxRegular"
    lines: list[str] = []
    current = ""
    for character in text:
        if character == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + character
        if current and pdfmetrics.stringWidth(candidate, font, size) > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    c.setFillColor(HexColor(color))
    c.setFont(font, size)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def page_base(
    c: canvas.Canvas,
    width: float,
    height: float,
    number: int,
    *,
    stage: bool = False,
) -> None:
    background = INK if stage else PAPER
    grid = "#2A3C52" if stage else LINE
    c.setFillColor(HexColor(background))
    c.rect(0, 0, width, height, stroke=0, fill=1)
    c.setStrokeColor(HexColor(grid))
    c.setLineWidth(.55)
    for x in range(0, int(width), 40):
        c.line(x, 0, x, height)
    for y in range(0, int(height), 40):
        c.line(0, y, width, y)
    draw_pdf_text(
        c,
        f"{number:02d} / 11",
        width - 76,
        26,
        9,
        "#A9B8CA" if stage else MUTED,
        True,
    )


def pdf_card(c: canvas.Canvas, x: float, y: float, width: float, height: float, label: str, title: str, body: str) -> None:
    c.setFillColor(white)
    c.setStrokeColor(HexColor(LINE))
    c.rect(x, y, width, height, stroke=1, fill=1)
    c.setFillColor(HexColor(BLUE))
    c.rect(x, y + height - 3, width, 3, stroke=0, fill=1)
    draw_pdf_text(c, label, x + 20, y + height - 30, 9, BLUE, True)
    draw_pdf_text(c, title, x + 20, y + height - 68, 18, INK, True)
    draw_wrapped(c, body, x + 20, y + height - 96, width - 40, 11, 17, MUTED)


def build_deck_pdf(path: Path) -> None:
    width, height = (960, 540)
    c = canvas.Canvas(str(path), pagesize=(width, height), pageCompression=1, invariant=1)

    # 01 · Three answers
    page_base(c, width, height, 1, stage=True)
    draw_pdf_text(c, "一个真实数字问题 · 公开合成数据", 64, 472, 11, "#81B5FF", True)
    draw_pdf_text(c, "同一份订单数据，", 64, 407, 42, WHITE, True)
    draw_pdf_text(c, "华东金额到底是多少？", 64, 355, 42, WHITE, True)
    answers = [("689", "所有记录都相加"), ("440", "只统计已支付"), ("390", "已支付再扣退款")]
    for offset, (value, label) in enumerate(answers):
        x = 64 + offset * 278
        c.setFillColor(HexColor("#142840" if value != "390" else "#173F73"))
        c.setStrokeColor(HexColor("#4C6077" if value != "390" else "#4D9AFF"))
        c.rect(x, 150, 252, 132, stroke=1, fill=1)
        c.setFillColor(HexColor("#81B5FF"))
        c.rect(x, 150, 4 if value != "390" else 7, 132, stroke=0, fill=1)
        draw_pdf_text(c, value, x + 22, 213, 43, WHITE, True)
        draw_pdf_text(c, "元", x + 112, 218, 14, "#B8C8DD")
        draw_pdf_text(c, label, x + 22, 174, 11, "#B8C8DD")
    draw_wrapped(c, "三个数都能算出来。真正没说清的是，哪些算进去、怎么处理、按什么时间。", 64, 112, 810, 15, 22, "#C7D4E5")
    c.showPage()

    # 02 · The data and calculations
    page_base(c, width, height, 2)
    draw_pdf_text(c, "01 / 三个答案怎么算", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "同一份数据，三种算法", 54, 443, 30, INK, True)
    draw_pdf_text(c, "6 行公开合成订单，地区由客户表连接得到。", 54, 416, 12, MUTED)
    table_x, table_y, table_w, row_h = 54, 104, 520, 38
    headers = ["订单", "地区", "金额", "状态"]
    col_x = [table_x, table_x + 105, table_x + 205, table_x + 330]
    c.setFillColor(HexColor("#E9EEF5"))
    c.rect(table_x, table_y + row_h * 6, table_w, row_h, stroke=0, fill=1)
    for x, header in zip(col_x, headers, strict=True):
        draw_pdf_text(c, header, x + 12, table_y + row_h * 6 + 13, 9, MUTED, True)
    rows = [
        ("O001", "华东", "120.00", "paid"),
        ("O002", "华南", "80.50", "paid"),
        ("O003", "华东", "50.00", "refunded"),
        ("O004", "华东", "199.00", "pending"),
        ("O005", "华南", "60.00", "paid"),
        ("O006", "华东", "320.00", "paid"),
    ]
    for index, row in enumerate(rows):
        y = table_y + row_h * (5 - index)
        if row[3] == "refunded":
            c.setFillColor(HexColor("#FFF5F4"))
            c.rect(table_x, y, table_w, row_h, stroke=0, fill=1)
        elif row[3] == "pending":
            c.setFillColor(HexColor("#FFF8EA"))
            c.rect(table_x, y, table_w, row_h, stroke=0, fill=1)
        c.setStrokeColor(HexColor(LINE))
        c.line(table_x, y, table_x + table_w, y)
        for x, value in zip(col_x, row, strict=True):
            color = "#B8493E" if value == "refunded" else "#A76A08" if value == "pending" else INK
            draw_pdf_text(c, value, x + 12, y + 13, 10, color, value in {"paid", "refunded", "pending"})
    calculations = [
        ("算法 A · 全部相加", "120 + 50 + 199 + 320", "689 元", False),
        ("算法 B · 只算 paid", "120 + 320", "440 元", False),
        ("算法 C · paid 再扣 refunded", "120 + 320 − 50", "390 元", True),
    ]
    for index, (label, formula, value, focus) in enumerate(calculations):
        y = 296 - index * 96
        c.setFillColor(HexColor("#EEF5FF" if focus else WHITE))
        c.setStrokeColor(HexColor(BLUE if focus else LINE))
        c.rect(610, y, 296, 78, stroke=1, fill=1)
        draw_pdf_text(c, label, 628, y + 55, 8, BLUE if focus else MUTED, True)
        draw_pdf_text(c, formula, 628, y + 26, 10, INK, True)
        draw_pdf_text(c, value, 818, y + 24, 18, BLUE if focus else INK, True)
    c.showPage()

    # 03 · Hidden rules create rework
    page_base(c, width, height, 3)
    draw_pdf_text(c, "02 / 为什么总在返工", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "公式会写，规则还在每个人脑子里", 54, 436, 30, INK, True)
    draw_wrapped(c, "同一句需求，三个人可能各自补上一套没有写下来的业务规则。", 54, 406, 820, 12, 18, MUTED)
    pdf_card(c, 54, 126, 210, 210, "一句需求", "按地区统计订单金额", "听起来已经很清楚")
    branch_rows = [("业务", "把待支付也算进去", "689"), ("分析师", "只看已支付订单", "440"), ("财务", "已支付还要扣退款", "390")]
    for index, (owner, rule, value) in enumerate(branch_rows):
        y = 280 - index * 70
        c.setFillColor(white)
        c.setStrokeColor(HexColor(LINE))
        c.rect(298, y, 330, 56, stroke=1, fill=1)
        draw_pdf_text(c, owner, 314, y + 21, 9, MUTED)
        draw_pdf_text(c, rule, 378, y + 20, 12, INK, True)
        draw_pdf_text(c, value, 577, y + 18, 17, BLUE, True)
    c.setFillColor(HexColor("#FFF9ED"))
    c.setStrokeColor(HexColor("#D6B46D"))
    c.rect(662, 126, 244, 210, stroke=1, fill=1)
    draw_pdf_text(c, "最后的问题", 682, 300, 9, "#A76A08", True)
    draw_wrapped(c, "谁算错了？依据在哪里？下次还用哪套？", 682, 254, 202, 16, 25, INK, True)
    c.showPage()

    # 04 · Product introduction
    page_base(c, width, height, 4, stage=True)
    c.drawImage(ImageReader(str(MARK_SOURCE)), 64, 402, width=68, height=68, mask="auto")
    draw_pdf_text(c, "数契 ContextOx", 64, 368, 11, "#81B5FF", True)
    draw_pdf_text(c, "先把业务意思说清楚，", 64, 308, 38, WHITE, True)
    draw_pdf_text(c, "再开始交付", 64, 260, 38, WHITE, True)
    promises = [
        "把资料放进来，说出你想弄清楚的问题",
        "Agent 找出处，把会改变结果的问题问出来",
        "人确认关键规则，留下可以回看的候选定义",
    ]
    for index, text in enumerate(promises, start=1):
        y = 184 - (index - 1) * 42
        draw_pdf_text(c, f"0{index}", 66, y, 10, "#63A4FF", True)
        draw_pdf_text(c, text, 112, y, 15, "#CFDAEA")
        c.setStrokeColor(HexColor("#34475E"))
        c.line(64, y - 14, 868, y - 14)
    c.showPage()

    # 05 · Select sources and state the goal
    page_base(c, width, height, 5)
    draw_pdf_text(c, "03 / 第一步", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "告诉它，这次看哪些资料", 54, 443, 28, INK, True)
    draw_pdf_text(c, "讲解模拟 · 对应当前 Workbench", 714, 454, 8, MUTED, True)
    frame_x, frame_y, frame_w, frame_h = 54, 72, 852, 330
    c.setFillColor(white)
    c.setStrokeColor(HexColor("#B9C5D2"))
    c.rect(frame_x, frame_y, frame_w, frame_h, stroke=1, fill=1)
    c.setFillColor(HexColor("#F7F9FC"))
    c.rect(frame_x, frame_y, 154, frame_h, stroke=0, fill=1)
    c.rect(frame_x + 154, frame_y, 420, frame_h, stroke=0, fill=1)
    c.setStrokeColor(HexColor(LINE))
    c.line(frame_x + 154, frame_y, frame_x + 154, frame_y + frame_h)
    c.line(frame_x + 574, frame_y, frame_x + 574, frame_y + frame_h)
    draw_pdf_text(c, "数契", frame_x + 22, frame_y + 294, 15, INK, True)
    draw_pdf_text(c, "资料库", frame_x + 18, frame_y + 248, 8, MUTED, True)
    for index, name in enumerate(("✓ orders.csv", "✓ customers.csv", "✓ notes.md")):
        y = frame_y + 210 - index * 38
        c.setFillColor(HexColor("#EDF4FE"))
        c.rect(frame_x + 14, y, 126, 27, stroke=0, fill=1)
        draw_pdf_text(c, name, frame_x + 23, y + 9, 8, "#205FA8", True)
    draw_pdf_text(c, "当前进度", frame_x + 176, frame_y + 296, 8, BLUE, True)
    draw_pdf_text(c, "从一个问题开始", frame_x + 176, frame_y + 266, 20, INK, True)
    steps = ["1 明确目标", "2 理解资料", "3 澄清规则", "4 整理成果"]
    for index, step in enumerate(steps):
        draw_pdf_text(c, step, frame_x + 176 + index * 96, frame_y + 231, 7, BLUE if index == 0 else MUTED, index == 0)
    draw_pdf_text(c, "3 份资料进入本轮", frame_x + 176, frame_y + 174, 12, INK, True)
    draw_wrapped(c, "订单、客户和说明文档会一起进入这一轮分析。", frame_x + 176, frame_y + 148, 360, 9, 14, MUTED)
    draw_pdf_text(c, "数契 Agent", frame_x + 592, frame_y + 296, 11, INK, True)
    draw_pdf_text(c, "你想从这三份资料里弄清什么？", frame_x + 592, frame_y + 244, 10, MUTED)
    c.setStrokeColor(HexColor("#BFCBD7"))
    c.rect(frame_x + 592, frame_y + 34, 236, 104, stroke=1, fill=0)
    draw_pdf_text(c, "我想按地区统计订单金额。", frame_x + 606, frame_y + 103, 10, INK)
    c.setFillColor(HexColor(BLUE))
    c.rect(frame_x + 766, frame_y + 48, 48, 25, stroke=0, fill=1)
    draw_pdf_text(c, "发送", frame_x + 779, frame_y + 57, 8, WHITE, True)
    c.showPage()

    # 06 · Questions that change the answer
    page_base(c, width, height, 6)
    draw_pdf_text(c, "04 / 第二步", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "Agent 把会改变结果的问题问出来", 54, 443, 28, INK, True)
    draw_pdf_text(c, "每个问题都说明为什么现在需要确认。", 54, 414, 11, MUTED)
    c.setFillColor(white)
    c.setStrokeColor(HexColor(LINE))
    c.rect(54, 90, 298, 280, stroke=1, fill=1)
    draw_pdf_text(c, "资料里已经找到", 76, 339, 9, BLUE, True)
    findings = ["订单状态：paid、refunded、pending", "连接字段：customer_id", "时间字段：paid_at"]
    for index, finding in enumerate(findings):
        draw_wrapped(c, finding, 76, 290 - index * 58, 250, 11, 16, INK, index == 1)
    questions = [
        ("01", "待支付订单要算进去吗？", "影响华东的 199 元", "统计范围"),
        ("02", "退款订单怎么处理？", "排除和扣减会得到不同答案", "金额规则"),
        ("03", "按哪个时间归属？", "支付时间会改变统计范围", "时间范围"),
    ]
    for index, (number, title, body, effect) in enumerate(questions):
        y = 292 - index * 94
        c.setFillColor(white)
        c.rect(382, y, 524, 78, stroke=1, fill=1)
        draw_pdf_text(c, number, 400, y + 29, 11, BLUE, True)
        draw_pdf_text(c, title, 446, y + 45, 13, INK, True)
        draw_pdf_text(c, body, 446, y + 23, 9, MUTED)
        c.setFillColor(HexColor("#FFF0CF"))
        c.rect(818, y + 24, 70, 24, stroke=0, fill=1)
        draw_pdf_text(c, effect, 829, y + 32, 7, "#A76A08", True)
    c.showPage()

    # 07 · Human review
    page_base(c, width, height, 7)
    draw_pdf_text(c, "05 / 第三步", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "人用大白话做决定", 54, 443, 28, INK, True)
    c.setFillColor(HexColor("#F5F7FA"))
    c.setStrokeColor(HexColor(LINE))
    c.rect(54, 86, 292, 316, stroke=1, fill=1)
    draw_pdf_text(c, "等待确认", 76, 365, 9, "#A76A08", True)
    draw_wrapped(c, "我把你的回答整理成了 3 条规则", 76, 322, 240, 18, 25, INK, True)
    draw_pdf_text(c, "确认后会更新", 76, 204, 8, MUTED)
    draw_pdf_text(c, "地区净订单金额", 76, 174, 12, INK, True)
    draw_pdf_text(c, "订单与客户关系", 76, 148, 12, INK, True)
    rules = [
        ("纳入哪些订单", "只统计已支付订单", "pending 不进入金额"),
        ("退款怎么处理", "从原订单地区的已支付金额中扣除", "refunded 作为扣减项"),
        ("按哪个时间", "按支付时间归属", "未支付订单没有支付时间"),
    ]
    for index, (label, answer, note) in enumerate(rules):
        y = 298 - index * 82
        c.setFillColor(white)
        c.rect(372, y, 534, 68, stroke=1, fill=1)
        draw_pdf_text(c, label, 390, y + 39, 8, MUTED)
        draw_pdf_text(c, answer, 498, y + 38, 11, INK, True)
        draw_pdf_text(c, note, 498, y + 17, 8, MUTED)
    c.setFillColor(HexColor(BLUE))
    c.rect(792, 102, 114, 34, stroke=0, fill=1)
    draw_pdf_text(c, "确认并继续", 813, 114, 9, WHITE, True)
    c.showPage()

    # 08 · Candidate definition update
    page_base(c, width, height, 8)
    draw_pdf_text(c, "06 / 回答带来的变化", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "回答之后，定义真的变了", 54, 443, 28, INK, True)
    draw_pdf_text(c, "390 元只解释规则差异；产品画面停在候选定义。", 54, 414, 11, MUTED)
    c.setFillColor(HexColor("#F0F6FF"))
    c.setStrokeColor(HexColor(LINE))
    c.rect(54, 84, 340, 304, stroke=1, fill=1)
    draw_pdf_text(c, "本轮更新", 76, 352, 9, BLUE, True)
    draw_pdf_text(c, "地区净订单金额", 76, 300, 24, INK, True)
    draw_wrapped(c, "按客户地区汇总已支付金额，再扣除同一地区的退款金额。", 76, 264, 286, 11, 17, MUTED)
    draw_pdf_text(c, "1 个字段候选", 76, 158, 10, BLUE, True)
    draw_pdf_text(c, "1 条关系候选", 190, 158, 10, BLUE, True)
    draw_pdf_text(c, "3 条已确认规则", 76, 124, 10, BLUE, True)
    result_rows = [
        ("纳入范围", "status = paid", "已确认"),
        ("退款处理", "从原订单地区金额中扣除", "已确认"),
        ("时间归属", "paid_at · 北京时间", "已确认"),
        ("资料关系", "orders.customer_id = customers.customer_id", "有出处"),
    ]
    for index, (label, value, status) in enumerate(result_rows):
        y = 319 - index * 58
        c.setStrokeColor(HexColor(LINE))
        c.line(426, y - 12, 906, y - 12)
        draw_pdf_text(c, label, 426, y + 10, 8, MUTED)
        draw_pdf_text(c, value, 516, y + 9, 10, INK, True)
        draw_pdf_text(c, status, 852, y + 9, 8, "#147D64", True)
    c.setFillColor(HexColor(PAPER))
    c.rect(426, 100, 480, 44, stroke=0, fill=1)
    draw_pdf_text(c, "notes.md · 第 7 行：纳入状态、退款和时间需要业务确认", 442, 116, 8, MUTED)
    c.showPage()

    # 09 · Readback chain
    page_base(c, width, height, 9)
    draw_pdf_text(c, "07 / 留下来的工作记录", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "留下的不只是一段 AI 回复", 54, 443, 28, INK, True)
    draw_pdf_text(c, "换个人接手，也能知道结论从哪里来，还有什么没解决。", 54, 414, 11, MUTED)
    chain = [
        ("01", "本轮资料", "3 份公开合成文件"),
        ("02", "为什么追问", "哪些选择会改变结果"),
        ("03", "谁做了决定", "人的回答与确认"),
        ("04", "这一轮改了什么", "字段、关系和规则"),
        ("05", "还不知道什么", "未知事项继续保留"),
    ]
    for index, (number, label, text) in enumerate(chain):
        x = 54 + index * 170
        c.setFillColor(white)
        c.setStrokeColor(HexColor(LINE))
        c.rect(x, 154, 154, 196, stroke=1, fill=1)
        c.circle(x + 28, 318, 14, stroke=1, fill=0)
        draw_pdf_text(c, number, x + 20, 315, 8, BLUE, True)
        draw_pdf_text(c, label, x + 18, 254, 8, MUTED)
        draw_wrapped(c, text, x + 18, 224, 118, 12, 18, INK, True)
    c.setFillColor(HexColor(INK))
    c.rect(54, 88, 834, 44, stroke=0, fill=1)
    draw_pdf_text(c, "复核时不用重猜", 74, 104, 11, WHITE, True)
    draw_pdf_text(c, "出处、回答和变化都能沿着同一条记录往回看", 576, 104, 9, "#CBD5E1")
    c.showPage()

    # 10 · Capabilities and boundaries
    page_base(c, width, height, 10)
    draw_pdf_text(c, "08 / Demo 1.0.0", 54, 485, 10, BLUE, True)
    draw_pdf_text(c, "现在能做什么，也明确不能做什么", 54, 443, 28, INK, True)
    columns = [
        (54, "当前可以体验", "#E9F7F2", "#147D64", [
            ("和 Agent 连续对话", "不用先理解复杂运行概念"),
            ("明确本轮资料范围", "只依据选中的公开或本地资料"),
            ("追问并确认关键规则", "人的决定不会被悄悄代替"),
            ("查看候选结果和出处", "变化与未知事项可以回看"),
        ]),
        (494, "当前不能这样理解", "#FFF0CF", "#A76A08", [
            ("候选不等于正式批准", "关键业务定义仍需要人的审核"),
            ("不会写入生产数据库", "Demo 不执行 SQL 或外部业务写入"),
            ("没有云端多人协作", "当前工作区和状态保存在本机"),
            ("客户价值仍需真实验证", "工程检查不代替业务效果"),
        ]),
    ]
    for x, label, tag_bg, tag_color, items in columns:
        c.setFillColor(white)
        c.setStrokeColor(HexColor(LINE))
        c.rect(x, 80, 412, 318, stroke=1, fill=1)
        c.setFillColor(HexColor(tag_bg))
        c.rect(x + 22, 350, 122, 25, stroke=0, fill=1)
        draw_pdf_text(c, label, x + 34, 359, 8, tag_color, True)
        for index, (title, body) in enumerate(items):
            y = 306 - index * 62
            draw_pdf_text(c, title, x + 24, y, 11, INK, True)
            draw_pdf_text(c, body, x + 24, y - 20, 8, MUTED)
            c.setStrokeColor(HexColor(LINE))
            c.line(x + 24, y - 31, x + 388, y - 31)
    c.showPage()

    # 11 · CTA
    page_base(c, width, height, 11, stage=True)
    c.drawImage(ImageReader(str(MARK_SOURCE)), 64, 385, width=86, height=86, mask="auto")
    draw_pdf_text(c, "数契 ContextOx · Demo 1.0.0", 64, 348, 11, "#81B5FF", True)
    draw_pdf_text(c, "带一个总对不上的业务口径，", 64, 286, 36, WHITE, True)
    draw_pdf_text(c, "和数契一起把它说清楚", 64, 240, 36, WHITE, True)
    draw_wrapped(c, "可以先用公开合成示例体验，也可以带一组脱敏资料试一轮。", 64, 192, 760, 14, 21, "#C7D4E5")
    c.setFillColor(white)
    c.rect(64, 105, 210, 42, stroke=0, fill=1)
    draw_pdf_text(c, "查看 45 秒产品演示", 88, 121, 10, INK, True)
    draw_pdf_text(c, "github.com/archerthegoat/contextox-agent", 310, 120, 10, "#81B5FF", True)
    draw_pdf_text(c, "公开源码 · 公开合成 Demo · 候选结果由人核对", 64, 62, 9, "#A9B8CA")
    c.showPage()
    c.save()


def build_one_pager(path: Path) -> None:
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4, pageCompression=1, invariant=1)
    c.setFillColor(HexColor(PAPER))
    c.rect(0, 0, width, height, stroke=0, fill=1)
    c.setFillColor(HexColor(BLUE))
    c.rect(0, height - 18, width, 18, stroke=0, fill=1)
    draw_pdf_text(c, "数契 ContextOx · Demo 1.0.0", 46, height - 72, 16, BLUE, True)
    draw_pdf_text(c, "让每一张表，", 46, height - 136, 36, INK, True)
    draw_pdf_text(c, "都说清自己代表什么。", 46, height - 180, 36, INK, True)
    draw_wrapped(c, "把表格、说明和人的判断，整理成有证据、可确认的业务定义。", 46, height - 222, width - 92, 15, 23, MUTED)

    draw_pdf_text(c, "为什么", 46, height - 292, 12, BLUE, True)
    draw_wrapped(c, "同一个业务目标，真正决定结果的退款、缺失和时间口径，往往散落在资料与人的经验里。", 46, height - 320, width - 92, 12, 19, INK)
    draw_pdf_text(c, "怎么工作", 46, height - 402, 12, BLUE, True)
    steps = ["1 选择本轮资料", "2 说出业务目标", "3 回答并确认口径", "4 核对候选定义与出处"]
    for i, step in enumerate(steps):
        x = 46 + (i % 2) * 254
        y = height - 442 - (i // 2) * 54
        c.setFillColor(white)
        c.setStrokeColor(HexColor(LINE))
        c.rect(x, y, 230, 38, stroke=1, fill=1)
        draw_pdf_text(c, step, x + 14, y + 13, 11, INK, True)

    draw_pdf_text(c, "当前边界", 46, height - 574, 12, BLUE, True)
    draw_wrapped(c, "当前结果是可核对的候选业务定义；正式 Contract 发布与跨任务批准知识复用属于后续阶段。工作区和状态保存在本机；模型调用只包含本轮明确范围。", 46, height - 602, width - 92, 11, 18, MUTED)
    c.drawImage(ImageReader(str(ASSETS / "demo-real.jpg")), 46, 28, width=285, height=160, preserveAspectRatio=True, anchor="c")
    c.setFillColor(HexColor(INK))
    c.rect(208, 162, 123, 24, stroke=0, fill=1)
    draw_pdf_text(c, "公开合成演示", 220, 169, 8, WHITE, True)
    draw_pdf_text(c, "项目与源码", 360, 118, 9, MUTED, True)
    draw_pdf_text(c, "github.com/archerthegoat/", 360, 88, 10, BLUE, True)
    draw_pdf_text(c, "contextox-agent", 360, 68, 10, BLUE, True)
    c.save()


def deterministic_zip(path: Path, files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ensure_dirs()
    register_pdf_fonts()

    cards = [
        (DOWNLOADS / "contextox-social-1200x630.png", (1200, 630), "landscape"),
        (DOWNLOADS / "contextox-stage-1920x1080.png", (1920, 1080), "landscape"),
        (DOWNLOADS / "contextox-portrait-1080x1440.png", (1080, 1440), "portrait"),
        (DOWNLOADS / "contextox-square-1080x1080.png", (1080, 1080), "portrait"),
    ]
    for path, size, layout in cards:
        make_card(path, size, layout)
    logo_pngs = make_logo_pngs()

    deck_pdf = PDF_DIR / "contextox-demo-1.0.0-presentation.pdf"
    one_pager = PDF_DIR / "contextox-demo-1.0.0-one-pager.pdf"
    build_deck_pdf(deck_pdf)
    build_one_pager(one_pager)
    shutil.copy2(deck_pdf, DOWNLOADS / deck_pdf.name)
    shutil.copy2(one_pager, DOWNLOADS / one_pager.name)
    video_source = ROOT / "output" / "video" / "contextox-demo-1.0.0-product-film.mp4"
    if not video_source.is_file():
        raise FileNotFoundError(f"validated product film missing: {video_source}")
    video_download = DOWNLOADS / video_source.name
    shutil.copy2(video_source, video_download)
    (DOWNLOADS / "contextox-demo-1.0.0-silent-launch.mp4").unlink(missing_ok=True)

    kit_files = [
        (ASSETS / "contextox-mark.svg", "logo/contextox-mark.svg"),
        (ASSETS / "contextox-mark-white.svg", "logo/contextox-mark-white.svg"),
        (ASSETS / "contextox-wordmark.svg", "logo/contextox-wordmark.svg"),
        (ROOT / "docs" / "brand" / "品牌与展示规范.md", "品牌与展示规范.md"),
    ]
    kit_files.extend((path, f"logo/{path.name}") for path in logo_pngs)
    deterministic_zip(DOWNLOADS / "contextox-brand-kit.zip", kit_files)

    manifest_paths = [path for path, _, _ in cards] + [
        ASSETS / "contextox-product-film-poster.png",
    ] + logo_pngs + [
        DOWNLOADS / deck_pdf.name,
        DOWNLOADS / one_pager.name,
        DOWNLOADS / "contextox-brand-kit.zip",
        video_download,
    ]
    manifest = {
        "version": "Demo 1.0.0",
        "generated_by": "scripts/build_showcase_assets.py",
        "files": [
            {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha(path)}
            for path in manifest_paths
        ],
    }
    (DOWNLOADS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"built {len(manifest_paths)} showcase artifacts")


if __name__ == "__main__":
    main()
