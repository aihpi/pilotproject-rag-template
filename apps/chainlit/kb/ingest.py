"""Generic ingestion CLI.

A plain run is incremental: files already indexed and unchanged are skipped, so
adding a document to the folder and running again indexes just that document.

Examples::

    python -m kb.ingest                           # index new and changed files
    python -m kb.ingest --dry-run                 # parse+chunk, print samples, no embed
    python -m kb.ingest --config examples/minimal/rag.config.yaml
    python -m kb.ingest --recreate                # rebuild the collection from scratch
    python -m kb.ingest --only faq handbook       # ingest specific sources
"""

from __future__ import annotations

import argparse
import asyncio
import os

from config import CONFIG_PATH_ENV, load_config


def _print_dry_run(per_source, chunks, limit: int) -> None:
    print("DRY RUN: parsed and chunked, nothing embedded or written.\n")
    total = 0
    for s in per_source:
        total += s["chunks"]
        print(
            f"  source '{s['name']}' [{s['format']} / {s['strategy']}]: "
            f"{s['sections']} sections -> {s['chunks']} chunks"
        )
    print(f"\n  TOTAL: {total} chunks across {len(per_source)} source(s)\n")
    print(f"First {min(limit, len(chunks))} chunk(s):\n" + "-" * 60)
    for chunk in chunks[:limit]:
        preview = " ".join(chunk.text.split())
        if len(preview) > 300:
            preview = preview[:300] + "…"
        print(f"\n  doc_id : {chunk.doc_id}")
        print(f"  text   : {preview}")
        print(f"  metadata: {chunk.metadata}")


def _report_failure(exc: Exception) -> None:
    """Explain a failed run in terms of what to do about it.

    Without this, a dropped connection ends in a forty-line traceback through httpx,
    openai and litellm, whose last line is "Connection error." Nobody can act on that.
    The advice comes from ``check_setup``, so there is one place that decides what a
    given error means rather than two that can disagree.
    """
    print()
    print("Reading the documents stopped early.")
    print()
    try:
        from check_setup import _classify, _short, check_service_host
        from settings import LITELLM_BASE_URL

        # The same probe check_setup uses, for the same reason: litellm reports a
        # misspelled address and a mid-request disconnect identically as "Connection
        # error", so without this a typo gets blamed on the network and the reader is
        # sent looking in the wrong place.
        host = check_service_host(LITELLM_BASE_URL or "")
        cause, steps = _classify(exc, host_reachable=host.ok)
        print(f"  the error : {_short(exc)}")
        if not host.ok and host.error:
            print(f"  also      : {host.error}")
        print(f"  meaning   : {cause}")
        for index, step in enumerate(steps, start=1):
            label = "  do this  : " if index == 1 else " " * len("  do this  : ")
            print(f"{label}{index}. {step}")
    except Exception:  # noqa: BLE001 — never let the reporter hide the real error
        import traceback

        print("  the error :")
        traceback.print_exc()
    print()
    print("  To test your connection and settings on their own, run:  make check")
    print("  More explanations: docs/troubleshooting.md")
    print()
    print("  Documents indexed by earlier runs are untouched. The ones being read this")
    print("  time were not saved, so they will be read again when you run this again.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest configured data sources into the vector store.")
    ap.add_argument("--config", default=os.getenv(CONFIG_PATH_ENV), help="Path to a rag config YAML.")
    ap.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and rebuild the collection, re-reading every file. Needed after "
        "changing the chunking strategy or the embed model.",
    )
    ap.add_argument("--only", nargs="*", help="Ingest only these data source names.")
    ap.add_argument(
        "--skip-if-exists",
        action="store_true",
        help="Deprecated and rarely useful: exit without doing anything if the "
        "collection exists. A plain run already skips files that are indexed and "
        "unchanged, so this only prevents NEW files from being picked up.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Parse and chunk only; print samples.")
    ap.add_argument("--limit", type=int, default=5, help="How many sample chunks --dry-run prints.")
    args = ap.parse_args()

    config = load_config(args.config)
    only = set(args.only) if args.only else None

    if args.dry_run:
        from kb.ingestion_pipeline import plan_ingest

        per_source, chunks = plan_ingest(config, only=only)
        _print_dry_run(per_source, chunks, args.limit)
        return

    from kb.ingestion_pipeline import collection_exists, get_client, ingest_all

    if args.skip_if_exists and not args.recreate:
        if collection_exists(get_client(), config.vector_store.collection):
            print(f"Collection '{config.vector_store.collection}' exists; skipping ingestion.")
            return

    try:
        result = asyncio.run(ingest_all(config, recreate=args.recreate, only=only))
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001 — turn it into advice, then exit non-zero
        _report_failure(exc)
        raise SystemExit(1) from exc
    # ingest_all already explains the adoption and nothing-to-do cases; repeating
    # "Ingested 0 chunks" underneath them only reads like something went wrong.
    nothing_happened = not result["ingested"] and not result.get("pruned")
    if result.get("adopted") or (nothing_happened and result.get("skipped")):
        return
    parts = []
    if result.get("skipped"):
        parts.append(f"{result['skipped']} file(s) unchanged")
    if result.get("pruned"):
        parts.append(f"{result['pruned']} entr(ies) removed for deleted documents")
    suffix = f" ({', '.join(parts)})" if parts else ""
    print(f"Ingested {result['ingested']} chunks into '{result['collection']}'.{suffix}")


if __name__ == "__main__":
    main()
