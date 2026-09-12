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


def page_base(c: canvas.Canvas, width: float, height: float, number: int) -> None:
    c.setFillColor(HexColor(PAPER))
    c.rect(0, 0, width, height, stroke=0, fill=1)
    c.setStrokeColor(HexColor(LINE))
    c.setLineWidth(.55)
    for x in range(0, int(width), 40):
        c.line(x, 0, x, height)
    for y in range(0, int(height), 40):
        c.line(0, y, width, y)
    draw_pdf_text(c, f"{number:02d} / 08", width - 76, 26, 9, MUTED, True)


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
    slides = [
        ("CONTEXTOX · DEMO 1.0.0", "让每一张表，\n都说清自己代表什么。", "把表格、说明和人的判断，整理成有证据、可确认的业务定义。"),
        ("01 / 一个看似简单的需求", "“我想按地区统计订单金额。”", "退款、缺失和时间，都会改变答案。"),
        ("02 / 数契的方法", "Agent 找证据，\n人确认业务事实。", "对话是入口；资料、人的决定和仍未知事项各自保持身份。"),
        ("03 / 资料与证据", "结论可以回到出处。", "技术观察、关系候选和业务决定分别呈现；证据不足时保持未知。"),
        ("04 / 人参与确认", "真正会改变结果的问题，\n交给人决定。", "说明为什么要问，整理候选回答，并把确认绑定精确版本。"),
        ("05 / 确认后的变化", "不是多一段回复，\n而是定义真的发生变化。", "退款规则、缺失处理与仍未知事项，都可在候选定义中回读。"),
        ("06 / 当前与下一步", "先把候选说清楚，\n再让它进入交付。", "当前是 Demo 1.0.0；下一步是正式定义交付和批准知识复用。"),
        ("07 / CONTEXTOX", "带一个真实的问题，\n和数契一起把它说清楚。", "公开源码与合成 Demo 已发布。欢迎提供脱敏案例或交互建议。"),
    ]
    for index, (kicker, title, body) in enumerate(slides, start=1):
        page_base(c, width, height, index)
        draw_pdf_text(c, kicker, 64, 468, 11, BLUE, True)
        y = 405
        for line in title.split("\n"):
            draw_pdf_text(c, line, 64, y, 48 if index != 1 else 52, INK, True)
            y -= 62
        draw_wrapped(c, body, 64, y - 8, 760, 18, 27, MUTED)

        if index == 2:
            labels = [("退款", "应该扣除吗？"), ("缺失", "应该归零吗？"), ("时间", "按什么时间？")]
            for offset, (label, card_title) in enumerate(labels):
                pdf_card(c, 64 + offset * 286, 66, 260, 145, label, card_title, "不同选择会产生不同业务结果。")
        elif index == 4:
            c.drawImage(ImageReader(str(ASSETS / "demo-preview.jpg")), 500, 68, width=390, height=219, preserveAspectRatio=True, anchor="c")
            c.setFillColor(HexColor(INK))
            c.rect(500, 274, 118, 23, stroke=0, fill=1)
            draw_pdf_text(c, "公开合成演示", 510, 280, 8, WHITE, True)
        elif index == 6:
            pdf_card(c, 64, 64, 370, 160, "确认前", "退款规则：未知", "缺失金额：未知\n表间关系：候选待补充")
            pdf_card(c, 526, 64, 370, 160, "确认后", "扣除同地区退款", "缺失不计入净额\n仍需确认时间范围")
            draw_pdf_text(c, "→", 468, 125, 34, BLUE, True)
        elif index == 7:
            states = [("CURRENT", "Demo 1.0.0"), ("NEXT", "正式定义交付"), ("THEN", "批准知识复用")]
            for offset, (state, card_title) in enumerate(states):
                pdf_card(c, 64 + offset * 286, 70, 260, 140, state, card_title, "每一步都有独立合同和验收证据。")
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
    video_source = ROOT / "output" / "video" / "contextox-demo-1.0.0-silent-launch.mp4"
    video_download = DOWNLOADS / video_source.name
    if video_source.is_file():
        shutil.copy2(video_source, video_download)

    kit_files = [
        (ASSETS / "contextox-mark.svg", "logo/contextox-mark.svg"),
        (ASSETS / "contextox-mark-white.svg", "logo/contextox-mark-white.svg"),
        (ASSETS / "contextox-wordmark.svg", "logo/contextox-wordmark.svg"),
        (ROOT / "docs" / "brand" / "品牌与展示规范.md", "品牌与展示规范.md"),
    ]
    kit_files.extend((path, f"logo/{path.name}") for path in logo_pngs)
    deterministic_zip(DOWNLOADS / "contextox-brand-kit.zip", kit_files)

    manifest_paths = [path for path, _, _ in cards] + logo_pngs + [
        deck_pdf,
        one_pager,
        DOWNLOADS / "contextox-brand-kit.zip",
    ]
    if video_source.is_file():
        manifest_paths.append(video_source)
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
