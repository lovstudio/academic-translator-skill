#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

try:
    from pypdf import PdfReader, PdfWriter
except Exception as exc:  # pragma: no cover - dependency check
    raise SystemExit("Missing dependency: pypdf. Install it with `python3 -m pip install pypdf`.") from exc

try:
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
except Exception as exc:  # pragma: no cover - dependency check
    raise SystemExit("Missing dependency: reportlab. Install it with `python3 -m pip install reportlab`.") from exc


PROMPT_VERSION = "2026-05-13-v1"
OUTPUT_PREFIX = os.environ.get("LOVSTUDIO_ACADEMIC_TRANSLATOR_PREFIX", "论文中文译本")
CJK_FONT = "HandTranslatorUnicode"
FALLBACK_CJK_FONT = "STSong-Light"
LATIN_FONT = "Helvetica"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate an English academic PDF into a Chinese PDF while preserving page correspondence."
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("--mode", default="target-only", choices=["target-only", "zh-only", "facing-pages", "bilingual-expanded"])
    parser.add_argument("--translator", default="openai", choices=["openai", "agent", "passthrough", "placeholder"])
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--version", default=None, help="Version like v01. Defaults to the next available version.")
    parser.add_argument("--pages", default=None, help="Page selection such as 1-3,8,10-12. Defaults to all pages.")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit selected pages; useful for layout tests.")
    parser.add_argument("--height-scale", type=float, default=1.9)
    parser.add_argument("--export-translation-json", nargs="?", const="", default=None, help="Export a JSON template for agent-filled translations and exit. Optionally pass a path.")
    parser.add_argument("--translation-json", type=Path, default=None, help="Render the PDF from an agent-filled translation JSON file.")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--rate-limit-seconds", type=float, default=0.0)
    return parser.parse_args()


def normalize_mode(mode: str) -> str:
    if mode == "zh-only":
        return "target-only"
    return mode


def default_output_dir() -> Path:
    cwd_output = Path.cwd() / "output"
    if cwd_output.exists() or Path.cwd().name == "interests":
        return cwd_output
    return Path.cwd()


def safe_stem(path: Path) -> str:
    stem = path.stem.strip()
    stem = re.sub(r"[/:]+", " - ", stem)
    stem = re.sub(r"\s+", " ", stem)
    return stem[:140].strip(" .-")


def next_output_path(output_dir: Path, input_pdf: Path, date_text: str, version: str | None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(input_pdf)
    if version:
        normalized = version if version.startswith("v") else f"v{int(version):02d}"
        return output_dir / f"{OUTPUT_PREFIX} - {stem} - {date_text} - {normalized}.pdf"

    pattern = re.compile(
        re.escape(f"{OUTPUT_PREFIX} - {stem} - {date_text} - v") + r"(\d+)\.pdf$"
    )
    highest = 0
    for child in output_dir.glob(f"{OUTPUT_PREFIX} - {stem} - {date_text} - v*.pdf"):
        match = pattern.match(child.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return output_dir / f"{OUTPUT_PREFIX} - {stem} - {date_text} - v{highest + 1:02d}.pdf"


def parse_page_selection(page_text: str | None, total_pages: int) -> list[int]:
    if not page_text:
        return list(range(1, total_pages + 1))
    selected: list[int] = []
    for part in page_text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise SystemExit(f"Invalid page range: {token}")
            selected.extend(range(start, end + 1))
        else:
            selected.append(int(token))
    deduped = []
    seen = set()
    for page in selected:
        if page < 1 or page > total_pages:
            raise SystemExit(f"Page {page} is outside source range 1-{total_pages}.")
        if page not in seen:
            deduped.append(page)
            seen.add(page)
    return deduped


def run_pdftotext(input_pdf: Path, page_number: int) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    cmd = [exe, "-layout", "-enc", "UTF-8", "-f", str(page_number), "-l", str(page_number), str(input_pdf), "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return clean_extracted_text(proc.stdout)


def clean_extracted_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def extract_page_text(input_pdf: Path, reader: PdfReader, page_number: int) -> str:
    text = run_pdftotext(input_pdf, page_number)
    if text:
        return text
    fallback = reader.pages[page_number - 1].extract_text() or ""
    return clean_extracted_text(fallback)


def split_paragraphs(text: str) -> list[str]:
    text = clean_extracted_text(text)
    if not text:
        return []
    raw_blocks = re.split(r"\n\s*\n+", text)
    paragraphs: list[str] = []
    for block in raw_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        joined = " ".join(lines)
        joined = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", joined)
        joined = re.sub(r"\s+", " ", joined).strip()
        if joined:
            paragraphs.append(joined)
    return paragraphs


def cache_key(kind: str, model: str, text: str, extra: str = "") -> str:
    payload = "\n".join([PROMPT_VERSION, kind, model, extra, text])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cache(cache_dir: Path, key: str) -> str | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("translation")


def save_cache(cache_dir: Path, key: str, source: str, translation: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "prompt_version": PROMPT_VERSION,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "translation": translation,
    }
    (cache_dir / f"{key}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def json_default_path(output_pdf: Path) -> Path:
    return output_pdf.with_suffix(".translations.json")


def export_path_from_arg(raw_path: str | None, output_pdf: Path) -> Path:
    if raw_path is None or raw_path == "":
        return json_default_path(output_pdf)
    return Path(raw_path).expanduser().resolve()


def write_translation_template(path: Path, input_pdf: Path, mode: str, total_pages: int, records: list[dict]) -> None:
    pages = []
    for record in records:
        if mode == "bilingual-expanded":
            pages.append(
                {
                    "page_number": record["page_number"],
                    "size": list(record["size"]),
                    "paragraphs": [
                        {"source": paragraph, "translation": ""}
                        for paragraph in record["paragraphs"]
                    ],
                }
            )
        else:
            pages.append(
                {
                    "page_number": record["page_number"],
                    "size": list(record["size"]),
                    "source": record["source"],
                    "translation": "",
                }
            )

    payload = {
        "schema": "lovstudio-academic-translator.translations.v1",
        "mode": mode,
        "source_pdf": str(input_pdf),
        "source_total_pages": total_pages,
        "instructions": "Fill every translation field with Simplified Chinese. Preserve citations, equations, figure/table labels, URLs, code, and proper nouns.",
        "pages": pages,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def records_from_translation_json(path: Path, input_pdf: Path, reader: PdfReader) -> tuple[str, list[int], list[dict]]:
    data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    mode = normalize_mode(data.get("mode", "target-only"))
    if mode not in {"target-only", "facing-pages", "bilingual-expanded"}:
        raise SystemExit(f"Unsupported mode in translation JSON: {mode}")

    records = []
    page_numbers = []
    for page_data in data.get("pages", []):
        page_number = int(page_data["page_number"])
        if page_number < 1 or page_number > len(reader.pages):
            raise SystemExit(f"Page {page_number} in translation JSON is outside the source PDF range.")
        size = tuple(page_data.get("size") or page_size(reader.pages[page_number - 1]))
        record = {
            "page_number": page_number,
            "size": (float(size[0]), float(size[1])),
            "source": page_data.get("source", ""),
        }
        if mode == "bilingual-expanded":
            paragraphs = page_data.get("paragraphs", [])
            record["paragraphs"] = [item.get("source", "") for item in paragraphs]
            record["translations"] = [item.get("translation", "") for item in paragraphs]
            missing = [
                idx + 1
                for idx, item in enumerate(paragraphs)
                if item.get("source", "").strip() and not item.get("translation", "").strip()
            ]
            if missing:
                raise SystemExit(f"Missing translations on page {page_number}, paragraphs: {missing}")
        else:
            translation = page_data.get("translation", "")
            if page_data.get("source", "").strip() and not translation.strip():
                raise SystemExit(f"Missing translation for page {page_number}.")
            record["translation"] = translation
        page_numbers.append(page_number)
        records.append(record)

    if not records:
        raise SystemExit(f"No pages found in translation JSON: {path}")
    return mode, page_numbers, records


def openai_chat_completion(args: argparse.Namespace, messages: list[dict[str, str]]) -> str:
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is required for --translator openai.")

    base_url = args.base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    body = json.dumps(
        {
            "model": args.model,
            "messages": messages,
            "temperature": 0.1,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"OpenAI-compatible API request failed: HTTP {exc.code}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"OpenAI-compatible API request failed: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"Unexpected API response: {json.dumps(data, ensure_ascii=False)[:1000]}") from exc


def translate_text(args: argparse.Namespace, cache_dir: Path, text: str, *, kind: str, context: str) -> str:
    text = clean_extracted_text(text)
    if not text:
        return ""
    if args.translator == "passthrough":
        return text
    if args.translator == "placeholder":
        return f"［待翻译：{context}］\n{text}"
    if args.translator == "agent":
        raise SystemExit("Use --translator agent to export a translation JSON template, then render with --translation-json after the current Codex agent fills it.")

    key = cache_key(kind, args.model, text, context)
    if not args.no_cache:
        cached = load_cache(cache_dir, key)
        if cached is not None:
            return cached

    system = (
        "You are a professional academic translator. Translate English research-paper text into clear Simplified Chinese. "
        "Preserve section numbers, citations, equations, figure/table labels, URLs, code, proper nouns, and paragraph breaks. "
        "Do not summarize, omit content, or add commentary."
    )
    user = (
        f"Translate the following paper text into Simplified Chinese only.\n"
        f"Context: {context}\n\n"
        f"```text\n{text}\n```"
    )
    translation = openai_chat_completion(args, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    if args.rate_limit_seconds:
        time.sleep(args.rate_limit_seconds)
    if not args.no_cache:
        save_cache(cache_dir, key, text, translation)
    return translation


def translate_paragraphs(args: argparse.Namespace, cache_dir: Path, paragraphs: list[str], page_number: int) -> list[str]:
    translations = []
    for idx, paragraph in enumerate(paragraphs, start=1):
        translations.append(
            translate_text(
                args,
                cache_dir,
                paragraph,
                kind="paragraph",
                context=f"page {page_number}, paragraph {idx}",
            )
        )
    return translations


def register_fonts() -> None:
    global CJK_FONT
    for candidate in [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]:
        if Path(candidate).exists():
            try:
                pdfmetrics.registerFont(TTFont(CJK_FONT, candidate))
                return
            except Exception:
                continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(FALLBACK_CJK_FONT))
        CJK_FONT = FALLBACK_CJK_FONT
    except Exception:
        pass


def text_units(text: str) -> Iterable[str]:
    pattern = re.compile(r"[\u3400-\u9fff]|[^\u3400-\u9fff\s]+|\s+")
    for match in pattern.finditer(text):
        yield match.group(0)


def wrap_paragraph(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for unit in text_units(text):
        if unit.isspace():
            candidate = current + (" " if current else "")
        else:
            candidate = current + unit
        if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
            lines.append(current.rstrip())
            current = unit.strip() if unit.isspace() else unit
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


def build_lines(blocks: list[str], font_name: str, font_size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    for block_index, block in enumerate(blocks):
        for raw_line in block.splitlines() or [block]:
            wrapped = wrap_paragraph(raw_line.strip(), font_name, font_size, max_width)
            lines.extend(wrapped)
        if block_index != len(blocks) - 1:
            lines.append("")
    return lines


def choose_layout(blocks: list[str], width: float, height: float, margin: float, allow_two_columns: bool) -> tuple[float, int, float, list[str]]:
    gap = 18
    column_options = [1, 2] if allow_two_columns else [1]
    sizes = [10.5, 10, 9.5, 9, 8.5, 8, 7.5, 7, 6.5, 6, 5.5, 5]
    usable_height = height - margin * 2

    best: tuple[float, int, float, list[str]] | None = None
    for columns in column_options:
        column_width = (width - margin * 2 - gap * (columns - 1)) / columns
        for font_size in sizes:
            leading = font_size * 1.35
            lines = build_lines(blocks, CJK_FONT, font_size, column_width)
            capacity = max(1, math.floor(usable_height / leading)) * columns
            candidate = (font_size, columns, leading, lines)
            best = candidate
            if len(lines) <= capacity:
                return candidate
    assert best is not None
    return best


def draw_lines(
    c: canvas.Canvas,
    lines: list[str],
    width: float,
    height: float,
    *,
    margin: float,
    font_size: float,
    columns: int,
    leading: float,
    footer: str,
) -> None:
    gap = 18
    column_width = (width - margin * 2 - gap * (columns - 1)) / columns
    lines_per_column = max(1, math.floor((height - margin * 2) / leading))
    c.setFillColor(colors.black)
    c.setFont(CJK_FONT, font_size)
    for idx, line in enumerate(lines):
        column = min(columns - 1, idx // lines_per_column)
        row = idx % lines_per_column
        if column >= columns:
            break
        x = margin + column * (column_width + gap)
        y = height - margin - row * leading
        if line:
            c.drawString(x, y, line)

    c.setFont(CJK_FONT, 7)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawCentredString(width / 2, margin * 0.45, footer)


def render_target_pdf(records: list[dict], output_pdf: Path, *, bilingual: bool, height_scale: float) -> None:
    register_fonts()
    c = canvas.Canvas(str(output_pdf), pageCompression=1)
    for index, record in enumerate(records, start=1):
        source_width, source_height = record["size"]
        width = source_width
        height = source_height * height_scale if bilingual else source_height
        c.setPageSize((width, height))
        margin = 42 if not bilingual else 44

        if bilingual:
            blocks: list[str] = []
            for source, target in zip(record["paragraphs"], record["translations"]):
                blocks.append(source)
                blocks.append(target)
        else:
            blocks = split_paragraphs(record["translation"]) or [record["translation"]]

        font_size, columns, leading, lines = choose_layout(
            blocks,
            width,
            height,
            margin,
            allow_two_columns=not bilingual,
        )
        footer = f"{OUTPUT_PREFIX} | {index}"
        draw_lines(
            c,
            lines,
            width,
            height,
            margin=margin,
            font_size=font_size,
            columns=columns,
            leading=leading,
            footer=footer,
        )
        c.showPage()
    c.save()


def interleave_original_and_translation(input_pdf: Path, page_numbers: list[int], translation_pdf: Path, output_pdf: Path) -> None:
    source_reader = PdfReader(str(input_pdf))
    translation_reader = PdfReader(str(translation_pdf))
    writer = PdfWriter()
    for idx, page_number in enumerate(page_numbers):
        writer.add_page(source_reader.pages[page_number - 1])
        writer.add_page(translation_reader.pages[idx])
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def page_size(page) -> tuple[float, float]:
    box = page.mediabox
    return float(box.width), float(box.height)


def main() -> None:
    args = parse_args()
    args.mode = normalize_mode(args.mode)
    input_pdf = args.input_pdf.expanduser().resolve()
    if not input_pdf.exists():
        raise SystemExit(f"Input PDF does not exist: {input_pdf}")

    output_dir = (args.output_dir or default_output_dir()).expanduser().resolve()
    output_pdf = next_output_path(output_dir, input_pdf, args.date, args.version)
    cache_dir = (args.cache_dir or (output_dir / ".translate-paper-pdf-cache")).expanduser().resolve()

    reader = PdfReader(str(input_pdf))
    total_pages = len(reader.pages)
    page_numbers = parse_page_selection(args.pages, total_pages)
    if args.max_pages is not None:
        page_numbers = page_numbers[: args.max_pages]
    if not page_numbers:
        raise SystemExit("No pages selected.")

    if args.translation_json:
        args.mode, page_numbers, records = records_from_translation_json(args.translation_json, input_pdf, reader)
    else:
        records = []
        export_only = args.translator == "agent" or args.export_translation_json is not None
        for ordinal, page_number in enumerate(page_numbers, start=1):
            print(f"[{ordinal}/{len(page_numbers)}] Extracting page {page_number}", file=sys.stderr)
            source_text = extract_page_text(input_pdf, reader, page_number)
            record = {
                "page_number": page_number,
                "size": page_size(reader.pages[page_number - 1]),
                "source": source_text,
            }
            if args.mode == "bilingual-expanded":
                paragraphs = split_paragraphs(source_text)
                record["paragraphs"] = paragraphs
                if not export_only:
                    print(f"[{ordinal}/{len(page_numbers)}] Translating page {page_number} paragraphs ({len(paragraphs)})", file=sys.stderr)
                    record["translations"] = translate_paragraphs(args, cache_dir, paragraphs, page_number)
            else:
                if not export_only:
                    print(f"[{ordinal}/{len(page_numbers)}] Translating page {page_number}", file=sys.stderr)
                    record["translation"] = translate_text(
                        args,
                        cache_dir,
                        source_text,
                        kind="page",
                        context=f"page {page_number} of {total_pages}",
                    )
            records.append(record)

        if export_only:
            translation_json = export_path_from_arg(args.export_translation_json, output_pdf)
            write_translation_template(translation_json, input_pdf, args.mode, total_pages, records)
            print(str(translation_json))
            return

    if args.mode == "facing-pages":
        with tempfile.TemporaryDirectory() as tmpdir:
            translated_only = Path(tmpdir) / "translated.pdf"
            render_target_pdf(records, translated_only, bilingual=False, height_scale=1.0)
            interleave_original_and_translation(input_pdf, page_numbers, translated_only, output_pdf)
    elif args.mode == "bilingual-expanded":
        render_target_pdf(records, output_pdf, bilingual=True, height_scale=args.height_scale)
    else:
        render_target_pdf(records, output_pdf, bilingual=False, height_scale=1.0)

    print(str(output_pdf))


if __name__ == "__main__":
    main()
