"""PDF parser.

Two modes, selected per source:

* ``pdf_options.docling_json_dir`` set: read heading-delimited sections from
  Docling JSON in that folder, one file per document. The folder fills itself
  (see ``_fill_docling_cache`` and docs/adding-data.md), so Docling runs once per PDF.
* otherwise — convert PDFs live with Docling (imported lazily) and reconstruct the
  same structured, heading-delimited sections from ``document.export_to_dict()``
  (falling back to per-page Markdown/text only if that is unavailable).

Both paths produce the same section structure; the JSON folder is purely a
speed/caching optimization (Docling + OCR is slow, and you re-ingest repeatedly).

Metadata is domain-neutral (``file``/``source``/``source_file``/``title``/
``section_title``/``page_start``/``page_end``). Static per-source tags can be
added via ``data_sources[].extra_metadata``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kb.parsers.base import Section, file_digest, iter_source_files, planning_pass
from kb.parsers import register_parser

if TYPE_CHECKING:
    from config.schema import ChunkingConfig, DataSourceConfig, RagConfig


# --------------------------------------------------------------------------- #
# Docling-JSON reading-order helpers (ported from ingest_docling.py)
# --------------------------------------------------------------------------- #
def _extract_page_from_prov(prov: Any) -> int | None:
    if isinstance(prov, dict):
        pn = prov.get("page_no")
        return pn if isinstance(pn, int) else None
    if isinstance(prov, list):
        page_numbers = [
            p.get("page_no")
            for p in prov
            if isinstance(p, dict) and isinstance(p.get("page_no"), int)
        ]
        if page_numbers:
            return min(page_numbers)
    return None


def _parse_ref_index(ref: str, prefix: str) -> int | None:
    if not isinstance(ref, str):
        return None
    marker = f"#/{prefix}/"
    if not ref.startswith(marker):
        return None
    raw = ref[len(marker):]
    return int(raw) if raw.isdigit() else None


def _collect_refs(
    ref: str,
    groups: list[dict[str, Any]],
    out: list[tuple[str, int]],
    seen: set[int] | None = None,
) -> None:
    """Walk one body reference in reading order, emitting ``(kind, index)``.

    ``kind`` is ``"text"`` or ``"table"``; group refs recurse into their
    children. Picture refs (and anything else) are ignored. The original code
    followed only ``#/texts/`` and ``#/groups/`` — a ``#/tables/N`` ref matched
    neither and tables were silently dropped. ``seen`` guards against cyclic
    group references in malformed JSON (genuine Docling trees are acyclic)."""
    text_idx = _parse_ref_index(ref, "texts")
    if text_idx is not None:
        out.append(("text", text_idx))
        return
    table_idx = _parse_ref_index(ref, "tables")
    if table_idx is not None:
        out.append(("table", table_idx))
        return
    group_idx = _parse_ref_index(ref, "groups")
    if group_idx is None or group_idx >= len(groups):
        return
    seen = seen if seen is not None else set()
    if group_idx in seen:
        return
    seen.add(group_idx)
    group = groups[group_idx]
    children = group.get("children")
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict):
            child_ref = child.get("$ref")
            if isinstance(child_ref, str):
                _collect_refs(child_ref, groups, out, seen)


def _ordered_items(data: dict[str, Any]) -> list[tuple[str, int]]:
    body = data.get("body")
    if not isinstance(body, dict):
        return []
    children = body.get("children")
    if not isinstance(children, list):
        return []
    groups = data.get("groups")
    if not isinstance(groups, list):
        groups = []
    out: list[tuple[str, int]] = []
    for child in children:
        if isinstance(child, dict):
            ref = child.get("$ref")
            if isinstance(ref, str):
                _collect_refs(ref, groups, out)
    return out


def _table_to_markdown(table: dict[str, Any]) -> str:
    """Serialize a Docling table dict to a Markdown grid from ``data.grid``.

    Falls back to row/col-ordered ``table_cells`` if no grid is present.
    Returns "" for an empty/unparseable table."""
    data = table.get("data")
    if not isinstance(data, dict):
        return ""

    def _cell_text(cell: Any) -> str:
        if not isinstance(cell, dict):
            return ""
        return " ".join(str(cell.get("text", "")).split())

    rows: list[list[str]] = []
    grid = data.get("grid")
    if isinstance(grid, list) and grid:
        for row in grid:
            if not isinstance(row, list):
                continue
            rows.append([_cell_text(c) for c in row])
    else:
        cells = data.get("table_cells")
        if not isinstance(cells, list) or not cells:
            return ""
        cells = [c for c in cells if isinstance(c, dict)]
        n_cols = data.get("num_cols") or (
            max((c.get("start_col_offset_idx", 0) for c in cells), default=0) + 1
        )
        by_row: dict[int, dict[int, str]] = {}
        for c in cells:
            r = c.get("start_row_offset_idx", 0)
            col = c.get("start_col_offset_idx", 0)
            text = _cell_text(c)
            # Repeat the cell across the columns it spans so a spanned header
            # doesn't leave blanks (matches how the grid branch expands spans).
            span = max(1, int(c.get("col_span", 1) or 1))
            for offset in range(span):
                by_row.setdefault(r, {}).setdefault(col + offset, text)
        for r in sorted(by_row):
            rows.append([by_row[r].get(col, "") for col in range(n_cols)])

    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "Table:\n" + "\n".join(lines)


def _sections_from_docling_data(
    data: dict[str, Any],
    stem: str,
    min_section_chars: int,
    id_prefix: str,
    include_tables: bool = True,
) -> list[Section]:
    """Reconstruct heading-delimited sections from a Docling document dict.

    This is the same structure whether it comes from an exported JSON file or a
    live conversion's ``document.export_to_dict()`` — so both paths share it.
    Tables (referenced in reading order) are serialized to Markdown and folded
    into the section they appear in when ``include_tables`` is set.
    """
    texts = data.get("texts")
    if not isinstance(texts, list):
        return []
    tables = data.get("tables")
    if not isinstance(tables, list):
        tables = []

    ordered_items = _ordered_items(data) or [("text", i) for i in range(len(texts))]

    built: list[dict[str, Any]] = []
    current_title: str | None = None
    current_texts: list[str] = []
    current_pages: list[int] = []

    def flush() -> None:
        nonlocal current_title, current_texts, current_pages
        content = " ".join(current_texts).strip()
        if len(content) < min_section_chars:
            current_title, current_texts, current_pages = None, [], []
            return
        section_title = (current_title or "Untitled Section").strip()
        merged = content
        if section_title and not content.lower().startswith(section_title.lower()):
            merged = f"{section_title}\n\n{content}"
        built.append(
            {
                "title": section_title,
                "text": merged,
                "page_start": min(current_pages) if current_pages else None,
                "page_end": max(current_pages) if current_pages else None,
            }
        )
        current_title, current_texts, current_pages = None, [], []

    for kind, idx in ordered_items:
        if kind == "table":
            if not include_tables or idx >= len(tables):
                continue
            table = tables[idx]
            if not isinstance(table, dict):
                continue
            md = _table_to_markdown(table)
            if not md:
                continue
            page_no = _extract_page_from_prov(table.get("prov"))
            if isinstance(page_no, int):
                current_pages.append(page_no)
            current_texts.append(md)
            continue

        if idx >= len(texts):
            continue
        item = texts[idx]
        if not isinstance(item, dict) or item.get("content_layer") == "furniture":
            continue
        raw = item.get("canonical_text") or item.get("text")
        if not isinstance(raw, str):
            continue
        cleaned = " ".join(raw.split())
        if not cleaned:
            continue
        page_no = _extract_page_from_prov(item.get("prov"))
        if item.get("label") in {"section_header", "title", "chapter_title"}:
            # Flush the previous section FIRST, then record the heading's page
            # against the new section — otherwise the heading's page leaks into
            # the previous section's range (inflating its page_end) and is lost
            # for the section it actually opens.
            flush()
            current_title = cleaned
            if isinstance(page_no, int):
                current_pages.append(page_no)
            continue
        if isinstance(page_no, int):
            current_pages.append(page_no)
        current_texts.append(cleaned)
    flush()

    pdf_name = f"{stem}.pdf"
    sections: list[Section] = []
    for idx, section in enumerate(built, start=1):
        sections.append(
            Section(
                text=section["text"],
                doc_id=f"{id_prefix}:{stem}:s{idx}",
                metadata={
                    "file": pdf_name,
                    "source": pdf_name,
                    "source_file": pdf_name,
                    "title": section["title"],
                    "section_title": section["title"],
                    "section_index": idx,
                    "page_start": section["page_start"],
                    "page_end": section["page_end"],
                },
            )
        )
    return sections


def _sections_from_docling_json(
    json_dir: Path, cfg: "ChunkingConfig", include_tables: bool
) -> list[Section]:
    sections: list[Section] = []
    # Via iter_source_files, not json_dir.glob, so this path honours the
    # incremental-ingest file gate like every other source.
    for json_path in iter_source_files(json_dir, "*.json", "*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[ingest] skipping unreadable JSON {json_path.name}: {exc}")
            continue
        if cfg.strategy == "docling_hybrid":
            from docling_core.types.doc import DoclingDocument

            # model_validate enforces a strict Docling schema-version check;
            # a stale/foreign/malformed export would otherwise abort the whole
            # run, so isolate the failure to this one file.
            try:
                document = DoclingDocument.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                print(f"[ingest] skipping {json_path.name} (not a valid DoclingDocument): {exc}")
                continue
            sections.extend(
                _sections_from_hybrid(
                    document, json_path.stem, "docling-hybrid-json", cfg.hybrid_max_tokens
                )
            )
        else:
            sections.extend(
                _sections_from_docling_data(
                    data, json_path.stem, cfg.min_section_chars, "docling-json", include_tables
                )
            )
    return sections


def _sections_from_hybrid(
    document: Any, stem: str, id_prefix: str, max_tokens: int | None
) -> list[Section]:
    """Chunk a DoclingDocument with Docling's native token-aware HybridChunker.

    The chunker serializes tables/figures itself and contextualizes each chunk
    with its heading trail; we map its metadata onto the citation fields."""
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

    # HybridChunker's default tokenizer (a HuggingFace model) is fetched on
    # first use; surface a clear message if it can't be loaded (e.g. offline
    # with an empty HF cache) instead of an opaque download error.
    try:
        chunker = HybridChunker(max_tokens=max_tokens) if max_tokens else HybridChunker()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "docling_hybrid chunking could not initialize its tokenizer "
            f"({exc}). It needs the HybridChunker default tokenizer available "
            "(download once online, or use a different chunking strategy)."
        ) from exc
    pdf_name = f"{stem}.pdf"
    sections: list[Section] = []
    for idx, ch in enumerate(chunker.chunk(dl_doc=document), start=1):
        text = chunker.contextualize(chunk=ch)
        if not isinstance(text, str) or not text.strip():
            continue
        meta = getattr(ch, "meta", None)
        headings = list(getattr(meta, "headings", None) or [])
        title = headings[-1] if headings else stem
        pages: list[int] = []
        for item in getattr(meta, "doc_items", None) or []:
            for prov in getattr(item, "prov", None) or []:
                page_no = getattr(prov, "page_no", None)
                if isinstance(page_no, int):
                    pages.append(page_no)
        sections.append(
            Section(
                text=text.strip(),
                doc_id=f"{id_prefix}:{stem}:c{idx}",
                metadata={
                    "file": pdf_name,
                    "source": pdf_name,
                    "source_file": pdf_name,
                    "title": title,
                    "section_title": title,
                    "section_index": idx,
                    "page_start": min(pages) if pages else None,
                    "page_end": max(pages) if pages else None,
                },
            )
        )
    return sections


# --------------------------------------------------------------------------- #
# Live Docling conversion (lazy import: the live path and the cache fill)
# --------------------------------------------------------------------------- #
def _extract_pages(document: Any) -> list[tuple[int | None, str]]:
    pages: list[tuple[int | None, str]] = []
    doc_pages = getattr(document, "pages", None)
    if doc_pages:
        for page in doc_pages:
            page_no = (
                getattr(page, "page_number", None)
                or getattr(page, "number", None)
                or getattr(page, "page_no", None)
            )
            text = None
            if hasattr(page, "export_to_markdown"):
                text = page.export_to_markdown()
            elif hasattr(page, "export_to_text"):
                text = page.export_to_text()
            elif hasattr(page, "text"):
                text = page.text() if callable(page.text) else page.text
            if isinstance(text, str) and text.strip():
                pages.append((int(page_no) if page_no is not None else None, text.strip()))
    if pages:
        return pages
    try:
        data = None
        if hasattr(document, "export_to_dict"):
            data = document.export_to_dict()
        elif hasattr(document, "export_to_json"):
            data = json.loads(document.export_to_json())
        if isinstance(data, dict):
            for page in data.get("pages", []) or []:
                page_no = page.get("number") or page.get("page_number") or page.get("page_no")
                text = page.get("text") or page.get("content")
                if isinstance(text, str) and text.strip():
                    pages.append((int(page_no) if page_no is not None else None, text.strip()))
    except Exception:  # noqa: BLE001
        return pages
    return pages


def _build_ocr_options(
    engine: str,
    lang: list[str],
    force_full_page_ocr: bool,
    *,
    enabled: bool = False,
):
    """Build Docling's OCR options.

    ``enabled`` mirrors ``pdf_options.ocr``. The options object is built on every
    run regardless, because ``PdfPipelineOptions`` always wants one and ``do_ocr``
    is what actually switches OCR on, so the availability check must be gated on
    ``enabled`` or it would break ordinary ingests that never OCR anything.
    """
    from docling.datamodel.pipeline_options import OcrMacOptions, TesseractOcrOptions

    if engine == "mac":
        return OcrMacOptions(lang=lang, force_full_page_ocr=force_full_page_ocr)

    options = TesseractOcrOptions(lang=lang, force_full_page_ocr=force_full_page_ocr)
    if not enabled:
        return options

    # Fail here rather than minutes later inside the converter. Docling's own
    # message is clear but arrives only once it has started up, and it cannot know
    # that the shipped Docker image deliberately installs no apt packages.
    command = getattr(options, "tesseract_cmd", None) or "tesseract"
    if shutil.which(command) is None:
        raise RuntimeError(
            f"pdf_options.ocr is true but the '{command}' binary was not found.\n"
            "  The shipped Docker image ships no OCR engine, to keep it small.\n"
            "  Most PDFs do not need OCR: they already carry a text layer, and "
            "ocr: false (the default) reads them fine.\n"
            "  If your PDFs really are scans, build a derived image:\n"
            "    FROM <this image>\n"
            "    RUN apt-get update && apt-get install -y tesseract-ocr "
            "tesseract-ocr-eng tesseract-ocr-deu && rm -rf /var/lib/apt/lists/*\n"
            "  On macOS outside Docker, `brew install tesseract` and set "
            "pdf_options.ocr_engine: mac."
        )
    return options


def _figure_chunk_text(caption: str, description: str, fig_idx: int, page: int | None) -> str:
    head = f"Abbildung {fig_idx + 1}" + (f" (Seite {page})" if page else "")
    parts = [head]
    if caption:
        parts.append(caption)
    if description:
        parts.append(description)
    return "\n\n".join(parts).strip()


def _figure_sections(
    document: Any, stem: str, config: "RagConfig", id_prefix: str, section_base: int
) -> list[Section]:
    """Turn each rendered figure into a searchable/citable Section.

    Requires the document to have been converted with ``generate_picture_images``.
    Persists each figure PNG (for UI thumbnails + ``attach`` pixels) and, in
    ``describe``/``attach``, writes a VLM description as the chunk text."""
    from kb.figure_store import (
        describe_figure,
        description_dir,
        figure_dir,
        persist_figure,
        pil_to_data_uri,
    )

    images = config.images
    pictures = getattr(document, "pictures", None) or []
    if not pictures:
        return []
    dest = figure_dir(config)
    descriptions_root = description_dir(config)
    pdf_name = f"{stem}.pdf"
    out: list[Section] = []
    failed = 0
    skipped = 0
    for fig_idx, picture in enumerate(pictures):
        try:
            pil = picture.get_image(document)
        except Exception:  # noqa: BLE001
            pil = None
        if pil is None:
            continue
        page = None
        prov = getattr(picture, "prov", None) or []
        if prov:
            page_no = getattr(prov[0], "page_no", None)
            page = page_no if isinstance(page_no, int) else None
        try:
            caption = (picture.caption_text(document) or "").strip()
        except Exception:  # noqa: BLE001
            caption = ""
        try:
            image_path = persist_figure(pil, stem, fig_idx, dest)
        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] could not save figure {fig_idx} of {pdf_name}: {exc}")
            continue
        try:
            description = describe_figure(
                pil_to_data_uri(pil, max_px=images.describe_image_max_px),
                images.describe_prompt,
                images.vision_model,
                descriptions=descriptions_root,
                stem=stem,
                fig_idx=fig_idx,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] figure description failed for {pdf_name} fig{fig_idx}: {exc}")
            description = ""
        if not description.strip():
            failed += 1
        # Judge on content, not on `text`: _figure_chunk_text always prepends
        # "Abbildung N (Seite X)", so `text` is never empty and the old `if not
        # text` guard never fired. That is how failed descriptions ended up stored
        # as retrievable chunks carrying nothing but their own number. A caption is
        # still worth indexing on its own; a bare number is not.
        if not description.strip() and not caption:
            skipped += 1
            continue
        text = _figure_chunk_text(caption, description, fig_idx, page)
        label = caption[:80] or f"Abbildung {fig_idx + 1}"
        out.append(
            Section(
                text=text,
                doc_id=f"{id_prefix}:{stem}:fig{fig_idx}",
                metadata={
                    "file": pdf_name,
                    "source": pdf_name,
                    "source_file": pdf_name,
                    "title": label,
                    "section_title": label,
                    "section_index": section_base + fig_idx + 1,
                    "page_start": page,
                    "page_end": page,
                    "is_figure": True,
                    "figure_index": fig_idx,
                    "image_path": image_path,
                },
            )
        )
    if failed:
        # One line per document, because the per-figure prints above scroll past
        # unnoticed in a long ingest and this is the number that tells you whether
        # to re-run.
        print(
            f"[ingest] {pdf_name}: {failed} of {len(pictures)} figure descriptions "
            f"failed ({skipped} figure(s) skipped, no caption to fall back on)"
        )
    return out


def _converter(opts, images_scale: float | None = None):
    """A Docling converter with this source's options. Imported lazily: only the
    live and the cache-filling paths ever need Docling. ``images_scale`` set means
    render figures at that scale; the cache fill leaves it ``None``, the JSON
    reader never uses figures and embedding them would bloat every cached file."""
    # Docling's layout model asks torch.compile for a kernel, which needs a C++
    # compiler. The slim image has none, and neither does a bare Linux box, so
    # every conversion died with "InvalidCxxCompiler". torch reads this at import
    # and the imports below are what pull torch in, so here is early enough --
    # and unlike the Dockerfile's ENV it also covers `uv run python -m kb.ingest`
    # outside Docker, which docs/getting-started.md documents. Eager mode converts
    # a paper in ~20 s on CPU. setdefault, so TORCH_COMPILE_DISABLE=0 still wins
    # on a machine that does have a compiler and wants the compiled kernel.
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")

    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    image_opts = (
        {"generate_picture_images": True, "images_scale": images_scale}
        if images_scale is not None
        else {}
    )
    pdf_opts = PdfPipelineOptions(
        accelerator_options=AcceleratorOptions(device=opts.device, num_threads=4),
        do_ocr=opts.ocr,
        ocr_batch_size=1,
        layout_batch_size=1,
        table_batch_size=1,
        ocr_options=_build_ocr_options(
            opts.ocr_engine, opts.ocr_lang, False, enabled=opts.ocr
        ),
        **image_opts,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
    )


def _stamp(pdf: Path, opts) -> str:
    """What must match for a cached JSON to be current: the Docling that wrote
    it, the settings it was written with, and the PDF it was written from."""
    settings = {"ocr": opts.ocr, "ocr_engine": opts.ocr_engine, "ocr_lang": opts.ocr_lang}
    return f"docling {version('docling')} {json.dumps(settings, sort_keys=True)} sha256 {file_digest(pdf)}\n"


def _fill_docling_cache(
    base: Path, glob: str | None, json_dir: Path, opts, config: "RagConfig"
) -> None:
    """Convert every PDF under ``base`` (a folder or a single file) whose
    ``<stem>.json`` in ``json_dir`` is missing or out of date.

    Each JSON the fill writes gets a ``<stem>.json.stamp`` beside it (see
    :func:`_stamp`); the JSON is out of date when the stamp no longer matches, so
    a Docling upgrade, a settings change or a replaced PDF all convert again. A
    JSON without a stamp was exported by hand or predates the stamps and is
    trusted as it is: delete it to convert that document again.

    The PDFs go through the gate first, so a new PDF is a change the planning
    pass notices. The staleness check itself is ungated: a JSON deleted by hand
    must be regenerated even when its PDF did not change. A planning pass never
    converts, only a real run does. PDFs that share a stem would share a JSON, so
    they are reported and none of them is converted. A PDF that fails to convert
    is skipped with a message and tried again on the next real run; it never
    stops the others. Each JSON is written under a unique temporary name and
    renamed, so a concurrent reader or writer never sees a half-written file.

    The conversion itself runs in a child process (:func:`_convert_in_child`).
    Docling holds about 1 GB of models and grows by 65 to 150 MB per converted
    document that neither ``del`` nor ``gc.collect()`` gives back (measured: 2.4 GB
    after ten papers, 6.3 GB after 84, then the container was OOM-killed while
    indexing). A process that exits returns all of it, so indexing starts with a
    clean parent that never imported Docling, and a hard crash inside the PDF
    backend takes the child down, not the ingest.
    """
    iter_source_files(base, glob, "*.pdf")
    pdfs = [base] if base.is_file() else sorted(
        p for p in base.glob(glob or "*.pdf") if p.is_file()
    )
    by_stem: dict[str, list[Path]] = {}
    for pdf in pdfs:
        by_stem.setdefault(pdf.stem, []).append(pdf)
    stale = []
    for stem, group in by_stem.items():
        if len(group) > 1:
            print(
                f"[ingest] {stem}: {len(group)} PDFs share this name and would share one "
                f"converted file, none converted: {', '.join(str(p) for p in group)}"
            )
            continue
        target = json_dir / f"{stem}.json"
        stamp = target.with_name(target.name + ".stamp")
        if not target.exists() or (stamp.exists() and stamp.read_text() != _stamp(group[0], opts)):
            stale.append(group[0])
    if planning_pass():
        return
    for orphan in json_dir.glob("*.json.stamp"):  # a removed document takes its stamp along
        if not orphan.with_suffix("").exists():
            orphan.unlink()
    if not stale:
        return
    if not json_dir.parent.is_dir():
        print(f"[ingest] {json_dir}: parent folder does not exist (volume not mounted?), not converting")
        return
    json_dir.mkdir(exist_ok=True)
    _convert_in_child(stale, json_dir, opts)


_CHILD = """
import json, sys
from pathlib import Path
from config.schema import PdfOptions
from kb.parsers.pdf import _convert_into
args = json.load(sys.stdin)
_convert_into([Path(p) for p in args["pdfs"]], Path(args["json_dir"]), PdfOptions(**args["opts"]))
"""


def _convert_in_child(pdfs: list[Path], json_dir: Path, opts) -> None:
    """Run :func:`_convert_into` in a fresh interpreter and wait for it. Output is
    inherited, so its progress lines land in the same log. A non-zero exit is
    reported, not raised: the JSONs it did write are read, the rest is tried
    again on the next real run."""
    payload = json.dumps({"pdfs": [str(p) for p in pdfs], "json_dir": str(json_dir), "opts": opts.model_dump()})
    result = subprocess.run(
        [sys.executable, "-c", _CHILD], input=payload, text=True,
        cwd=Path(__file__).resolve().parents[2],  # the app root, so `kb` and `config` import
    )
    if result.returncode:
        print(f"[ingest] conversion process exited with {result.returncode}; "
              "PDFs it did not reach are tried again on the next run")


def _convert_into(pdfs: list[Path], json_dir: Path, opts) -> None:
    """Convert ``pdfs`` into ``json_dir``, one JSON and one stamp each."""
    converter = _converter(opts)
    for pdf in pdfs:
        target = json_dir / f"{pdf.stem}.json"
        fd, tmp = tempfile.mkstemp(dir=json_dir, prefix=f"{pdf.stem}.", suffix=".json.tmp")
        os.close(fd)
        try:
            converter.convert(str(pdf)).document.save_as_json(Path(tmp))
            Path(tmp).replace(target)
            target.with_name(target.name + ".stamp").write_text(_stamp(pdf, opts))
        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] {pdf.name}: conversion failed, skipped: {exc}", flush=True)
            continue
        finally:
            Path(tmp).unlink(missing_ok=True)
        print(f"[ingest] converted {pdf.name} -> {json_dir.name}/{target.name}", flush=True)


def _sections_from_live_pdf(
    pdf_paths: list[Path], opts, cfg: "ChunkingConfig", config: "RagConfig"
) -> list[Section]:
    images = config.images
    converter = _converter(opts, images.images_scale if images.mode != "none" else None)

    sections: list[Section] = []
    for pdf in pdf_paths:
        result = converter.convert(str(pdf))
        document = getattr(result, "document", None)
        if not document:
            continue

        # Docling-native token-aware chunking: let HybridChunker split the
        # document directly (it serializes tables/figures itself).
        if cfg.strategy == "docling_hybrid":
            hybrid = _sections_from_hybrid(document, pdf.stem, "docling-hybrid", cfg.hybrid_max_tokens)
            if hybrid:
                sections.extend(hybrid)
                if images.mode != "none":
                    sections.extend(
                        _figure_sections(document, pdf.stem, config, "docling-hybrid", 10_000)
                    )
                continue

        # Preferred: reconstruct structured, heading-delimited sections from the
        # Docling document model (same output as a pre-exported JSON file), so
        # citations get section titles and page ranges — no manual export needed.
        data = None
        if hasattr(document, "export_to_dict"):
            try:
                data = document.export_to_dict()
            except Exception:  # noqa: BLE001
                data = None
        if isinstance(data, dict):
            structured = _sections_from_docling_data(
                data, pdf.stem, cfg.min_section_chars, "docling", opts.include_tables
            )
            if structured:
                sections.extend(structured)
                if images.mode != "none":
                    sections.extend(
                        _figure_sections(document, pdf.stem, config, "docling", len(structured))
                    )
                continue

        # Fallback: per-page Markdown/text extraction.
        pages = _extract_pages(document)
        if not pages:
            if hasattr(document, "export_to_markdown"):
                text = document.export_to_markdown()
            elif hasattr(document, "export_to_text"):
                text = document.export_to_text()
            else:
                text = ""
            if isinstance(text, str) and text.strip():
                sections.append(
                    Section(
                        text=text.strip(),
                        doc_id=f"docling:{pdf.name}",
                        metadata={
                            "file": pdf.name,
                            "source": pdf.name,
                            "source_file": pdf.name,
                            "title": pdf.stem,
                            "page_start": None,
                            "page_end": None,
                        },
                    )
                )
            continue
        for idx, (page_no, text) in enumerate(pages, start=1):
            page = page_no or idx
            sections.append(
                Section(
                    text=text,
                    doc_id=f"docling:{pdf.name}:p{page}",
                    metadata={
                        "file": pdf.name,
                        "source": pdf.name,
                        "source_file": pdf.name,
                        "title": pdf.stem,
                        "page_start": page_no,
                        "page_end": page_no,
                    },
                )
            )
    return sections


@register_parser("pdf")
def parse_pdf(source: "DataSourceConfig", config: "RagConfig") -> list[Section]:
    from config.schema import PdfOptions

    chunking = source.chunking or config.chunking
    opts = source.pdf_options or PdfOptions()
    if opts.docling_json_dir:
        if config.images.mode != "none":
            print(
                "[ingest] images.mode != none but this source uses "
                "pdf_options.docling_json_dir — figure handling needs live "
                "conversion, so figures are skipped for this source."
            )
        json_dir = config.resolve_path(opts.docling_json_dir)
        _fill_docling_cache(config.resolve_path(source.path), source.glob, json_dir, opts, config)
        return _sections_from_docling_json(json_dir, chunking, opts.include_tables)

    base = config.resolve_path(source.path)
    pdf_paths = iter_source_files(base, source.glob, "*.pdf")
    if not pdf_paths:
        return []

    return _sections_from_live_pdf(pdf_paths, opts, chunking, config)
