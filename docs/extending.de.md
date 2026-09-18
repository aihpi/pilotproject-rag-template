# Erweitern: eigene Parser & Chunker

Die meisten Datenbestände sind durch die eingebauten Parser (`pdf`, `txt`/`md`,
`json`, `csv`) und die Chunker
(`fixed_size`/`heading`/`passthrough`/`semantic`/`docling_hybrid`) abgedeckt.
Wenn deine Daten eine nicht reduzierbare, spezielle Struktur haben, registriere
eigene.

## Ein eigener Parser

Ein Parser erhält die Quell-Konfiguration + die Gesamtkonfiguration und gibt eine
Liste von `Section`-Objekten zurück (`text`, `metadata`, `doc_id`). Registriere
ihn per Name; die Ingestion-Pipeline schaltet bei `format: custom` +
`parser_name` auf ihn um.

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

Importiere ihn einmal, damit der Decorator läuft, und füge ihn der Import-Liste in
`kb/parsers/__init__.py` hinzu. Dann verwenden:

```yaml
data_sources:
  - name: mine
    path: ./data/mine
    format: custom
    parser_name: my_format
    chunking: {strategy: passthrough}
```

!!! tip "Metadaten-Schlüssel"
    Verwende `source_file`/`file`, `title`/`section_title`,
    `page_start`/`page_end`, damit der eingebaute Zitat-Renderer sofort
    funktioniert. Zusätzliche Felder können über `citation.extra_fields` in Zitaten
    angezeigt werden.

Das mitgelieferte [`kb/parsers/example_custom.py`](https://github.com/aihpi/pilotproject-rag-template/blob/main/apps/chainlit/kb/parsers/example_custom.py)
ist ein lauffähiges Beispiel (ein JSON-Lines-Parser, registriert als
`example_jsonl`). Kopiere diese Datei als Ausgangspunkt für deinen eigenen.

## Ein eigener Chunker

Ein Chunker erhält die `Section`s des Parsers plus die effektive
`ChunkingConfig` und gibt `Chunk`s zurück (`text`, `metadata`, `doc_id`).

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

Registriere ihn in `kb/chunkers/__init__.py` und wähle ihn dann aus:

```yaml
chunking:
  strategy: sentence
```

`doc_id` muss **stabil und eindeutig** sein: Die Pipeline leitet daraus jede
Qdrant-Point-ID ab (UUID5), sodass eine deterministische ID wiederholte Ingests
idempotent hält.
