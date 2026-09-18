# Contributing

Thanks for helping improve this RAG template. It is a prototype meant to be
forked and pointed at new corpora, so the bar for a change is simple: does it
stay config-driven and does it keep working for someone else's data?

## Setup

The repo root is a docs-only project; the application lives in `apps/chainlit/`.

```bash
git clone https://github.com/aihpi/pilotproject-rag-template.git
cd pilotproject-rag-template
uv sync                      # root project (documentation toolchain)

cd apps/chainlit
uv sync                      # the app itself
cp .env.example .env         # set LITELLM_BASE_URL + LITELLM_API_KEY
```

See [docs/getting-started.md](docs/getting-started.md) for ingesting and running
the app, and `apps/chainlit/README.md` for the day-to-day operations commands.

## Tests

```bash
cd apps/chainlit
RAG_CONFIG=config/default.yaml uv run pytest tests/ -q
```

The tests pin the neutral baseline config on purpose, so they do not depend on
whichever instance your `.env` selects. Run them before opening a PR, and add
cases for new parsers, chunkers, tools or config fields.

## Documentation

Docs are built with MkDocs from the repo root (English pages plus `.de.md`
translations, wired up in `mkdocs.yml`):

```bash
uv run --only-group docs mkdocs serve
```

The configuration reference is generated from the pydantic models in
`apps/chainlit/config/schema.py` via mkdocstrings — document new config fields in
their docstring, not by hand. If you add a page, add both the English and German
file and a `nav` entry.

## Commits

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(config): add semantic chunking strategy
fix: normalize Unicode hyphens in keyword dedup
docs: document the figure marker protocol
chore: untrack local instance state
refactor!: drop IT-Grundschutz legacy and orphaned code
```

Use `!` (or a `BREAKING CHANGE:` footer) when a config or data layout has to be
migrated. Keep the subject imperative and under ~72 characters.

## Pull requests

1. Branch off `main` (`feature/<issue>-<slug>` or `fix/<slug>`).
2. Keep the change focused, and update the docs and `CHANGELOG.md` in the same PR.
3. Run the tests, and start the app once if you touched ingestion or the UI.
4. Fill in `.github/PULL_REQUEST_TEMPLATE.md` — link the issue and say *why*.
5. Squash-merge; the squashed subject becomes the history entry.

## Do not commit

- **Secrets.** `.env` is gitignored — keep keys, gateway URLs and passwords
  there. Never put them in a YAML config, a test fixture or a doc example.
- **Your own instance.** `my-rag.yaml`, your documents, generated prompts,
  figure stores and Qdrant/Chainlit state are local by design and gitignored.
  Contribute a small, redistributable sample under `examples/` instead if a
  feature needs data to demonstrate it.
