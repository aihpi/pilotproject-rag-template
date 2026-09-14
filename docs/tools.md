# Agentic tools

A simple document assistant does one search and answers from the result. This one
instead gets a **set of abilities** and decides for itself which to use, how
often, and in what order: list what documents exist, read a whole document,
fetch more text around a promising passage, or check a claim before stating it.

You decide which abilities it gets. Each one is a small file in
`apps/chainlit/tools/`.

```yaml
tools:
  enabled: [search, list_documents, fetch_document, expand_context, verify_claim]
  descriptions:
    list_documents: "your own wording, this is what the model reads"
  fetch_max_chunks: 200   # whole-document size cap for fetch_document
  expand_window: 1        # default neighbor window for expand_context
```

!!! note "The default is single-tool RAG"
    If you say nothing, the assistant only gets `search`, so an older setup keeps
    behaving exactly as it did. The order you list them in is the order the model
    is told about them. There is also a setting, `RAG_TOOLS_ENABLED`, which takes
    the names separated by `||`.

## The five built-in tools

| Tool | What it does | Produces sources |
|---|---|---|
| `search` | Finds the passages that best match a question | yes |
| `list_documents` | Lists what is in the knowledge base | no (just for orientation) |
| `fetch_document` | Reads one whole document from start to finish | yes |
| `expand_context` | Fetches the text around a passage | yes |
| `verify_claim` | Checks a statement against the documents before it is said | yes |

### `search`

The ordinary search, and the only one older setups had. It takes a `query`, a
number of results (`top_k`), and optionally one `document` to search inside. That
last one only works if you allow filtering by file name, via
`retrieval.filterable_fields: [source_file]`; otherwise it is quietly ignored.

For compatibility with older setups, this one tool takes its wording from the
`tool:` block rather than from `tools.descriptions`.

### `list_documents`

Lists every document: its exact file name, title, how many pieces it was split
into, and roughly how long it is. It takes no input and produces no sources,
because it is only there for orientation.

It solves a specific problem. People refer to documents loosely ("the Kage 2018
paper"), but `fetch_document` and `expand_context` need the exact file name. This
ability lets the assistant look that up. It also answers "which documents do you
have?" directly.

### `fetch_document`

Reads a **complete** document, all sections in the right order, by its exact file
name. This is the right choice for summaries and overviews, where ordinary search
only ever returns scattered fragments and the assistant would miss half the
document.

Very long documents are cut off at `tools.fetch_max_chunks`, and the assistant is
told when that happened, so it knows it did not see everything.

### `expand_context`

Fetches the sections just before and after a given one. Use it against the
classic problem that a found passage is too short: the assistant sees something
promising that looks cut off mid-thought and pulls in the neighbouring text
instead of guessing.

### `verify_claim`

A safeguard against invented answers. The assistant passes a sentence it is about
to write, and this searches the documents again for evidence. It reports back
whether the sentence is actually supported, so an unsupported claim can be
dropped or softened before anyone sees it.

## Descriptions are the prompt

The description of an ability is the only thing the model knows about it, which
makes it the most important thing you can tune. Every built-in one comes with
sensible wording in German and English, picked according to the `language:`
setting (see [Configuration](configuration.md)).

Override any of them under `tools.descriptions`. This is worth doing to use your
own vocabulary: "paper", "Baustein" or "ticket" instead of the generic
"document".

## Writing your own tool

This part needs Python. An ability consists of two pieces: a description of what
it takes as input, and a function that does the work. The types are in
[`tools/base.py`](https://github.com/aihpi/pilotprojekt-rag-template/blob/main/apps/chainlit/tools/base.py).

```python
# apps/chainlit/tools/count_pages.py
from typing import Any

from tools import register_tool
from tools.base import ToolContext, ToolResult


def _schema(cfg) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "count_pages",
            "description": "Page count of one document, by its exact source_file.",
            "parameters": {
                "type": "object",
                "properties": {"source_file": {"type": "string"}},
                "required": ["source_file"],
            },
        },
    }


@register_tool("count_pages", build_schema=_schema)
async def _count_pages(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import fetch_document          # inside the handler, see below

    results = await fetch_document(
        str(args.get("source_file") or ""),
        collection=ctx.collection,
        max_chunks=ctx.fetch_max_chunks,
    )
    pages = {(r.metadata or {}).get("page_start") for r in results} - {None}
    return ToolResult(payload={"pages": len(pages)}, results=[])
```

Save the file in `tools/` and add its name to `tools.enabled`. Every `.py` in
that folder is imported at startup, so the decorator runs and the tool is
registered; there is no list to maintain.

!!! warning "Two rules that will bite you"
    **Sources come from `results`.** Whatever you put in `ToolResult.results`
    must have `.text`, `.score` and `.metadata`, because that is what the source
    list is built from. An ability that is only for orientation returns
    `results=[]`, and then only the `payload` reaches the model.

    **Import `rag_tool` inside the function, never at the top of the file.**
    Loading the settings happens at import time, so importing it at the top makes
    two modules wait for each other and the app fails to start.

## Tools that live outside the template

A tool that only makes sense for one deployment does not belong in this
repository. Keep it in your own repo and mount it in: everything found in
`extra_tools/`, the empty folder next to `tools/`, is imported the same way as
`tools/`. In Docker the app lives at `/app`, so the mount target is
`/app/extra_tools/`.

A Compose override file next to your tool does the wiring. Because the mount also
holds the settings file, `RAG_CONFIG` can point straight into it. Host paths in an
override resolve against `apps/chainlit/`, not against the override file, so write
them from there:

```yaml
# my-extension/docker-compose.override.yml  (my-extension/ sits next to the template)
services:
  chainlit:
    volumes:
      - ../../../my-extension/tools/:/app/extra_tools/
    environment:
      RAG_CONFIG: extra_tools/my-rag.yaml
```

```bash
cd apps/chainlit
docker compose -f docker-compose.yml -f ../../../my-extension/docker-compose.override.yml up -d
```

Then list the tool in `tools.enabled` as usual. Three things to know:

- **Works without Docker too.** The folder is scanned relative to the app, so
  for a local `chainlit run` you copy the file into `apps/chainlit/extra_tools/`
  instead of mounting it. Git ignores everything in that folder.
- **File names are module names.** `search.py` or `json.py` would shadow an
  existing module, so give the file a distinctive name.
- **A broken file is skipped, not fatal.** An import error is logged as a
  warning and startup continues. The tool then simply is not registered, and the
  name in `tools.enabled` fails validation with the list of known ids, which is
  where to look first when a mounted tool does not appear.

## Invalid ids fail fast

A name in `tools.enabled` that does not exist is rejected while the settings are
being read, and the error message lists the valid names. So a typo shows up
immediately at startup, instead of quietly leaving the assistant without an
ability you thought it had.
