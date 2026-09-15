#!/usr/bin/env python3
"""Validate the static ContextOx showcase package without third-party deps."""

from __future__ import annotations

import csv
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DEMO = ROOT / "src" / "contextox" / "demo"


class ShowcaseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.slides = 0
        self.menu_items = 0
        self.ids: set[str] = set()
        self.videos: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for key in ("href", "src"):
            value = values.get(key)
            if value:
                self.references.append(value)
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
        classes = set((values.get("class") or "").split())
        if tag == "section" and "slide" in classes:
            self.slides += 1
        if tag == "button" and "data-slide-index" in values:
            self.menu_items += 1
        if tag == "video":
            self.videos.append(values)


def compute_east_china_totals() -> tuple[Decimal, Decimal, Decimal]:
    with (DEMO / "customers.csv").open(encoding="utf-8", newline="") as handle:
        regions = {
            row["customer_id"]: row["region"]
            for row in csv.DictReader(handle)
        }
    with (DEMO / "orders.csv").open(encoding="utf-8", newline="") as handle:
        east_orders = [
            row
            for row in csv.DictReader(handle)
            if regions.get(row["customer_id"]) == "华东"
        ]

    all_records = sum((Decimal(row["amount"]) for row in east_orders), Decimal("0"))
    paid_only = sum(
        (Decimal(row["amount"]) for row in east_orders if row["status"] == "paid"),
        Decimal("0"),
    )
    refunded = sum(
        (
            Decimal(row["amount"])
            for row in east_orders
            if row["status"] == "refunded"
        ),
        Decimal("0"),
    )
    return all_records, paid_only, paid_only - refunded


def main() -> int:
    errors: list[str] = []
    required = {
        "index.html",
        "index-en.html",
        "presentation.html",
        "presentation-en.html",
        "presentation.js",
        "styles.css",
        "assets/contextox-mark.svg",
        "assets/contextox-mark-white.svg",
        "assets/contextox-wordmark.svg",
        "assets/favicon.svg",
        "assets/demo-flow.webp",
        "assets/demo-real.jpg",
        "assets/demo-preview.jpg",
        "assets/contextox-product-film-poster.png",
        "downloads/contextox-demo-1.0.0-product-film.mp4",
        "downloads/contextox-demo-1.0.0-presentation.pdf",
        "downloads/contextox-demo-1.0.0-presentation-en.pdf",
        "downloads/contextox-demo-1.0.0-one-pager-en.pdf",
        "downloads/contextox-demo-1.0.0-product-film-en.mp4",
        "downloads/manifest.json",
    }
    for relative in sorted(required):
        if not (SITE / relative).is_file():
            errors.append(f"missing required file: site/{relative}")

    parsed: dict[str, ShowcaseParser] = {}
    for name in ("index.html", "index-en.html", "presentation.html", "presentation-en.html"):
        path = SITE / name
        if not path.is_file():
            continue
        parser = ShowcaseParser()
        text = path.read_text(encoding="utf-8")
        parser.feed(text)
        parsed[name] = parser
        for reference in parser.references:
            if reference.startswith(("https://", "http://", "mailto:", "tel:", "data:", "#")):
                continue
            reference_parts = urlsplit(reference)
            file_part = reference_parts.path
            fragment = reference_parts.fragment
            target = (path.parent / file_part).resolve() if file_part else path
            if not target.is_file():
                errors.append(f"broken reference in {name}: {reference}")
                continue
            if fragment and target.suffix == ".html":
                target_parser = parsed.get(target.name)
                if target_parser is None:
                    target_parser = ShowcaseParser()
                    target_parser.feed(target.read_text(encoding="utf-8"))
                    parsed[target.name] = target_parser
                if fragment.isdigit():
                    if int(fragment) < 1 or int(fragment) > target_parser.slides:
                        errors.append(f"out-of-range slide reference in {name}: {reference}")
                elif fragment not in target_parser.ids:
                    errors.append(f"missing fragment in {name}: {reference}")

    for name in ("presentation.html", "presentation-en.html"):
        deck = parsed.get(name)
        if deck:
            if deck.slides != 12:
                errors.append(f"{name} must contain 12 slides, found {deck.slides}")
            if deck.menu_items != deck.slides:
                errors.append(
                    f"{name} menu/slide mismatch: {deck.menu_items}/{deck.slides}"
                )
        deck_text = (SITE / name).read_text(encoding="utf-8")
        if "data-language-switch" not in deck_text:
            errors.append(f"{name} must expose the browser language switch")

    entry_text = (SITE / "index.html").read_text(encoding="utf-8")
    if "presentation.html#1" not in entry_text:
        errors.append("root entry must open presentation.html#1")
    if "index-en.html" not in entry_text:
        errors.append("root entry must expose the English entrypoint")
    if "下载物料" in entry_text or "download-grid" in entry_text:
        errors.append("root entry still exposes the retired material download center")
    entry = parsed.get("index.html")
    if entry and entry.slides:
        errors.append("root entry must stay a technical handoff, not a second presentation")
    english_entry_text = (SITE / "index-en.html").read_text(encoding="utf-8")
    if "presentation-en.html#1" not in english_entry_text:
        errors.append("English entry must open presentation-en.html#1")
    english_entry = parsed.get("index-en.html")
    if english_entry and english_entry.slides:
        errors.append("English entry must stay a technical handoff, not a second presentation")

    all_copy = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SITE / "index.html", SITE / "presentation.html")
        if path.is_file()
    )
    for forbidden in ("完全离线", "生产可用", "生产级", "客户案例", "正式 Contract 已发布"):
        if forbidden in all_copy:
            errors.append(f"forbidden unverified claim found: {forbidden}")
    for required_copy in ("Demo 1.0.0", "公开合成", "候选"):
        if required_copy not in all_copy:
            errors.append(f"required disclosure missing: {required_copy}")
    for required_presentation_copy in (
        "同一份订单数据，",
        "为什么会有",
        "三个答案？",
        "不同工具解决不同阶段的问题",
        "通用 Agent，如 Codex",
        "Atlan、DataHub",
        "数契当前聚焦",
        "联系 @archerthegoat",
        "离线阅读 12 页 PDF",
    ):
        if required_presentation_copy not in (SITE / "presentation.html").read_text(encoding="utf-8"):
            errors.append(f"presentation V2.1 copy missing: {required_presentation_copy}")

    english_presentation_text = (SITE / "presentation-en.html").read_text(encoding="utf-8")
    for required_english_copy in (
        "The same order data",
        "three answers",
        "Public synthetic data",
        "General Agent, such as Codex",
        "Atlan, DataHub",
        "ContextOx's current bet",
        "candidate definition",
        "Chinese",
    ):
        if required_english_copy not in english_presentation_text:
            errors.append(f"English presentation copy missing: {required_english_copy}")
    if 'lang="en"' not in english_presentation_text:
        errors.append("English presentation must declare lang=\"en\"")
    if "Switch to Chinese" not in english_presentation_text:
        errors.append("English presentation language switch label missing")

    legacy_video = SITE / "downloads" / "contextox-demo-1.0.0-silent-launch.mp4"
    if legacy_video.exists():
        errors.append("legacy silent-launch video remains in the distribution package")
    if "silent-launch" in all_copy:
        errors.append("legacy silent-launch reference remains in showcase copy")

    totals = compute_east_china_totals()
    expected_totals = (Decimal("689.00"), Decimal("440.00"), Decimal("390.00"))
    if totals != expected_totals:
        errors.append(
            "public demo arithmetic drift: "
            f"expected {expected_totals}, calculated {totals}"
        )
    for value in expected_totals:
        visible_value = f"{value:.0f}"
        if visible_value not in all_copy:
            errors.append(f"public demo total missing from showcase copy: {visible_value}")

    manifest_path = SITE / "downloads" / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_entries = manifest.get("files", [])
            manifest_names = {entry.get("path") for entry in manifest_entries}
            expected_video_path = "site/downloads/contextox-demo-1.0.0-product-film.mp4"
            if expected_video_path not in manifest_names:
                errors.append("product film missing from manifest")
            for english_path in (
                "site/downloads/contextox-demo-1.0.0-presentation-en.pdf",
                "site/downloads/contextox-demo-1.0.0-one-pager-en.pdf",
                "site/downloads/contextox-demo-1.0.0-product-film-en.mp4",
            ):
                if english_path not in manifest_names:
                    errors.append(f"English showcase artifact missing from manifest: {english_path}")
            if any("silent-launch" in str(name) for name in manifest_names):
                errors.append("legacy silent-launch video remains in manifest")
            for entry in manifest_entries:
                relative = entry.get("path")
                if not isinstance(relative, str):
                    errors.append("manifest entry has no path")
                    continue
                artifact = ROOT / relative
                if not artifact.is_file():
                    errors.append(f"manifest target missing: {relative}")
                    continue
                if artifact.stat().st_size != entry.get("bytes"):
                    errors.append(f"manifest byte count drift: {relative}")
                digest = sha256(artifact.read_bytes()).hexdigest()
                if digest != entry.get("sha256"):
                    errors.append(f"manifest sha256 drift: {relative}")
        except (json.JSONDecodeError, OSError) as error:
            errors.append(f"manifest cannot be read: {error}")

    if errors:
        print("showcase validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("showcase validation: PASS")
    print("- root entry: opens the dynamic presentation without a material download center")
    print("- presentation: 12 slides and 12 menu entries")
    print("- English presentation: 12 slides, language switch and English downloads")
    print("- positioning: Codex, Atlan/DataHub, BI and ContextOx roles are explicit")
    print("- public demo arithmetic: 华东 689 / 440 / 390")
    print("- product film: playable artifact link and no legacy file")
    print("- manifest: byte counts and SHA-256 values verified")
    print("- asset policy: no missing local dependencies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
