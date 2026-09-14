"""Generic ingestion pipeline: parse -> chunk -> embed -> upsert.

``plan_ingest`` is the pure (no-I/O) parse+chunk stage — used by ``--dry-run``
and importable without Qdrant/LiteLLM. ``ingest_chunks``/``ingest_all`` add
embedding and Qdrant upserts, with config-driven payload indexes and an
embed-model sentinel guard. Heavy imports (qdrant_client, llm.embed) are
deferred into the functions that need them.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Awaitable, Callable

from config import RagConfig, get_config
from kb.chunkers import get_chunker
from kb.chunkers.base import Chunk
from kb.parsers import get_parser
from kb.parsers.base import FileGate, file_gate
from kb.sparse import SPARSE_FORMAT, SPARSE_VECTOR, sparse_vector

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]

# Deterministic id for the per-collection metadata sentinel point. It carries
# no "text" payload, so retrieval (which drops text-less payloads) never
# surfaces it as a result.
_SENTINEL_KEY = "__ingest_meta__"

# Second metadata point: which files this collection was built from, as
# {path relative to the config dir: sha256}. Keyed by path rather than by the
# payload's ``source_file`` on purpose — that key is parser-defined and
# inconsistent (the PDF parser stores "<stem>.pdf", so a docling-JSON source
# indexes X.pdf for a file named X.json, and the CSV/JSON parsers store whatever
# the user's field_mapping produces). Hashing paths sidesteps all of it and, as a
# bonus, an edited document is re-ingested instead of going stale.
_MANIFEST_KEY = "__ingest_manifest__"


def _point_id(doc_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))


# --------------------------------------------------------------------------- #
# Pure parse + chunk (no embedding, no Qdrant)
# --------------------------------------------------------------------------- #
def plan_ingest(
    config: RagConfig | None = None,
    *,
    only: set[str] | None = None,
    gate: FileGate | None = None,
) -> tuple[list[dict[str, Any]], list[Chunk]]:
    """Parse and chunk every (selected) data source. Returns (per-source
    summary, all chunks). No I/O beyond reading source files.

    With ``gate``, files whose contents are already indexed are not parsed, and
    the gate collects what it saw. A gate in ``skip_all`` mode enumerates the
    files without parsing any of them.
    """
    config = config or get_config()
    with file_gate(gate):
        return _plan_ingest(config, only)


def _plan_ingest(
    config: RagConfig,
    only: set[str] | None,
) -> tuple[list[dict[str, Any]], list[Chunk]]:
    per_source: list[dict[str, Any]] = []
    all_chunks: list[Chunk] = []
    for src in config.data_sources:
        if only and src.name not in only:
            continue
        parser = get_parser(src)
        chunking = src.chunking or config.chunking
        chunker = get_chunker(chunking.strategy)
        sections = parser(src, config)
        for section in sections:
            if src.extra_metadata:
                section.metadata.update(src.extra_metadata)
        chunks = chunker(sections, chunking)
        for i, chunk in enumerate(chunks):
            if not chunk.doc_id:
                chunk.doc_id = f"{src.name}:{i}"
        per_source.append(
            {
                "name": src.name,
                "format": src.format,
                "strategy": chunking.strategy,
                "sections": len(sections),
                "chunks": len(chunks),
            }
        )
        all_chunks.extend(chunks)
    return per_source, all_chunks


# --------------------------------------------------------------------------- #
# Qdrant helpers (lazy heavy imports)
# --------------------------------------------------------------------------- #
def get_client():
    from qdrant_client import QdrantClient

    from settings import QDRANT_API_KEY, QDRANT_URL

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def collection_exists(client, name: str) -> bool:
    return name in {c.name for c in client.get_collections().collections}


def _distance(name: str):
    from qdrant_client.models import Distance

    return {"cosine": Distance.COSINE, "dot": Distance.DOT, "euclid": Distance.EUCLID}[name]


def _ensure_collection(client, name: str, size: int, distance: str, recreate: bool) -> None:
    from qdrant_client.models import Modifier, SparseVectorParams, VectorParams

    if recreate and collection_exists(client, name):
        client.delete_collection(collection_name=name, timeout=60)
    if not collection_exists(client, name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=size, distance=_distance(distance)),
            # Every new collection gets the sparse (lexical) vector, whether or
            # not hybrid search is on: the vector is a locally computed word
            # count, so writing it costs nothing, and its presence makes
            # `retrieval.hybrid` a pure query-time switch — no re-ingest to turn
            # it on later, and one collection can A/B dense vs. fused retrieval.
            # IDF is applied server-side, which is why the client only ever
            # sends term frequencies (see kb/sparse.py).
            sparse_vectors_config={SPARSE_VECTOR: SparseVectorParams(modifier=Modifier.IDF)},
        )


def lexical_rebuild_reason(client, collection: str, *, hybrid: bool) -> str | None:
    """Why ``collection`` must be rebuilt before it can be searched lexically, or None.

    Two ways a collection is unusable, both of which otherwise fail *silently* —
    the lexical leg simply matches nothing, so hybrid degrades to dense while the
    config (and the header chip) still say it is on:

    * it predates lexical vectors, and an existing collection cannot gain one;
    * it was written by a different lexical format, so its stored token ids do not
      match the ones this code computes.

    ``hybrid`` gates only the first. A dense-only collection is perfectly usable
    with the switch off — ingest keeps writing plain dense points into it — so
    calling it broken there would demand a full re-embed nobody asked for. The
    format check is ungated: ingest writes lexical vectors into any collection whose
    schema has them, so a mismatch would mix two formats in one index and then
    overwrite the recorded version, leaving a corpus no later check can diagnose.
    """
    if not collection_exists(client, collection):
        return None  # nothing to conflict with; ingest will build it correctly

    has_sparse = _supports_sparse(client, collection)
    if hybrid and not has_sparse:
        return (
            f"Collection '{collection}' has no lexical vector, but retrieval.hybrid is "
            f"on. It was built before hybrid search existed and cannot gain one, so the "
            f"lexical half of every query would match nothing."
        )
    if has_sparse:
        stored = (_read_sentinel(client, collection) or {}).get("sparse_format")
        # A missing key means the collection predates versioning — tolerated, exactly
        # as a missing embed_model is below.
        if stored is not None and stored != SPARSE_FORMAT:
            return (
                f"Collection '{collection}' was built with lexical format {stored}, but "
                f"this version writes {SPARSE_FORMAT}. Its stored terms would not match "
                f"the ones queries compute, so hybrid search would silently find "
                f"nothing — and ingesting into it would mix the two formats."
            )
    return None


_verified_hybrid: set[tuple[str, bool]] = set()


def verify_hybrid_compatible(client, collection: str, *, hybrid: bool) -> None:
    """Raise unless ``collection`` can serve hybrid retrieval as configured.

    For readers that cannot fix the problem themselves — the app, the query path,
    ``make check``. Ingest calls :func:`lexical_rebuild_reason` instead and rebuilds.

    A collection's answer cannot change without a re-ingest, so a success is cached;
    a failure is not, and neither is a lookup error. Keyed on the flag too, since
    ``hybrid`` decides whether the missing-vector case counts as broken.
    """
    if (collection, hybrid) in _verified_hybrid:
        return

    reason = lexical_rebuild_reason(client, collection, hybrid=hybrid)
    if reason:
        raise RuntimeError(
            f"{reason} Run 'docker compose run --rm ingest python -m kb.ingest' — it "
            f"rebuilds a collection in this state automatically. Or set "
            f"retrieval.hybrid: false to stay with semantic search."
        )
    _verified_hybrid.add((collection, hybrid))


def _count_points(client, name: str) -> int | str:
    """Point count for the rebuild log; never the reason a rebuild fails."""
    try:
        return client.count(collection_name=name).count
    except Exception:  # noqa: BLE001 — a log detail, not a precondition
        return "an unknown number of"


def _supports_sparse(client, name: str) -> bool:
    """Whether the collection declares our sparse vector.

    Collections created before sparse vectors existed are dense-only, and Qdrant
    rejects a point carrying a vector name the collection does not declare — so
    incremental ingest into an old collection must keep writing plain dense.

    A lookup failure propagates deliberately: guessing "legacy" on error would
    write dense-only points into a sparse-capable collection, leaving permanent
    silent gaps in the lexical index. The collection exists at every call site
    (``_ensure_collection`` ran first), so a failure here is a real fault.
    """
    params = client.get_collection(name).config.params
    return SPARSE_VECTOR in (params.sparse_vectors or {})


def _ensure_payload_indexes(client, name: str, fields: list[str]) -> None:
    from qdrant_client.models import PayloadSchemaType

    for field in fields:
        try:
            client.create_payload_index(
                collection_name=name, field_name=field, field_schema=PayloadSchemaType.KEYWORD
            )
        except Exception:  # noqa: BLE001 — index may already exist
            pass


def _read_sentinel(client, name: str) -> dict[str, Any] | None:
    try:
        records = client.retrieve(
            collection_name=name, ids=[_point_id(_SENTINEL_KEY)], with_payload=True
        )
    except Exception:  # noqa: BLE001
        return None
    return dict(records[0].payload) if records else None


def read_manifest(client, name: str) -> dict[str, str] | None:
    """Files this collection was built from, or None if it predates the manifest."""
    try:
        records = client.retrieve(
            collection_name=name, ids=[_point_id(_MANIFEST_KEY)], with_payload=True
        )
    except Exception:  # noqa: BLE001 — collection may not exist yet
        return None
    if not records:
        return None
    files = dict(records[0].payload or {}).get("files")
    return dict(files) if isinstance(files, dict) else None


def write_manifest(client, name: str, files: dict[str, str], vector: list[float]) -> None:
    from qdrant_client.models import PointStruct

    client.upsert(
        collection_name=name,
        points=[
            PointStruct(
                id=_point_id(_MANIFEST_KEY),
                vector=vector,
                payload={"_meta": True, "files": dict(files)},
            )
        ],
    )


def indexed_source_files(client, name: str) -> set[str]:
    """Distinct ``source_file`` payload values in a collection.

    One scroll fetching a single payload field, not one filtered count per file:
    ``retrieval.payload_indexes`` is empty by default, so every filtered count
    would scan the whole collection.
    """
    found: set[str] = set()
    offset = None
    while True:
        try:
            points, offset = client.scroll(
                collection_name=name,
                limit=1000,
                offset=offset,
                with_payload=["source_file"],
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] could not list indexed documents: {exc}")
            return found
        for point in points:
            value = (point.payload or {}).get("source_file")
            if isinstance(value, str):
                found.add(value)
        if offset is None:
            break
    return found


def _write_sentinel(client, name: str, embed_model: str, size: int, vector: list[float]) -> None:
    from qdrant_client.models import PointStruct

    client.upsert(
        collection_name=name,
        points=[
            PointStruct(
                id=_point_id(_SENTINEL_KEY),
                # Deliberately a bare dense vector, never the dict form used for
                # chunks: _manifest_vector reuses this point's vector and gates on
                # isinstance(vector, list). A dict here makes it return None, the
                # manifest is never stored, and every later run re-embeds the whole
                # corpus.
                vector=vector,
                payload={
                    "_meta": True,
                    "embed_model": embed_model,
                    "vector_size": size,
                    "sparse_format": SPARSE_FORMAT,
                },
            )
        ],
    )


# --------------------------------------------------------------------------- #
# Embed + upsert
# --------------------------------------------------------------------------- #
def _payload(chunk: Chunk) -> dict[str, Any]:
    return {"text": chunk.text, **chunk.metadata}


async def ingest_chunks(
    chunks: list[Chunk],
    *,
    collection: str,
    distance: str = "cosine",
    payload_indexes: list[str] | None = None,
    recreate: bool = False,
    batch_size: int | None = None,
    max_batch_chars: int | None = None,
    embed_model: str | None = None,
    progress: ProgressCallback | None = None,
) -> int:
    from qdrant_client.models import PointStruct

    from llm import embed

    if not chunks:
        logger.info("ingest: no chunks to ingest")
        return 0

    batch_size = batch_size or int(os.getenv("INGEST_BATCH_SIZE", "64"))
    max_batch_chars = max_batch_chars or int(os.getenv("INGEST_MAX_BATCH_CHARS", "20000"))
    embed_model = embed_model or get_config().models.embed_model
    client = get_client()

    # Sentinel guard: refuse a silent embed-model swap into an existing collection.
    if collection_exists(client, collection) and not recreate:
        sentinel = _read_sentinel(client, collection)
        prev = sentinel.get("embed_model") if sentinel else None
        if prev and prev != embed_model:
            raise RuntimeError(
                f"Collection '{collection}' was built with embed model '{prev}', but the "
                f"config uses '{embed_model}'. Vectors would be incompatible. Re-ingest with "
                f"--recreate, or set vector_store.collection to a new name."
            )

    first_vec = (await embed([chunks[0].text]))[0]
    _ensure_collection(client, collection, len(first_vec), distance, recreate)
    _ensure_payload_indexes(client, collection, payload_indexes or [])
    _write_sentinel(client, collection, embed_model, len(first_vec), first_vec)

    # Legacy dense-only collections (created before sparse vectors) reject named
    # vectors, so attach the lexical vector only where the schema declares it.
    has_sparse = _supports_sparse(client, collection)

    def _vector(dense: list[float], text: str):
        if not has_sparse:
            return dense
        return {"": dense, SPARSE_VECTOR: sparse_vector(text)}

    client.upsert(
        collection_name=collection,
        points=[
            PointStruct(
                id=_point_id(chunks[0].doc_id),
                vector=_vector(first_vec, chunks[0].text),
                payload=_payload(chunks[0]),
            )
        ],
    )

    start = 1
    current = batch_size
    while start < len(chunks):
        batch: list[Chunk] = []
        total = 0
        for chunk in chunks[start : start + current]:
            n = len(chunk.text)
            if batch and total + n > max_batch_chars:
                break
            batch.append(chunk)
            total += n
        vectors = await embed([c.text for c in batch])
        points = [
            PointStruct(id=_point_id(c.doc_id), vector=_vector(v, c.text), payload=_payload(c))
            for c, v in zip(batch, vectors, strict=True)
        ]
        try:
            client.upsert(collection_name=collection, points=points)
            start += len(batch)
            if progress:
                await progress(min(start, len(chunks)), len(chunks))
        except Exception as exc:  # noqa: BLE001
            if "Payload error" in str(exc) and current > 1:
                current = max(1, current // 2)
                logger.warning("ingest: batch too large, halving to %d and retrying", current)
                continue
            raise

    logger.info("ingest: upserted %d chunks into '%s'", len(chunks), collection)
    return len(chunks)


def _adopt(client, collection: str, config: RagConfig, only: set[str] | None) -> dict[str, str]:
    """Record what an existing pre-manifest collection was built from.

    Enumerates and hashes every source file without parsing any of it, so the
    first run under the new code costs nothing and every later run is
    incremental. The alternative, treating a manifest-less collection as empty,
    would silently re-embed (and re-describe every figure in) corpora that are
    already indexed.

    The assumption is that the collection matches the folder. Rather than trust
    it, cross-check against the payload: a file with no chunks under either
    naming convention is reported, because that is a document the user believes
    is searchable and is not.
    """
    gate = FileGate(skip_all=True, root=config.resolve_path("."))
    plan_ingest(config, only=only, gate=gate)

    indexed = indexed_source_files(client, collection)
    unmatched = [
        key
        for key in sorted(gate.seen)
        # Two candidate keys cover both conventions without a per-format switch:
        # text.py stores path.name, pdf.py stores "<stem>.pdf".
        if not {(name := key.rsplit("/", 1)[-1]), f"{name.rsplit('.', 1)[0]}.pdf"} & indexed
    ]

    print(
        f"[ingest] adopting existing collection '{collection}': "
        f"{len(gate.seen)} file(s) recorded, nothing re-ingested."
    )
    if unmatched:
        print(
            f"[ingest] WARNING: {len(unmatched)} file(s) have no chunks in "
            f"'{collection}' and were adopted anyway, so they stay unsearchable. "
            f"Re-ingest with --recreate to index them:"
        )
        for key in unmatched:
            print(f"[ingest]   - {key}")
    return dict(gate.seen)


def _payload_names_for(gate_key: str) -> list[str]:
    """Payload ``source_file`` values a manifest key could have produced.

    Two candidates, the same pair the adoption cross-check uses: parsers store
    either the plain file name (``text.py``) or ``"<stem>.pdf"`` (``pdf.py``, which
    is why a docling-JSON source indexes ``X.pdf`` for a file named ``X.json``).
    """
    name = gate_key.rsplit("/", 1)[-1]
    return [name, f"{name.rsplit('.', 1)[0]}.pdf"]


def _prune_removed(
    client,
    collection: str,
    removed: list[str],
    keep_names: set[str],
) -> tuple[int, list[str]]:
    """Delete the entries of files that are gone from disk.

    Returns (points deleted, keys whose entries could not be safely identified).
    A key is left alone when it matches nothing (a ``json``/``csv`` source whose
    ``field_mapping`` writes no ``source_file``) and when its identity is
    ambiguous: entries are found by file *name*, so a deleted ``a/intro.pdf``
    cannot be told apart from a surviving ``b/intro.pdf`` and pruning it would
    take the survivor's entries with it. ``keep_names`` holds the names still on
    disk, and anything colliding with them is reported instead of deleted.
    """
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        FilterSelector,
        MatchAny,
    )

    deleted = 0
    unmatched: list[str] = []
    handled: set[str] = set()
    for key in removed:
        names = _payload_names_for(key)
        if keep_names.intersection(names):
            print(
                f"[ingest] not removing entries for {key}: another file still on disk "
                f"has the same name, and entries are matched by name only."
            )
            unmatched.append(key)
            continue
        condition = Filter(
            must=[FieldCondition(key="source_file", match=MatchAny(any=names))]
        )
        try:
            found = client.count(
                collection_name=collection, count_filter=condition, exact=True
            ).count
        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] could not look up entries for {key}: {exc}")
            unmatched.append(key)
            continue
        if not found:
            # A PDF and its docling JSON index under one name. When the first of
            # the pair already took the entries, the second has nothing to report.
            if not handled.intersection(names):
                unmatched.append(key)
            handled.update(names)
            continue
        client.delete(collection_name=collection, points_selector=FilterSelector(filter=condition))
        deleted += found
        handled.update(names)
        print(f"[ingest] removed {found} entr(ies) for deleted document {key}")
    return deleted, unmatched


def _warn_about_duplicate_names(gate: FileGate) -> None:
    """Report files that share a name, because only one of them survives indexing.

    Parsers derive ``doc_id`` from the file name, and the pipeline derives each
    Qdrant point id from ``doc_id``, so two files called ``intro.pdf`` in different
    directories produce identical ids and the second silently overwrites the first.
    That predates the incremental ingest and is not fixed here (changing the id
    derivation would invalidate every existing point id and force a full re-ingest),
    but it is no longer silent.
    """
    by_name: dict[str, list[str]] = {}
    for key in sorted(gate.seen):
        by_name.setdefault(key.rsplit("/", 1)[-1], []).append(key)
    clashes = {name: keys for name, keys in by_name.items() if len(keys) > 1}
    if not clashes:
        return
    print(
        f"[ingest] WARNING: {len(clashes)} file name(s) occur more than once. Documents "
        "are identified by name, so only one of each set ends up in the collection and "
        "the others are lost. Rename them to be unique:"
    )
    for name, keys in clashes.items():
        print(f"[ingest]   {name}: {', '.join(keys)}")


def _removed_keys(
    manifest: dict[str, str],
    gate: FileGate,
    *,
    only: set[str] | None,
) -> list[str]:
    """Manifest entries whose file is no longer on disk, or [] if unsafe to say.

    Two cases must never be mistaken for deletions:

    - ``--only`` deliberately looks at a subset of the sources, so every file
      belonging to the other sources is simply not enumerated.
    - Nothing at all was found while the manifest is not empty. A documents folder
      that is suddenly empty is far more likely a bind mount that did not come up,
      or a wrong ``path``, than someone deleting the whole corpus, and acting on it
      would clear the collection.

    Replacing the whole corpus is *not* one of those cases and does prune: swapping
    every old document for new ones leaves files on disk, so the old entries are
    correctly recognised as gone.
    """
    if only:
        return []
    removed = [key for key in sorted(manifest) if key not in gate.seen]
    if removed and not gate.seen:
        print(
            f"[ingest] WARNING: not one of the {len(manifest)} known file(s) was found, "
            "and the folder holds nothing else. Refusing to treat that as a deletion, "
            "because this is usually a mount that did not come up or a wrong 'path'. "
            "Nothing was removed. If you really do want to empty the collection, use "
            "--recreate."
        )
        return []
    return removed


async def ingest_all(
    config: RagConfig | None = None,
    *,
    recreate: bool = False,
    only: set[str] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Ingest every configured source, skipping files that are already indexed.

    Without ``recreate`` this is incremental: a file is parsed only if it is new
    or its contents changed, and a file deleted from disk has its entries removed.
    Adding, editing or deleting a document and restarting is therefore enough,
    which is what the ``ingest`` compose service relies on.
    """
    config = config or get_config()
    collection = config.vector_store.collection
    client = get_client()

    # Ingest is the one caller that can fix a stale lexical index, so it does rather
    # than telling the reader to re-run it with a flag. Checked here and not in
    # ingest_chunks because an unchanged corpus never reaches that call, and "code
    # updated, documents untouched" is exactly how a mismatch arrives. `config`, not
    # get_config() — `kb.ingest --config other.yaml` need not be the process singleton.
    if not recreate:
        reason = lexical_rebuild_reason(client, collection, hybrid=config.retrieval.hybrid)
        if reason:
            # Loud, and with the cost named: this discards every point and re-embeds
            # the corpus, which is billed gateway traffic. Silence here would look
            # like an ordinary incremental run that inexplicably took an hour.
            existing = _count_points(client, collection)
            print(f"[ingest] {reason}")
            print(
                f"[ingest] rebuilding '{collection}' automatically: {existing} existing "
                f"point(s) will be discarded and the corpus re-embedded. This is the "
                f"only way to add a lexical vector to an existing collection."
            )
            recreate = True

    exists = collection_exists(client, collection)

    manifest: dict[str, str] = {}
    if not recreate and exists:
        stored = read_manifest(client, collection)
        if stored is None:
            adopted = _adopt(client, collection, config, only)
            if adopted:
                _store_manifest(client, collection, adopted)
            return {
                "ingested": 0,
                "collection": collection,
                "sources": [],
                "adopted": len(adopted),
            }
        manifest = stored

    gate = FileGate(known=manifest, root=config.resolve_path("."))
    per_source, all_chunks = plan_ingest(config, only=only, gate=gate)
    _warn_about_duplicate_names(gate)

    # Deletions are handled whether or not there is anything new to ingest, and in
    # the same run, so replacing a whole corpus in one go both removes the old
    # entries and indexes the new documents.
    removed = [] if recreate else _removed_keys(manifest, gate, only=only)
    pruned = 0
    if removed and exists:
        keep_names = {name for key in gate.seen for name in _payload_names_for(key)}
        pruned, unmatched = _prune_removed(client, collection, removed, keep_names)
        if unmatched:
            print(
                f"[ingest] WARNING: {len(unmatched)} deleted document(s) left entries "
                "that could not be removed safely, so they stay searchable. Either the "
                "source writes its own metadata (a json/csv field_mapping without "
                "source_file), or another file on disk has the same name. Use "
                "--recreate to clear them:"
            )
            for key in unmatched:
                print(f"[ingest]   - {key}")

    count = 0
    if all_chunks:
        parsed = len(gate.seen) - len(gate.skipped)
        print(f"[ingest] {parsed} file(s) to ingest, {len(gate.skipped)} unchanged and skipped.")
        count = await ingest_chunks(
            all_chunks,
            collection=collection,
            distance=config.vector_store.distance,
            payload_indexes=config.retrieval.payload_indexes,
            recreate=recreate,
            embed_model=config.models.embed_model,
            progress=progress,
        )
    elif gate.skipped and not removed:
        print(
            f"[ingest] nothing to do: all {len(gate.skipped)} file(s) are already "
            f"indexed in '{collection}' and unchanged."
        )

    # Merge rather than replace: a run limited by --only must not drop the other
    # sources' files from the manifest and make them look new next time. Pruned
    # files are dropped, so a document that comes back later is ingested again.
    if recreate:
        merged = dict(gate.seen)
    else:
        merged = {**manifest, **gate.seen}
        for key in removed:
            merged.pop(key, None)

    if merged or count or pruned:
        _store_manifest(client, collection, merged)

    return {
        "ingested": count,
        "collection": collection,
        "sources": per_source,
        "skipped": len(gate.skipped),
        "removed": len(removed),
        "pruned": pruned,
    }


def _manifest_vector(client, collection: str) -> list[float] | None:
    """Reuse the sentinel's vector: the manifest point needs one of the right size."""
    sentinel = None
    try:
        records = client.retrieve(
            collection_name=collection, ids=[_point_id(_SENTINEL_KEY)], with_vectors=True
        )
        sentinel = records[0] if records else None
    except Exception:  # noqa: BLE001
        return None
    vector = getattr(sentinel, "vector", None)
    return list(vector) if isinstance(vector, list) else None


def _store_manifest(client, collection: str, files: dict[str, str]) -> None:
    vector = _manifest_vector(client, collection)
    if vector is None:
        print("[ingest] could not store the file manifest (no sentinel vector to reuse).")
        return
    write_manifest(client, collection, files, vector)


def drop_collection(name: str) -> None:
    client = get_client()
    if collection_exists(client, name):
        client.delete_collection(collection_name=name, timeout=60)
