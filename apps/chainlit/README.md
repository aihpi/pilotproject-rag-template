# Chainlit RAG App

This folder holds the actual application:

- the settings layer (`config/`): one file per setup, chosen with `RAG_CONFIG`,
- the reading pipeline (`kb/`): read documents, cut them up, store them so they
  can be searched,
- the things the assistant is allowed to do (`tools/`),
- the chat window (`app.py`), and ready-made setups under `examples/`.

Which setup is active is decided by `RAG_CONFIG`. A fresh clone uses
`examples/papers/rag.config.yaml` (a small set of open-access papers with all
features on). `config/default.yaml` is the plain baseline, and `my-rag.yaml` is
the slot reserved for your own setup, which git ignores.

This file is the short operations guide: starting, stopping, and what to do when
something breaks. Everything else (setting things up, adding data, tools,
figures, prompts, the full list of settings) is in the
[project docs](../../docs/index.md).

## Quickstart (Docker Compose)

From `apps/chainlit`:

```bash
cp .env.example .env      # set LITELLM_BASE_URL + LITELLM_API_KEY, pick RAG_CONFIG
make up                   # docker compose up -d --build
make logs                 # follow chainlit logs
```

Then open <http://localhost:8000>.

Four things start together: the chat window on port 8000, the search storage
(`qdrant`) on 6333, the database for chat history (`postgres`) on 5432, and a
one-off job that reads your documents in before the chat window comes up.

Inside Docker these talk to each other by name, not through `localhost`, which is
why Compose sets `QDRANT_URL=http://qdrant:6333` for you.

To check that your AI service is reachable before you read any documents in:

```bash
make check                # tests settings, host and each model five times
```

Day-to-day:

```bash
make down         # stop stack
make reingest     # force recreate + reingest the collection
make ps           # service status
```

## Local (no Docker)

You need Python 3.12 or newer, a running Qdrant, and access to an AI service.

```bash
uv sync                                  # or: make local-install (uv, not pip)
docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant
uv run python -m kb.ingest               # embed + upsert
uv run chainlit run app.py -w            # add --port 8001 if 8000 is taken
```

## Environment

`.env` is only for passwords, addresses and which setup to load. Everything about
a particular setup (models, collection, chunking, prompt, citations, tools,
images) belongs in the settings file instead. `.env.example` lists everything
with comments; the important ones are:

- `RAG_CONFIG`: which settings file to load (counted from `apps/chainlit/`)
- `LITELLM_BASE_URL`, `LITELLM_API_KEY`
- `QDRANT_URL`, `QDRANT_API_KEY`
- `DATABASE_URL`, `CHAINLIT_AUTH_SECRET`, `CHAINLIT_AUTH_USERNAME`/`_PASSWORD`
  (chat history in the sidebar plus login; change the `admin/admin` default)
- `INGEST_RECREATE`, `INGEST_BATCH_SIZE`, `INGEST_MAX_BATCH_CHARS`,
  `INGEST_DOCLING_JSON_DIR`: control the automatic reading-in step
- `DOCUMENT_WATCH`, `DOCUMENT_WATCH_INTERVAL`: the folder watcher, on by default.
  Set `DOCUMENT_WATCH=false` to read documents in only when you ask.

Anything under "optional overrides" in `.env.example` (`CHAT_MODEL`, `TOP_K`, …)
beats the settings file. Handy occasionally, but easy to forget you set it, so
use it sparingly.

## Ingestion

```bash
uv run python -m kb.ingest --dry-run          # parse + chunk only, prints samples
uv run python -m kb.ingest --recreate         # rebuild the collection from scratch
uv run python -m kb.ingest --only docs        # a single data source
uv run python -m kb.ingest --skip-if-exists   # no-op if the collection exists
uv run python -m kb.ingest --config examples/minimal/rag.config.yaml
```

A normal run is incremental. New files get read, edited files get read again,
files you deleted lose their entries, and everything else is skipped without
being touched, so you do not pay twice for the same document. The app also
watches the folder and does this by itself within seconds. See
[Changing your documents](../../docs/managing-documents.md).

> Two things still need `--recreate`: switching the model that makes text
> searchable (refused outright otherwise, because old and new data cannot be
> compared) and changing how documents are cut into chunks. A run limited by
> `--only` never deletes anything.
>
> `--skip-if-exists` is the exception to all of the above. It checks whether the
> collection is there and nothing else, which is what makes it useful as a
> startup guard.

Reading PDFs is slow. Point `pdf_options.docling_json_dir` at a folder and each
PDF is converted once, on its first ingest, and read from there afterwards. See
[Adding your data](../../docs/adding-data.md).

## Chat history & export

There are two separate histories, which is confusing until you know:

- The sidebar history is the real one. It lives in Postgres via `DATABASE_URL`
  and needs login.
- A local file at `.chainlit/chat_history.sqlite3` backs the slash commands, with
  exports written to `.files/chat_exports`. Both paths move via `CHAT_DB_PATH`
  and `CHAT_EXPORT_DIR`.

In the chat window: `/history`, `/history <session_id>`, `/export`,
`/export <session_id>`, `/export all`.

For a bulk download there is an "Export all chats" button in the left sidebar.
Admins can also download all collected thumbs up/down ratings. See
[Feedback export](../../docs/feedback-export.md).

## Tests

```bash
RAG_CONFIG=config/default.yaml uv run pytest tests/ -q
```

## Troubleshooting

Longer version, in plain language, in
[Troubleshooting](../../docs/troubleshooting.md). The short list:

- `Connection error` on every model, or an ingest that dies partway: run
  `make check`. It tells you whether the problem is your settings, your network
  or the service itself, and prints the steps to take for each.
- `Connection refused` to Qdrant: it is not running, or `QDRANT_URL` is wrong.
  Inside Docker it has to be `http://qdrant:6333`, never `localhost`.
- Embedding 400, or a context-window error: too much text in one request. Lower
  `INGEST_MAX_BATCH_CHARS` and/or `INGEST_BATCH_SIZE`.
- Large payload 400 from Qdrant: lower the batch size (256 → 128).
- Port 8000 already in use: something else has it. Run
  `chainlit run app.py -w --port 8001`.
- Citations point at the wrong text: your documents and the stored data are out
  of step. Read everything in again with `--recreate`.
- `401 Unauthorized` or `404 Source not found` under `/sources/...`: log in
  again. If that does not help, the file is not inside the folder named in
  `sources.data_dir`, or its file type is missing from
  `sources.served_extensions`.
- `database disk image is malformed` at chat start: the local chat file broke.
  Under Docker it now lives on a named volume, where this does not happen, and
  Compose pins the path itself, so your `.env` cannot move it back by accident.
  Point `DOCKER_CHAT_DB_PATH` somewhere else only if you know the target handles
  SQLite locking.
