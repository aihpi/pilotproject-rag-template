"""sources.served_extensions has to actually gate what /sources serves.

It was dead: shipped set in three configs, documented in the README and both
adding-data pages, asserted in a test, and read by nothing. The route hardcoded
".pdf", so adding ".md" and expecting Markdown sources to open gave a silent 404.
"""

import mimetypes
from pathlib import Path

import app as chainlit_app


def _with_extensions(monkeypatch, tmp_path, extensions):
    cfg = chainlit_app.get_config()
    monkeypatch.setattr(cfg.sources, "served_extensions", extensions)
    monkeypatch.setattr(chainlit_app, "DATA_RAW_DIR", tmp_path)
    return cfg


def test_a_listed_extension_is_served(monkeypatch, tmp_path):
    (tmp_path / "notes.md").write_text("# hello", encoding="utf-8")
    _with_extensions(monkeypatch, tmp_path, [".pdf", ".md"])

    assert chainlit_app._resolve_source_pdf_path("notes.md") == tmp_path / "notes.md"


def test_an_unlisted_extension_is_refused(monkeypatch, tmp_path):
    (tmp_path / "secrets.env").write_text("KEY=1", encoding="utf-8")
    _with_extensions(monkeypatch, tmp_path, [".pdf", ".md"])

    assert chainlit_app._resolve_source_pdf_path("secrets.env") is None


def test_extensions_are_normalised(monkeypatch, tmp_path):
    """A config may write them without a dot, or in capitals."""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    _with_extensions(monkeypatch, tmp_path, ["md", ".PDF"])

    assert chainlit_app._served_suffixes() == {".md", ".pdf"}
    assert chainlit_app._resolve_source_pdf_path("a.md") is not None


def test_traversal_is_still_refused(monkeypatch, tmp_path):
    """The gate that matters most must survive the change."""
    _with_extensions(monkeypatch, tmp_path, [".md"])

    for name in ("../app.py", "sub/a.md", "/etc/passwd"):
        assert chainlit_app._resolve_source_pdf_path(name) is None


def test_the_media_type_matches_the_file():
    """Everything used to be served as application/pdf."""
    assert mimetypes.guess_type("a.md")[0] == "text/markdown"
    assert mimetypes.guess_type("a.pdf")[0] == "application/pdf"


def _with_sources(monkeypatch, data_dir, source_paths, extensions=(".pdf",)):
    """Stub the whole config: `resolve_path` is a method, so pydantic refuses to
    let monkeypatch set it on a real instance."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        sources=SimpleNamespace(served_extensions=list(extensions)),
        data_sources=[SimpleNamespace(path=str(p)) for p in source_paths],
        resolve_path=lambda p: Path(p),
    )
    monkeypatch.setattr(chainlit_app, "get_config", lambda: cfg)
    monkeypatch.setattr(chainlit_app, "DATA_RAW_DIR", data_dir)
    return cfg


def test_a_second_data_source_folder_is_served(monkeypatch, tmp_path):
    """The bug this grew for: a corpus split across folders.

    `source_file` is a bare basename, and resolution only ever looked in
    `sources.data_dir`, so a citation into any other declared folder resolved to
    None and the citation stayed plain text.
    """
    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    (primary / "a.pdf").write_bytes(b"%PDF-1.4")
    (secondary / "b.pdf").write_bytes(b"%PDF-1.4")

    _with_sources(monkeypatch, primary, [secondary])

    assert chainlit_app._resolve_source_pdf_path("a.pdf") == primary / "a.pdf"
    assert chainlit_app._resolve_source_pdf_path("b.pdf") == secondary / "b.pdf"
    assert chainlit_app._resolve_source_pdf_path("nope.pdf") is None


def test_data_dir_wins_a_basename_collision(monkeypatch, tmp_path):
    """Same filename in two folders: data_dir first, as a single-folder instance did."""
    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    (primary / "dup.pdf").write_bytes(b"%PDF-primary")
    (secondary / "dup.pdf").write_bytes(b"%PDF-secondary")

    _with_sources(monkeypatch, primary, [secondary])

    assert chainlit_app._resolve_source_pdf_path("dup.pdf") == primary / "dup.pdf"


def test_a_file_path_data_source_serves_its_folder(monkeypatch, tmp_path):
    """`data_sources[].path` may name one file rather than a directory."""
    primary, other = tmp_path / "primary", tmp_path / "other"
    primary.mkdir()
    other.mkdir()
    single = other / "one.pdf"
    single.write_bytes(b"%PDF-1.4")

    _with_sources(monkeypatch, primary, [single])

    assert chainlit_app._resolve_source_pdf_path("one.pdf") == single


def test_traversal_is_refused_across_every_root(monkeypatch, tmp_path):
    """The gate that matters most, now that there is more than one root."""
    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-secret")

    _with_sources(monkeypatch, primary, [secondary])

    for name in ("../secret.pdf", "sub/a.pdf", "/etc/passwd", "..%2Fsecret.pdf"):
        assert chainlit_app._resolve_source_pdf_path(name) is None


def test_a_file_in_a_subfolder_is_served(monkeypatch, tmp_path):
    """A source indexed with a recursive glob still has clickable citations.

    `**/*.pdf` indexes nested files, parsers store the bare name, and resolution
    only ever looked at the top of each root -- so every nested document was
    searchable, answerable, and its citation rendered as plain text.
    """
    docs = tmp_path / "share"
    (docs / "Abteilung_A" / "2024").mkdir(parents=True)
    (docs / "top.pdf").write_bytes(b"%PDF-top")
    (docs / "Abteilung_A" / "2024" / "nested.pdf").write_bytes(b"%PDF-nested")

    _with_sources(monkeypatch, docs, [])

    assert chainlit_app._resolve_source_pdf_path("top.pdf") == docs / "top.pdf"
    assert (
        chainlit_app._resolve_source_pdf_path("nested.pdf")
        == docs / "Abteilung_A" / "2024" / "nested.pdf"
    )


def test_the_top_level_copy_wins_over_a_nested_one(monkeypatch, tmp_path):
    """The direct lookup runs first, so adding the walk cannot change which
    file an existing instance served."""
    docs = tmp_path / "share"
    (docs / "sub").mkdir(parents=True)
    (docs / "dup.pdf").write_bytes(b"%PDF-top")
    (docs / "sub" / "dup.pdf").write_bytes(b"%PDF-nested")

    _with_sources(monkeypatch, docs, [])

    assert chainlit_app._resolve_source_pdf_path("dup.pdf") == docs / "dup.pdf"


def test_a_symlink_out_of_the_root_is_refused(monkeypatch, tmp_path):
    """The walk must not become a way out of the served folder."""
    docs = tmp_path / "share"
    (docs / "sub").mkdir(parents=True)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-secret")
    (docs / "sub" / "escape.pdf").symlink_to(secret)

    _with_sources(monkeypatch, docs, [])

    assert chainlit_app._resolve_source_pdf_path("escape.pdf") is None


def test_a_nested_file_with_an_unlisted_extension_is_still_refused(monkeypatch, tmp_path):
    """served_extensions gates the walk too, not just the direct lookup."""
    docs = tmp_path / "share"
    (docs / "sub").mkdir(parents=True)
    (docs / "sub" / "secrets.env").write_text("KEY=1")

    _with_sources(monkeypatch, docs, [], extensions=(".pdf",))

    assert chainlit_app._resolve_source_pdf_path("secrets.env") is None
