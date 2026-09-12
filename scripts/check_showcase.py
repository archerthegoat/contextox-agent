#!/usr/bin/env python3
"""Validate the static ContextOx showcase package without third-party deps."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class ShowcaseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.slides = 0
        self.menu_items = 0
        self.ids: set[str] = set()

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
            if reference.startswith(("https://", "http://", "mailto:", "#")):
                continue
            file_part, _, fragment = reference.partition("#")
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
        if deck.slides != 8:
            errors.append(f"presentation must contain 8 slides, found {deck.slides}")
        if deck.menu_items != deck.slides:
            errors.append(
                f"presentation menu/slide mismatch: {deck.menu_items}/{deck.slides}"
            )

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

    if errors:
        print("showcase validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("showcase validation: PASS")
    print("- landing page: references and disclosures valid")
    print("- presentation: 8 slides and 8 menu entries")
    print("- asset policy: no missing local dependencies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
