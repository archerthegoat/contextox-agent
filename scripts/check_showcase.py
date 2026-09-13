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
        "presentation.html",
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
        "downloads/manifest.json",
    }
    for relative in sorted(required):
        if not (SITE / relative).is_file():
            errors.append(f"missing required file: site/{relative}")

    parsed: dict[str, ShowcaseParser] = {}
    for name in ("index.html", "presentation.html"):
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

    deck = parsed.get("presentation.html")
    if deck:
        if deck.slides != 11:
            errors.append(f"presentation must contain 11 slides, found {deck.slides}")
        if deck.menu_items != deck.slides:
            errors.append(
                f"presentation menu/slide mismatch: {deck.menu_items}/{deck.slides}"
            )

    landing = parsed.get("index.html")
    if landing:
        if len(landing.videos) != 1:
            errors.append(f"landing page must contain one product video, found {len(landing.videos)}")
        else:
            video = landing.videos[0]
            if "controls" not in video:
                errors.append("landing product video must expose controls")
            if "autoplay" in video:
                errors.append("landing product video must not autoplay")
            if video.get("poster") != "assets/contextox-product-film-poster.png":
                errors.append("landing product video poster is not the V2 Workbench frame")

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
    for required_landing_copy in (
        "同一份订单数据，为什么会有三个答案？",
        "看 45 秒产品演示",
        "打开动态介绍",
        "45 秒无旁白产品片",
        "11 页动态 HTML",
        "11 页演示 PDF",
    ):
        if required_landing_copy not in (SITE / "index.html").read_text(encoding="utf-8"):
            errors.append(f"landing V2 copy missing: {required_landing_copy}")

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
    print("- landing page: references and disclosures valid")
    print("- presentation: 11 slides and 11 menu entries")
    print("- public demo arithmetic: 华东 689 / 440 / 390")
    print("- product film: controls, no autoplay, V2 poster and no legacy file")
    print("- manifest: byte counts and SHA-256 values verified")
    print("- asset policy: no missing local dependencies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
