# Extending: custom parsers & chunkers

Most corpora are covered by the built-in `pdf`, `txt`/`md`, `json` and `csv`
parsers plus the `fixed_size`/`heading`/`passthrough`/`semantic`/`docling_hybrid`
chunkers. When your data has irreducibly custom structure, register your own.

## A custom parser

A parser takes the source config + the full config and returns a list of
`Section` objects (`text`, `metadata`, `doc_id`). Register it by name; the
ingestion pipeline dispatches on `format: custom` + `parser_name`.

```python
# apps/chainlit/kb/parsers/my_parser.py
from kb.parsers.base import Section, iter_source_files
from kb.parsers import register_parser


@register_parser("my_format")
def parse_my_format(source, config) -> list[Section]:
    base = config.resolve_path(source.path)
    sections: list[Section] = []
    for path in iter_source_files(base, source.glob, "*.myext"):
        for i, record in enumerate(_read(path)):
            sections.append(
                Section(
                    text=record["body"],
                    doc_id=f"{path.stem}:{i}",
                    metadata={"source_file": path.name, "title": record["title"]},
                )
            )
    return sections
```

Import it once so the decorator runs, by adding it to the import list in
`kb/parsers/__init__.py`. Then use it:

```yaml
data_sources:
  - name: mine
    path: ./data/mine
    format: custom
    parser_name: my_format
    chunking: {strategy: passthrough}
```

!!! tip "Metadata keys"
    Use `source_file`/`file`, `title`/`section_title`, `page_start`/`page_end`
    so the built-in citation renderer works out of the box. Anything extra can
    be surfaced in citations via `citation.extra_fields`.

The bundled [`kb/parsers/example_custom.py`](https://github.com/aihpi/pilotproject-rag-template/blob/main/apps/chainlit/kb/parsers/example_custom.py)
is a runnable example (a JSON Lines parser, registered as `example_jsonl`). Copy
that file to start your own.

## A custom chunker

A chunker takes the parser's `Section`s plus the effective `ChunkingConfig` and
returns `Chunk`s (`text`, `metadata`, `doc_id`).

```python
# apps/chainlit/kb/chunkers/sentence.py
from kb.chunkers.base import Chunk
from kb.chunkers import register_chunker


@register_chunker("sentence")
def chunk_sentences(sections, cfg) -> list[Chunk]:
    chunks = []
    for section in sections:
        for i, sentence in enumerate(_split_sentences(section.text), start=1):
            chunks.append(Chunk(
                text=sentence,
                metadata={**section.metadata, "chunk_index": i},
                doc_id=f"{section.doc_id}:s{i}",
            ))
    return chunks
```

Register it in `kb/chunkers/__init__.py`, then select it:

```yaml
chunking:
  strategy: sentence
```

`doc_id` must be **stable and unique**: the pipeline derives each Qdrant point
id from it (UUID5), so a deterministic id keeps re-ingests idempotent.
