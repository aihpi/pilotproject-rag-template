"""Parser interface: a data source -> a list of :class:`Section`.

A parser owns its own file iteration (globbing a directory or reading a single
file) so format-specific logic (e.g. the Docling-JSON section reconstruction,
which spans a whole directory) stays inside the parser.

``Section.metadata`` uses the flat keys the retrieval/citation layer reads
(``source_file``/``file``/``source``, ``title``/``section_title``,
``page_start``/``page_end``, plus arbitrary extras). ``doc_id`` is a stable
string; the ingestion pipeline derives each Qdrant point id from it, so keeping
it deterministic keeps re-ingests idempotent.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

if TYPE_CHECKING:  # avoid import cycles at runtime
    from config.schema import DataSourceConfig, RagConfig


@dataclass
class Section:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_id: str | None = None


# A parser takes the source config + the full config and returns its sections.
ParserFn = Callable[["DataSourceConfig", "RagConfig"], list[Section]]


# --------------------------------------------------------------------------- #
# Incremental ingest: the file gate
# --------------------------------------------------------------------------- #
# The ingest pipeline needs to know which files a run would read, and to skip the
# ones already indexed, without changing ``ParserFn`` — that signature is a
# documented extension point, so widening it would break every custom parser.
# Every built-in parser enumerates through ``iter_source_files``, so the gate
# lives there and a context variable carries it. Parsers that do not use the
# helper simply get no filtering: correct, just not cheap, because point ids are
# deterministic and upserts are idempotent.
_FILE_GATE: ContextVar["FileGate | None"] = ContextVar("_FILE_GATE", default=None)

_HASH_CHUNK_BYTES = 1 << 20


def file_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks so large PDFs stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class FileGate:
    """Records every candidate file and hides the ones already ingested.

    ``known`` maps a gate key to the sha256 recorded at the last ingest. A file
    is skipped only when its hash still matches, so an *edited* document is
    re-ingested instead of going stale.

    ``skip_all`` enumerates and hashes without returning anything, which is how a
    run discovers the full file list while parsing nothing.

    ``stat_only`` goes further and records size plus modification time instead of a
    hash, so a caller can tell whether anything *might* have changed without reading
    a byte. The folder watcher polls with this: hashing the corpus every few seconds
    would be pointless work, while a handful of stat calls is free. It is a
    change *hint* only. Whether a file really changed is still decided by the hash
    on the following real run.
    """

    known: dict[str, str] = field(default_factory=dict)
    skip_all: bool = False
    stat_only: bool = False
    convert: bool = True   # False: parse everything, but convert and write nothing (--dry-run)
    root: Path | None = None
    seen: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def key(self, path: Path) -> str:
        """Stable identity for a file, relative to ``root``.

        Uses ``os.path.relpath`` rather than ``Path.relative_to`` because sources
        routinely sit *outside* the config directory: the shipped example config is
        in ``examples/papers/`` and points at ``../../data/documents``.
        ``relative_to`` cannot express that and would fall back to an absolute
        path, which differs between a Docker run (``/app/data/...``) and a local
        one (``/Users/.../data/...``). The manifest would then miss on every file
        and re-ingest the whole corpus, vision calls included.
        """
        resolved = path.resolve()
        if self.root:
            try:
                return Path(os.path.relpath(resolved, self.root.resolve())).as_posix()
            except ValueError:  # different drives on Windows
                pass
        return resolved.as_posix()

    def admit(self, paths: list[Path]) -> list[Path]:
        """Record each file, then return only those that need parsing."""
        admitted: list[Path] = []
        for path in paths:
            gate_key = self.key(path)
            if self.stat_only:
                try:
                    stat = path.stat()
                except OSError:  # vanished between listing and stat
                    continue
                self.seen[gate_key] = f"{stat.st_size}:{stat.st_mtime_ns}"
                self.skipped.append(gate_key)
                continue
            try:
                digest = file_digest(path)
            except OSError as exc:  # unreadable: let the parser report it
                print(f"[ingest] could not hash {path.name}: {exc}")
                admitted.append(path)
                continue
            self.seen[gate_key] = digest
            if self.skip_all or self.known.get(gate_key) == digest:
                self.skipped.append(gate_key)
                continue
            admitted.append(path)
        return admitted


def planning_pass() -> bool:
    """True while this run only looks: a ``skip_all`` or ``stat_only`` gate (the
    watcher's quick check) or ``convert=False`` (the dry run). Parsers may read
    under it, but must not convert or write anything."""
    gate = _FILE_GATE.get()
    return bool(gate and (gate.skip_all or gate.stat_only or not gate.convert))


@contextmanager
def file_gate(gate: FileGate | None) -> Iterator[FileGate | None]:
    """Apply ``gate`` to every ``iter_source_files`` call in this context."""
    token = _FILE_GATE.set(gate)
    try:
        yield gate
    finally:
        _FILE_GATE.reset(token)


def iter_source_files(
    base: Path,
    glob: str | None,
    default_glob: str,
) -> list[Path]:
    """Return the files a source points at (a single file or a globbed dir).

    Under an active :func:`file_gate` the result excludes files whose contents
    are already indexed. Every candidate is recorded first, so the caller still
    learns about skipped files.
    """
    if base.is_file():
        found = [base]
    elif base.is_dir():
        found = sorted(p for p in base.glob(glob or default_glob) if p.is_file())
    else:
        return []

    gate = _FILE_GATE.get()
    return gate.admit(found) if gate else found
