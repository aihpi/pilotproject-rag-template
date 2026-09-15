"""Incremental ingest: the file gate and the per-collection file manifest.

Before this, the compose ``ingest`` service exited as soon as the target
collection existed, so a PDF dropped into the documents folder was never indexed
and nothing said so. A run is now incremental: files already indexed and
unchanged are skipped, new and edited ones are not.

No Qdrant, LiteLLM or Docling here — the pipeline talks to a fake client and a
fake embedder, and the sources are plain text files.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import kb.ingestion_pipeline as pipeline
from config.schema import ChunkingConfig, DataSourceConfig, RagConfig
from kb.parsers.base import FileGate, file_digest, file_gate, iter_source_files


def _config_at(dir_path: Path, **kw) -> RagConfig:
    cfg = RagConfig(**kw)
    cfg._config_dir = dir_path
    return cfg


def _text_config(tmp_path: Path) -> RagConfig:
    return _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="docs", path="docs", format="txt", glob="*.txt"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_unchanged_file_is_skipped_and_still_recorded(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    gate = FileGate(root=tmp_path)

    first = gate.admit([target])
    assert first == [target]
    digest = gate.seen["a.txt"]

    # A second run that already knows this hash must not hand the file over.
    again = FileGate(known={"a.txt": digest}, root=tmp_path)
    assert again.admit([target]) == []
    # ...but the caller still learns the file exists, so the manifest keeps it.
    assert again.seen == {"a.txt": digest}
    assert again.skipped == ["a.txt"]


def test_edited_file_is_ingested_again(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("first version", encoding="utf-8")
    stale = FileGate(root=tmp_path)
    stale.admit([target])
    old_digest = stale.seen["a.txt"]

    target.write_text("second version", encoding="utf-8")
    gate = FileGate(known={"a.txt": old_digest}, root=tmp_path)

    assert gate.admit([target]) == [target], "an edited file must not be treated as indexed"
    assert gate.seen["a.txt"] != old_digest


def test_skip_all_enumerates_without_returning_anything(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    gate = FileGate(skip_all=True, root=tmp_path)

    admitted = gate.admit(sorted(tmp_path.glob("*.txt")))

    assert admitted == []
    assert set(gate.seen) == {"a.txt", "b.txt"}


def test_gate_keys_are_relative_to_the_config_dir(tmp_path):
    nested = tmp_path / "data" / "documents"
    nested.mkdir(parents=True)
    target = nested / "paper.pdf"
    target.write_bytes(b"%PDF-1.4")
    gate = FileGate(root=tmp_path)

    gate.admit([target])

    assert list(gate.seen) == ["data/documents/paper.pdf"]


def test_keys_stay_relative_when_the_source_is_outside_the_config_dir(tmp_path):
    """The shipped layout, and the case that first went wrong.

    examples/papers/rag.config.yaml points at ../../data/documents, so the files
    are not below the config directory. Path.relative_to cannot express that and
    fell back to an absolute path, which differs between a Docker run
    (/app/data/...) and a local one (/Users/.../data/...). The manifest then missed
    on every file and would have re-ingested the whole corpus, vision calls
    included.
    """
    config_dir = tmp_path / "examples" / "papers"
    config_dir.mkdir(parents=True)
    docs = tmp_path / "data" / "documents"
    docs.mkdir(parents=True)
    paper = docs / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    gate = FileGate(root=config_dir)
    gate.admit([paper])

    assert list(gate.seen) == ["../../data/documents/paper.pdf"]
    # The same relative key must come out from a different absolute prefix.
    other = tmp_path / "elsewhere"
    (other / "examples" / "papers").mkdir(parents=True)
    (other / "data" / "documents").mkdir(parents=True)
    twin = other / "data" / "documents" / "paper.pdf"
    twin.write_bytes(b"%PDF-1.4")
    twin_gate = FileGate(root=other / "examples" / "papers")
    twin_gate.admit([twin])

    assert list(twin_gate.seen) == list(gate.seen)


def test_iter_source_files_applies_the_active_gate(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    gate = FileGate(skip_all=True, root=tmp_path)

    with file_gate(gate):
        assert iter_source_files(tmp_path, "*.txt", "*.txt") == []
    # Outside the context the helper behaves exactly as before.
    assert len(iter_source_files(tmp_path, "*.txt", "*.txt")) == 2


def test_docling_json_directory_is_gated_too(tmp_path):
    """pdf.py used json_dir.glob() directly, bypassing the gate entirely."""
    from kb.parsers import pdf as pdf_parser

    (tmp_path / "one.json").write_text("{}", encoding="utf-8")
    gate = FileGate(skip_all=True, root=tmp_path)

    with file_gate(gate):
        sections = pdf_parser._sections_from_docling_json(
            tmp_path, ChunkingConfig(strategy="passthrough"), True
        )

    assert sections == []
    assert list(gate.seen) == ["one.json"]


# --------------------------------------------------------------------------- #
# docling_json_dir fills itself: a PDF without a JSON is converted once
# --------------------------------------------------------------------------- #
def _minimal_docling_document(title: str):
    from docling_core.types.doc import DoclingDocument
    from docling_core.types.doc.labels import DocItemLabel

    doc = DoclingDocument(name=title)
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text=title)
    doc.add_text(label=DocItemLabel.PARAGRAPH, text="Body text of the converted document, long enough to be a section. " * 4)
    return doc


class _StubConverter:
    """Stands in for Docling: counts calls, returns a minimal real document."""

    def __init__(self):
        self.calls: list[str] = []

    def convert(self, path: str):
        self.calls.append(Path(path).name)
        return type("Result", (), {"document": _minimal_docling_document(Path(path).stem)})()


@pytest.fixture
def cache_source(tmp_path, monkeypatch):
    from kb.parsers import pdf as pdf_parser

    pdfs, json_dir = tmp_path / "pdfs", tmp_path / "json"
    pdfs.mkdir()
    (pdfs / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    stub = _StubConverter()
    monkeypatch.setattr(pdf_parser, "_converter", lambda opts, images_scale=None: stub)
    # In-process, so the stub is what converts; the real child is tested on its own below.
    monkeypatch.setattr(pdf_parser, "_convert_in_child", pdf_parser._convert_into)
    config = _config_at(tmp_path, data_sources=[DataSourceConfig(
        name="papers", path="pdfs", format="pdf",
        pdf_options={"docling_json_dir": "json"},
        chunking=ChunkingConfig(strategy="passthrough"),
    )])
    return pdf_parser, config, config.data_sources[0], pdfs, json_dir, stub


def test_missing_json_is_converted_once_and_read(cache_source):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source

    sections = pdf_parser.parse_pdf(source, config)

    assert stub.calls == ["paper.pdf"]
    assert (json_dir / "paper.json").exists()
    assert sections and all(s.metadata["source_file"] == "paper.pdf" for s in sections)

    pdf_parser.parse_pdf(source, config)
    assert stub.calls == ["paper.pdf"], "a second run reads the cache, converts nothing"


def test_planning_pass_records_the_pdf_but_converts_nothing(cache_source):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    gate = FileGate(skip_all=True, stat_only=True, root=config._config_dir)

    with file_gate(gate):
        pdf_parser.parse_pdf(source, config)

    assert stub.calls == []
    assert not json_dir.exists()
    assert "pdfs/paper.pdf" in gate.seen, "the watcher must see a new PDF as a change"


def test_deleted_json_is_regenerated_even_for_an_unchanged_pdf(cache_source):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    pdf_parser.parse_pdf(source, config)
    (json_dir / "paper.json").unlink()

    gate = FileGate(known={"pdfs/paper.pdf": file_digest(pdfs / "paper.pdf")}, root=config._config_dir)
    with file_gate(gate):
        pdf_parser.parse_pdf(source, config)

    assert stub.calls == ["paper.pdf", "paper.pdf"]
    assert (json_dir / "paper.json").exists()

def test_a_pdf_that_fails_to_convert_is_skipped_not_fatal(cache_source, capsys):
    """One encrypted or broken PDF must not stop the run: the watcher would retry
    the same failing pass every tick and nothing new would get indexed."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    (pdfs / "bad.pdf").write_bytes(b"%PDF-1.4 broken")
    good = stub.convert

    def convert(path):
        if Path(path).name == "bad.pdf":
            raise RuntimeError("encrypted")
        return good(path)

    stub.convert = convert
    sections = pdf_parser.parse_pdf(source, config)

    assert (json_dir / "paper.json").exists() and not (json_dir / "bad.json").exists()
    assert {s.metadata["source_file"] for s in sections} == {"paper.pdf"}
    assert "bad.pdf: conversion failed" in capsys.readouterr().out
    assert not list(json_dir.glob("*.tmp")), "the temporary file is removed on failure"


def test_a_replaced_pdf_is_converted_again_even_with_the_same_mtime(cache_source):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    pdf_parser.parse_pdf(source, config)
    stat = (pdfs / "paper.pdf").stat()
    (pdfs / "paper.pdf").write_bytes(b"%PDF-1.4 a better scan")
    os.utime(pdfs / "paper.pdf", (stat.st_atime, stat.st_mtime))  # mtimes alone would say: unchanged

    pdf_parser.parse_pdf(source, config)
    assert stub.calls == ["paper.pdf", "paper.pdf"]

    pdf_parser.parse_pdf(source, config)
    assert stub.calls == ["paper.pdf", "paper.pdf"], "the stamp matches again"


def test_a_docling_upgrade_converts_again(cache_source, monkeypatch):
    """Upgrading Docling changes neither the PDF nor the JSON, so nothing else
    would ever notice that the folder still holds the old version's text."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    pdf_parser.parse_pdf(source, config)
    monkeypatch.setattr(pdf_parser, "version", lambda dist: "99.0.0")

    pdf_parser.parse_pdf(source, config)
    assert stub.calls == ["paper.pdf", "paper.pdf"]
    assert "docling 99.0.0" in (json_dir / "paper.json.stamp").read_text()


def test_changed_conversion_settings_convert_again(cache_source):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    pdf_parser.parse_pdf(source, config)
    source.pdf_options.ocr = True

    pdf_parser.parse_pdf(source, config)
    assert stub.calls == ["paper.pdf", "paper.pdf"]


def test_a_json_without_a_stamp_is_trusted(cache_source):
    """A hand export (``docling --to json``) or a folder from before the stamps
    carries no stamp. It is kept as it is; deleting it is the way to reconvert."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    pdf_parser.parse_pdf(source, config)
    (json_dir / "paper.json.stamp").unlink()

    pdf_parser.parse_pdf(source, config)
    assert stub.calls == ["paper.pdf"]


def test_pdfs_sharing_a_stem_are_reported_and_neither_is_converted(cache_source, capsys):
    """``**/*.pdf`` can select ``a/report.pdf`` and ``b/report.pdf``; both would
    write ``report.json`` and the second would silently win."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    for folder in ("a", "b"):
        (pdfs / folder).mkdir()
        (pdfs / folder / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    source.glob = "**/*.pdf"

    pdf_parser.parse_pdf(source, config)

    assert stub.calls == ["paper.pdf"] and not (json_dir / "report.json").exists()
    assert "report: 2 PDFs share this name" in capsys.readouterr().out


def test_a_single_pdf_file_as_path_is_converted_too(cache_source, tmp_path):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    single = _config_at(tmp_path, data_sources=[DataSourceConfig(
        name="one", path="pdfs/paper.pdf", format="pdf",
        pdf_options={"docling_json_dir": "json"},
        chunking=ChunkingConfig(strategy="passthrough"),
    )])

    sections = pdf_parser.parse_pdf(single.data_sources[0], single)

    assert stub.calls == ["paper.pdf"] and (json_dir / "paper.json").exists()
    assert sections and all(s.metadata["source_file"] == "paper.pdf" for s in sections)


def test_the_cache_fill_converts_without_figure_images(cache_source, monkeypatch):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    seen: list[float | None] = []
    monkeypatch.setattr(
        pdf_parser, "_converter",
        lambda opts, images_scale=None: seen.append(images_scale) or stub,
    )

    pdf_parser.parse_pdf(source, config)

    assert seen == [None], "the JSON reader never uses figures, the cache must not carry them"


def test_a_missing_parent_folder_means_no_conversion(cache_source, tmp_path, capsys):
    """An unmounted volume looks like a missing folder; converting the whole
    corpus into a folder created inside the container would be wasted work."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    cfg = _config_at(tmp_path, data_sources=[DataSourceConfig(
        name="papers", path="pdfs", format="pdf",
        pdf_options={"docling_json_dir": "not-mounted/json"},
        chunking=ChunkingConfig(strategy="passthrough"),
    )])

    pdf_parser.parse_pdf(cfg.data_sources[0], cfg)

    assert stub.calls == [] and not (tmp_path / "not-mounted").exists()
    assert "parent folder does not exist" in capsys.readouterr().out


def test_a_dry_run_gate_reads_but_converts_nothing(cache_source):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    with file_gate(FileGate(convert=False, root=config._config_dir)):
        pdf_parser.parse_pdf(source, config)

    assert stub.calls == [] and not json_dir.exists()


def test_the_conversion_runs_in_a_child_process(cache_source, monkeypatch, capfd):
    """Docling's memory only comes back when the process that loaded it exits, so
    the fill converts in a child. Real child here, with a PDF that does not exist:
    it must start, import the app, report the failure and exit cleanly."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    monkeypatch.undo()  # the real _converter and the real child
    json_dir.mkdir()

    pdf_parser._convert_in_child([pdfs / "missing.pdf"], json_dir, source.pdf_options)

    out = capfd.readouterr().out
    assert "missing.pdf: conversion failed, skipped" in out
    assert "conversion process exited" not in out
    assert not list(json_dir.iterdir())


def test_a_dead_conversion_process_is_reported_not_raised(cache_source, monkeypatch, capsys):
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    monkeypatch.undo()  # the real _convert_in_child, whose subprocess we fake
    monkeypatch.setattr(
        pdf_parser.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": -11})()
    )

    sections = pdf_parser.parse_pdf(source, config)

    assert sections == [] and stub.calls == []
    assert "conversion process exited with -11" in capsys.readouterr().out


def test_plan_ingest_without_a_gate_converts_nothing(cache_source):
    """``plan_ingest`` promises no I/O beyond reading; only ``ingest_all`` hands
    in a gate that may convert."""
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    per_source, chunks = pipeline.plan_ingest(config)

    assert stub.calls == [] and not json_dir.exists()
    assert per_source[0]["sections"] == 0, "nothing to read yet, so nothing planned"



# --------------------------------------------------------------------------- #
# A fake Qdrant client, just enough for ingest_all
# --------------------------------------------------------------------------- #
class _Record:
    def __init__(self, payload=None, vector=None):
        self.payload = payload
        self.vector = vector


class FakeClient:
    def __init__(self, collections=()):
        self.points: dict[str, _Record] = {}
        self.collections = set(collections)
        self.upserts: list[list] = []
        self.deletes: list = []

    def _matching(self, condition):
        """Resolve a MatchAny filter on source_file against the stored payloads."""
        wanted: set[str] = set()
        for cond in condition.must:
            wanted.update(cond.match.any)
        return [
            pid
            for pid, rec in self.points.items()
            if (rec.payload or {}).get("source_file") in wanted
        ]

    def count(self, collection_name, count_filter=None, exact=True):
        n = len(self._matching(count_filter)) if count_filter else len(self.points)
        return type("C", (), {"count": n})()

    def delete(self, collection_name, points_selector=None):
        self.deletes.append(points_selector)
        for pid in self._matching(points_selector.filter):
            del self.points[pid]

    def get_collections(self):
        names = [type("C", (), {"name": n})() for n in self.collections]
        return type("R", (), {"collections": names})()

    def retrieve(self, collection_name, ids, with_payload=False, with_vectors=False):
        return [self.points[i] for i in ids if i in self.points]

    def scroll(self, collection_name, limit=1000, offset=None, **kw):
        payloads = [
            _Record(payload=r.payload) for r in self.points.values() if r.payload
        ]
        return payloads, None

    def upsert(self, collection_name, points):
        self.upserts.append(points)
        for point in points:
            self.points[point.id] = _Record(payload=point.payload, vector=point.vector)

    def create_collection(self, collection_name, vectors_config=None, sparse_vectors_config=None):
        self.collections.add(collection_name)
        self.sparse_config = sparse_vectors_config

    def get_collection(self, name):
        # Collections seeded via the constructor mimic legacy dense-only ones;
        # those created through create_collection report what was configured.
        sparse = getattr(self, "sparse_config", None)
        params = type("P", (), {"sparse_vectors": dict(sparse) if sparse else None})()
        return type("I", (), {"config": type("Cfg", (), {"params": params})()})()

    def delete_collection(self, collection_name, timeout=None):
        self.collections.discard(collection_name)
        self.points.clear()

    def create_payload_index(self, **kw):
        return None


@pytest.fixture
def fake_embed(monkeypatch):
    calls: list[list[str]] = []

    async def embed(texts):
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    import llm

    monkeypatch.setattr(llm, "embed", embed)
    return calls


def _run(config, client, monkeypatch, **kw):
    monkeypatch.setattr(pipeline, "get_client", lambda: client)
    return asyncio.run(pipeline.ingest_all(config, **kw))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def test_first_ingest_writes_a_manifest(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()

    result = _run(_text_config(tmp_path), client, monkeypatch)

    assert result["ingested"] == 1
    stored = pipeline.read_manifest(client, "documents")
    assert list(stored) == ["docs/a.txt"]


def test_ingest_verifies_the_config_it_was_handed_not_the_singleton(
    tmp_path, monkeypatch, fake_embed
):
    """`kb.ingest --config other.yaml` loads a config that need not be the process
    singleton, so the lexical-format check must read the argument. Pinned here
    because ingest_all is the only place it runs: ingest_chunks used to repeat it
    with the same collection and the same flag, which the cache made a no-op."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    config = _text_config(tmp_path)
    config.retrieval.hybrid = True
    config.vector_store.collection = "other"

    seen: list[tuple[str, bool]] = []

    def spy(client, collection, *, hybrid):
        seen.append((collection, hybrid))
        return None

    monkeypatch.setattr(pipeline, "lexical_rebuild_reason", spy)
    _run(config, FakeClient(), monkeypatch)

    assert seen == [("other", True)]


def test_second_run_does_nothing(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)

    _run(config, client, monkeypatch)
    fake_embed.clear()
    result = _run(config, client, monkeypatch)

    assert result["ingested"] == 0
    assert result["skipped"] == 1
    assert fake_embed == [], "an unchanged corpus must not be embedded again"


def test_a_new_file_is_picked_up_without_touching_the_rest(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    # This is the reported bug: adding a document and running again did nothing.
    (docs / "b.txt").write_text("beta content", encoding="utf-8")
    fake_embed.clear()
    result = _run(config, client, monkeypatch)

    assert result["ingested"] == 1
    assert result["skipped"] == 1
    assert any("beta content" in t for batch in fake_embed for t in batch)
    assert not any("alpha content" in t for batch in fake_embed for t in batch)
    assert set(pipeline.read_manifest(client, "documents")) == {"docs/a.txt", "docs/b.txt"}


def test_recreate_ignores_the_manifest(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    fake_embed.clear()
    result = _run(config, client, monkeypatch, recreate=True)

    assert result["ingested"] == 1
    assert any("alpha content" in t for batch in fake_embed for t in batch)


def test_existing_collection_without_a_manifest_is_adopted(tmp_path, monkeypatch, fake_embed, capsys):
    """Every collection built by the old code hits this path.

    Treating it as empty would re-embed the whole corpus, and with
    images.mode: describe re-describe every figure, so adoption records the files
    and ingests nothing.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")

    client = FakeClient(collections={"documents"})
    # A pre-manifest collection: a sentinel and a chunk, but no manifest point.
    client.points[pipeline._point_id(pipeline._SENTINEL_KEY)] = _Record(
        payload={"_meta": True, "embed_model": "m"}, vector=[0.1, 0.2, 0.3]
    )
    client.points["chunk-1"] = _Record(payload={"source_file": "a.txt", "text": "alpha"})

    result = _run(_text_config(tmp_path), client, monkeypatch, recreate=False)

    assert result["ingested"] == 0
    assert result["adopted"] == 1
    assert fake_embed == [], "adoption must not embed anything"
    assert list(pipeline.read_manifest(client, "documents")) == ["docs/a.txt"]
    assert "adopting existing collection" in capsys.readouterr().out


def test_adoption_warns_about_files_that_have_no_chunks(tmp_path, monkeypatch, fake_embed, capsys):
    """A document the user believes is searchable but is not."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "indexed.txt").write_text("in the collection", encoding="utf-8")
    (docs / "missing.txt").write_text("never ingested", encoding="utf-8")

    client = FakeClient(collections={"documents"})
    client.points[pipeline._point_id(pipeline._SENTINEL_KEY)] = _Record(
        payload={"_meta": True, "embed_model": "m"}, vector=[0.1, 0.2, 0.3]
    )
    client.points["chunk-1"] = _Record(payload={"source_file": "indexed.txt", "text": "x"})

    _run(_text_config(tmp_path), client, monkeypatch)

    out = capsys.readouterr().out
    assert "docs/missing.txt" in out
    assert "docs/indexed.txt" not in out.split("WARNING")[-1]


# --------------------------------------------------------------------------- #
# Deleted documents
# --------------------------------------------------------------------------- #
def _sources_in(client) -> set[str]:
    return {
        (r.payload or {}).get("source_file")
        for r in client.points.values()
        if r.payload and not r.payload.get("_meta")
    }


def test_a_deleted_document_loses_its_entries(tmp_path, monkeypatch, fake_embed):
    """Otherwise the assistant keeps answering from, and citing, a file that is gone."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "keep.txt").write_text("kept content", encoding="utf-8")
    (docs / "gone.txt").write_text("secret project zeta", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)
    assert _sources_in(client) == {"keep.txt", "gone.txt"}

    (docs / "gone.txt").unlink()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 1
    assert _sources_in(client) == {"keep.txt"}
    # Dropped from the manifest too, so the file coming back later is ingested again.
    assert list(pipeline.read_manifest(client, "documents")) == ["docs/keep.txt"]


def test_replacing_the_whole_corpus_prunes_and_ingests_in_one_run(
    tmp_path, monkeypatch, fake_embed
):
    """Delete every old document and add new ones: both halves happen at once."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "old_a.txt").write_text("old alpha", encoding="utf-8")
    (docs / "old_b.txt").write_text("old beta", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    for name in ("old_a.txt", "old_b.txt"):
        (docs / name).unlink()
    (docs / "new_a.txt").write_text("new alpha", encoding="utf-8")
    (docs / "new_b.txt").write_text("new beta", encoding="utf-8")
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 2, "old entries must go"
    assert result["ingested"] == 2, "new documents must be indexed in the same run"
    assert _sources_in(client) == {"new_a.txt", "new_b.txt"}
    assert set(pipeline.read_manifest(client, "documents")) == {
        "docs/new_a.txt",
        "docs/new_b.txt",
    }


def test_one_deleted_one_added_and_the_rest_untouched(tmp_path, monkeypatch, fake_embed):
    """The everyday case: swap a single document out of a larger set.

    All three states occur in one run, which the other tests only cover in
    isolation: two files unchanged, one pruned, one ingested.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, text in (
        ("stay_a.txt", "alpha stays"),
        ("stay_b.txt", "beta stays"),
        ("outgoing.txt", "outgoing content"),
    ):
        (docs / name).write_text(text, encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)
    assert _sources_in(client) == {"stay_a.txt", "stay_b.txt", "outgoing.txt"}

    (docs / "outgoing.txt").unlink()
    (docs / "incoming.txt").write_text("incoming content", encoding="utf-8")
    fake_embed.clear()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 1
    assert result["ingested"] == 1
    assert result["skipped"] == 2
    assert _sources_in(client) == {"stay_a.txt", "stay_b.txt", "incoming.txt"}
    assert set(pipeline.read_manifest(client, "documents")) == {
        "docs/stay_a.txt",
        "docs/stay_b.txt",
        "docs/incoming.txt",
    }
    # The two survivors must not have been re-embedded.
    embedded = [t for batch in fake_embed for t in batch]
    assert any("incoming content" in t for t in embedded)
    assert not any("stays" in t for t in embedded)


def test_an_empty_folder_is_not_treated_as_a_deletion(tmp_path, monkeypatch, fake_embed, capsys):
    """The footgun: a bind mount that did not come up would otherwise wipe everything."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    (docs / "a.txt").unlink()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 0
    assert _sources_in(client) == {"a.txt"}, "nothing may be deleted"
    out = capsys.readouterr().out
    assert "Refusing to treat that as a deletion" in out
    assert "--recreate" in out, "must name the intentional way to empty a collection"


def test_only_does_not_prune_the_other_sources(tmp_path, monkeypatch, fake_embed):
    """--only looks at a subset, so the unvisited sources' files are not deletions."""
    docs = tmp_path / "docs"
    docs.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (other / "b.txt").write_text("beta", encoding="utf-8")
    config = _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="docs", path="docs", format="txt", glob="*.txt"),
            DataSourceConfig(name="other", path="other", format="txt", glob="*.txt"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )
    client = FakeClient()
    _run(config, client, monkeypatch)
    assert _sources_in(client) == {"a.txt", "b.txt"}

    (docs / "a.txt").write_text("alpha edited", encoding="utf-8")
    result = _run(config, client, monkeypatch, only={"docs"})

    assert result["pruned"] == 0
    assert _sources_in(client) == {"a.txt", "b.txt"}, "b.txt was never visited, not deleted"
    assert set(pipeline.read_manifest(client, "documents")) == {"docs/a.txt", "other/b.txt"}


def test_deleting_one_of_two_files_with_the_same_name_keeps_the_survivor(
    tmp_path, monkeypatch, fake_embed, capsys
):
    """Data loss, found by probing: entries are matched by file name only.

    Two sources can each hold an `intro.txt`. Pruning the deleted one by name would
    delete the surviving one's entries as well, which is exactly what happened
    before this guard: the collection was left empty.
    """
    for folder in ("handbooks", "papers"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "intro.txt").write_text(f"{folder} introduction", encoding="utf-8")
    config = _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="handbooks", path="handbooks", format="txt", glob="*.txt"),
            DataSourceConfig(name="papers", path="papers", format="txt", glob="*.txt"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )
    client = FakeClient()
    _run(config, client, monkeypatch)

    (tmp_path / "handbooks" / "intro.txt").unlink()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 0, "must not delete by an ambiguous name"
    assert _sources_in(client) == {"intro.txt"}, "the surviving document must keep its entries"
    out = capsys.readouterr().out
    assert "not removing entries for handbooks/intro.txt" in out


def test_removing_a_pdf_and_its_json_together_prints_no_warning(
    cache_source, monkeypatch, fake_embed, capsys
):
    """With a JSON folder a document is two files that index under one name.

    Pruning met the same document twice: the first removed file took the entries,
    the second found none left and was reported as not safely removable, with the
    advice to --recreate, after every routine removal of a document.
    """
    pdf_parser, config, source, pdfs, json_dir, stub = cache_source
    (pdfs / "other.pdf").write_bytes(b"%PDF-1.4 fake")  # a survivor, so the folder is not empty
    client = FakeClient()
    _run(config, client, monkeypatch)
    assert _sources_in(client) == {"paper.pdf", "other.pdf"}

    (pdfs / "paper.pdf").unlink()
    (json_dir / "paper.json").unlink()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] >= 1 and _sources_in(client) == {"other.pdf"}
    assert "could not be removed safely" not in capsys.readouterr().out
    assert not (json_dir / "paper.json.stamp").exists(), "the stamp goes with its JSON"


def test_deleting_two_files_that_share_a_stem_removes_both(
    tmp_path, monkeypatch, fake_embed, capsys
):
    """`_payload_names_for` lists `<stem>.pdf` for every file, so `intro.txt` and
    `intro.md` overlap by name. A first version of the pair skip took the second
    one for the other half of a PDF/JSON pair and left its entries behind."""
    for folder, ext in (("a", "txt"), ("b", "md")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / f"intro.{ext}").write_text(f"{folder} introduction", encoding="utf-8")
        (tmp_path / folder / f"keep.{ext}").write_text(f"{folder} kept", encoding="utf-8")
    config = _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="a", path="a", format="txt", glob="*.txt"),
            DataSourceConfig(name="b", path="b", format="md", glob="*.md"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )
    client = FakeClient()
    _run(config, client, monkeypatch)
    assert _sources_in(client) == {"intro.txt", "keep.txt", "intro.md", "keep.md"}

    (tmp_path / "a" / "intro.txt").unlink()
    (tmp_path / "b" / "intro.md").unlink()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 2
    assert _sources_in(client) == {"keep.txt", "keep.md"}
    assert "could not be removed safely" not in capsys.readouterr().out


def test_an_unmatched_non_pdf_key_is_still_reported_after_a_same_stem_key():
    """Only ``.pdf`` and ``.json`` keys index under ``<stem>.pdf``. Giving every key
    that second name let a handled ``intro.txt`` hide an unmatched ``intro.md``."""
    deleted, unmatched = pipeline._prune_removed(
        FakeClient(), "c", ["a/intro.txt", "b/intro.md"], keep_names=set()
    )

    assert deleted == 0 and unmatched == ["a/intro.txt", "b/intro.md"]


def test_duplicate_file_names_are_reported(tmp_path, monkeypatch, fake_embed, capsys):
    """The pre-existing collision behind the above, previously silent.

    doc_id comes from the file name and the point id from doc_id, so two files with
    the same name produce the same id and one overwrites the other. Not fixed here,
    because changing the derivation invalidates every existing point id, but no
    longer silent.
    """
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "same.txt").write_text(f"content {folder}", encoding="utf-8")
    config = _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="a", path="a", format="txt", glob="*.txt"),
            DataSourceConfig(name="b", path="b", format="txt", glob="*.txt"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )
    _run(config, FakeClient(), monkeypatch)

    out = capsys.readouterr().out
    assert "occur more than once" in out
    assert "same.txt: a/same.txt, b/same.txt" in out


def test_a_renamed_file_moves_with_its_entries(tmp_path, monkeypatch, fake_embed):
    """Same bytes, new name: the old entries go and the new name is indexed."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "old_name.txt").write_text("stable content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    (docs / "old_name.txt").rename(docs / "new_name.txt")
    result = _run(config, client, monkeypatch)

    assert (result["pruned"], result["ingested"]) == (1, 1)
    assert _sources_in(client) == {"new_name.txt"}
    assert list(pipeline.read_manifest(client, "documents")) == ["docs/new_name.txt"]


def test_unidentifiable_entries_are_reported_not_silently_kept(
    tmp_path, monkeypatch, fake_embed, capsys
):
    """A csv/json field_mapping may write no source_file, so nothing matches."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (docs / "gone.txt").write_text("beta", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    # Strip the identifying metadata, as a custom field_mapping would.
    for rec in client.points.values():
        if rec.payload and rec.payload.get("source_file") == "gone.txt":
            rec.payload.pop("source_file")
    (docs / "gone.txt").unlink()
    result = _run(config, client, monkeypatch)

    assert result["pruned"] == 0
    out = capsys.readouterr().out
    assert "could not be removed safely" in out
    assert "docs/gone.txt" in out


def test_adoption_matches_the_pdf_naming_convention(tmp_path, monkeypatch, fake_embed, capsys):
    """The PDF parser stores '<stem>.pdf' as source_file, so a .json source
    indexes X.pdf. Adoption must not report such a file as missing."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "paper.json").write_text("{}", encoding="utf-8")

    client = FakeClient(collections={"documents"})
    client.points[pipeline._point_id(pipeline._SENTINEL_KEY)] = _Record(
        payload={"_meta": True, "embed_model": "m"}, vector=[0.1, 0.2, 0.3]
    )
    client.points["chunk-1"] = _Record(payload={"source_file": "paper.pdf", "text": "x"})

    config = _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="docs", path="docs", format="txt", glob="*.json"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )
    _run(config, client, monkeypatch)

    assert "WARNING" not in capsys.readouterr().out


def test_a_dense_only_collection_is_rebuilt_instead_of_refused(
    tmp_path, monkeypatch, fake_embed, capsys
):
    """Ingest is the only caller that can fix a stale lexical index, so it does.
    Refusing here just told the reader to re-run the same command with a flag."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    config = _text_config(tmp_path)
    config.retrieval.hybrid = True

    client = FakeClient()
    monkeypatch.setattr(
        pipeline, "lexical_rebuild_reason", lambda *a, **k: "no lexical vector"
    )
    recreated: list[bool] = []
    real = pipeline._ensure_collection
    monkeypatch.setattr(
        pipeline,
        "_ensure_collection",
        lambda c, n, s, d, recreate: (recreated.append(recreate), real(c, n, s, d, recreate))[1],
    )

    _run(config, client, monkeypatch)

    assert recreated == [True], "the collection has to be rebuilt, not written into"
    out = capsys.readouterr().out
    assert "no lexical vector" in out, "the reason has to be stated"
    assert "re-embedded" in out, (
        "the cost has to be stated — this is billed gateway traffic, and silence "
        "makes it look like an incremental run that mysteriously took an hour"
    )


def test_a_healthy_collection_is_not_rebuilt(tmp_path, monkeypatch, fake_embed):
    """The rebuild is expensive, so it must fire only on a real defect."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")

    client = FakeClient()
    monkeypatch.setattr(pipeline, "lexical_rebuild_reason", lambda *a, **k: None)
    recreated: list[bool] = []
    real = pipeline._ensure_collection
    monkeypatch.setattr(
        pipeline,
        "_ensure_collection",
        lambda c, n, s, d, recreate: (recreated.append(recreate), real(c, n, s, d, recreate))[1],
    )

    _run(_text_config(tmp_path), client, monkeypatch)

    assert recreated == [False], "nothing was wrong; nothing should be re-embedded"
