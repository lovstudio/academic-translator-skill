#!/usr/bin/env python3
"""Compare source and translated Markdown structure for paper-translation delivery."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")
MATH_BLOCK = re.compile(r"(?ms)^\$\$.*?^\$\$|^\\\[.*?^\\\]")


def inventory(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    headings = [match.group(2) for match in HEADING.finditer(text)]
    images = IMAGE.findall(text)
    links = LINK.findall(text)
    fenced_code_blocks = len(re.findall(r"(?m)^\s*```", text)) // 2
    indented_code_blocks = 0
    in_indented_block = False
    for line in text.splitlines():
        is_indented = bool(re.match(r"^(?: {4}|\t)\S", line))
        if is_indented and not in_indented_block:
            indented_code_blocks += 1
        in_indented_block = is_indented
    tables = sum(1 for line in text.splitlines() if line.strip().startswith("|") and "|" in line.strip()[1:])
    math_blocks = len(MATH_BLOCK.findall(text))
    return {
        "headings": headings,
        "images": images,
        "links": links,
        "code_blocks": fenced_code_blocks + indented_code_blocks,
        "fenced_code_blocks": fenced_code_blocks,
        "indented_code_blocks": indented_code_blocks,
        "table_rows": tables,
        "math_blocks": math_blocks,
    }


def compare(source: dict[str, object], translation: dict[str, object], allow_localized_images: bool) -> list[str]:
    errors: list[str] = []
    for key in ("code_blocks", "math_blocks"):
        if source[key] != translation[key]:
            errors.append(f"{key}: source={source[key]}, translation={translation[key]}")
    if len(source["headings"]) != len(translation["headings"]):
        errors.append(f"headings: source={len(source['headings'])}, translation={len(translation['headings'])}")
    if len(source["images"]) != len(translation["images"]):
        errors.append(f"images: source={len(source['images'])}, translation={len(translation['images'])}")
    elif not allow_localized_images and source["images"] != translation["images"]:
        errors.append("image order or URL changed; use --allow-localized-images only after checking anchors")
    if source["table_rows"] and not translation["table_rows"]:
        errors.append("table rows disappeared from the translation")
    return errors


def self_test() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        source = base / "source.md"
        translation = base / "translation.md"
        fixture = "# Title\n\n## Method\n\n![Figure 1](fig-1.png)\n\n$$x = y$$\n\n```python\nprint(x)\n```\n\n| a | b |\n| - | - |\n"
        source.write_text(fixture, encoding="utf-8")
        translation.write_text(fixture.replace("Title", "标题"), encoding="utf-8")
        errors = compare(inventory(source), inventory(translation), False)
        if errors:
            print(json.dumps({"self_test": "failed", "errors": errors}, ensure_ascii=False))
            return 1
    print('{"self_test":"passed"}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Markdown paper translation structure")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--translation", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-localized-images", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.source or not args.translation:
        parser.error("--source and --translation are required unless --self-test is used")
    source = inventory(args.source)
    translation = inventory(args.translation)
    errors = compare(source, translation, args.allow_localized_images)
    report = {"source": source, "translation": translation, "errors": errors, "passed": not errors}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
