# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

Turned a single-purpose domain assistant into a reusable, config-driven RAG
template: one declarative YAML describes an entire instance, so the same codebase
can be pointed at a new corpus without touching Python.

### Added

- **Tools register themselves.** Every `.py` in `tools/` is imported at startup,
  so a new tool needs no entry in `tools/__init__.py`. A second directory,
  `extra_tools/` next to it, is scanned the same way, so a deployment-specific tool
  and its settings file can live in another repository and be bind-mounted in via
  a Compose override, leaving the template untouched. The folder is part of the
  repository (empty) and is scanned relative to the app, so a tool copied into it
  also loads in a local `chainlit run` without Docker. See
  [Agentic tools](https://aihpi.github.io/pilotprojekt-rag-template/tools/).
- **Figure descriptions are kept as readable Markdown next to your documents, so
  re-reading them does not pay for the vision calls again.** With
  `images.mode: describe` every picture costs a call, and three ordinary things
  used to repeat that cost for the whole corpus: `--recreate`, a chunking change
  (the ingest manifest records only path-to-hash, so a settings change is
  invisible to it), and an import that died partway. Each description is now
  `data/documents/descriptions/<document>/fig<n>.md`, a per-document folder
  beside `figures/`, with a short header fingerprinting the picture, prompt and
  model it came from. A stored description is reused only while that fingerprint
  still matches, so editing `describe_prompt`, `vision_model` or
  `describe_image_max_px` asks for fresh ones. You can correct a description by
  hand and it survives, though it reaches the assistant only on the next
  `--recreate`. Empty descriptions are never stored, so a failed call is
  retried on the next run rather than leaving the figure permanently
  undescribed.

  Removing a document deliberately leaves its descriptions and figures in place,
  so you can drop a document to compare answers, add it back, or point a second
  collection at the same corpus without paying again. The flip side: those files
  stay on disk and the pictures stay reachable to logged-in users until you
  delete them. [Changing your documents](https://aihpi.github.io/pilotprojekt-rag-template/managing-documents/)
  spells out how to remove a document completely.

  Supersedes the short-lived global cache under
  `$XDG_CACHE_HOME/rag-template/figure-descriptions`, which was keyed only by
  image content and so was shared across corpora, outlived the document it
  described, and lived where nobody would look for their own data. That
  directory is now unused and safe to delete; its descriptions are written again
  on the next rebuild. `make check`'s vision probe still calls the model
  directly, since its whole job is to measure the connection repeatedly.
- **YAML-driven configuration.** One file per instance (selected by `RAG_CONFIG`)
  describes data sources, chunking, models, vector store, retrieval, citations,
  prompt, tools, figures and profiles. Typed and validated by pydantic models in
  `apps/chainlit/config/schema.py`; environment variables still override single
  values, and relative paths resolve against the config file's own directory.
- **Parser and chunker registries.** Built-in `pdf`, `txt`/`md`, `json` and `csv`
  parsers, a declarative field-mapping DSL for structured records, plus five
  chunking strategies — `fixed_size`, `heading`, `passthrough`, `semantic` and
  `docling_hybrid` — selectable globally or per data source. Custom parsers and
  chunkers register by name.
- **Agentic tools.** Five pluggable tools the model may call, enabled per instance
  via `tools.enabled`: `search`, `list_documents`, `fetch_document`,
  `expand_context` and `verify_claim`.
- **Figure handling.** `images.mode` chooses between dropping PDF figures
  (`none`), indexing model-written figure descriptions as searchable and citable
  chunks (`describe`), or additionally feeding figure pixels to a vision-capable
  chat model (`attach`). Cited figures render inline in the answer or as
  thumbnails.
- **Automatic system-prompt generation.** When no prompt is configured, one is
  generated from a sample of the indexed chunks and cached next to the config.
- **Chat model selector.** Users can switch the chat model from the UI settings,
  within the models the instance allows.
- **Bundled example corpus.** `examples/papers/` (three open-access papers, all
  features enabled) is the default, so a fresh clone is runnable; also
  `examples/minimal/` as a starting point and `config/default.yaml` as the neutral
  baseline.
- **Documentation site.** MkDocs Material site (English and German) with a
  configuration reference generated from the schema.
- **`make check`, a setup test.** Answers the question that costs the most time when
  something does not work: is it my settings, my connection, or the AI service? It
  reads your settings, resolves and connects to the service host, then tries the chat,
  search and image models several times each. Every failure is reported as **numbered
  steps**, not a stack trace: which file to open, which line to look at, what to check,
  and to run it again afterwards.

  It also refuses to spend anything before checking the obvious. The placeholder values
  that `.env.example` ships with (`your-key`, `http://localhost:4000`) are recognised
  and named as the problem, because copying that file and never editing it is the
  commonest cause of "nothing works" and produces an error that says nothing about it.
  The key is shown masked, so a wrong or truncated key is visible without exposing it.

  Trying each model repeatedly is the point. The failure that misleads people is not
  "it broke" but "it broke sometimes": a connection losing one request in three lets a
  chat message through while making a document import fail dozens of times. A single
  attempt cannot tell those apart, so the report says `3 of 5 attempts worked`.

  The host is probed separately, with a plain name lookup and connection attempt,
  because litellm reports a misspelled address and a mid-request disconnect with the
  same "Connection error" message. Without that split, a typo in `LITELLM_BASE_URL`
  gets diagnosed as a VPN problem and sends people looking in the wrong place.

- **Hybrid retrieval: a lexical search alongside the semantic one, off by
  default.** Embeddings are good at meaning and bad at exact strings — asked for
  `BSI-Standard 200-2` a dense search returns `200-1`, because to the model those
  two sentences mean nearly the same thing. Every chunk now also carries a
  term-frequency vector, and with `retrieval.hybrid: true` Qdrant runs both
  searches and fuses the rankings (`fusion: rrf` or `dbsf`, `prefetch_limit`
  candidates per leg) before anything reaches the assistant. Measured over 30
  identifiers each occurring in exactly one paper of nine: on natural questions
  76% → 93% top-1, on the bare term 50% → 90%.

  **No model, no GPU, no new dependency.** The lexical vector is a word count;
  Qdrant applies the IDF weighting server-side, so there is no corpus statistic to
  compute, store or keep in sync. Hyphenated compounds stay whole, so
  `BSI-Standard` is one term rather than two common words — that detail is most of
  the win, and it lives in `apps/chainlit/kb/sparse.py` if your corpus needs
  different tokenizing. Function words are dropped from the query — not from stored
  chunks, so no re-ingest. IDF does not make that unnecessary: Qdrant applies BM25's
  IDF term without its TF saturation or length normalization, so a term contributes
  `tf × idf`, unbounded in `tf`. On the example corpus, "Was ist X und wofür wurde es
  verwendet?" was won by a chunk not containing X at all — twelve occurrences of `und`
  scored 20.09 against 5.47 for the rare compound identifying the right document.
  Without the filtering the feature measured 36% against dense's 76%, i.e. worse than
  not having it.

  Ingest writes the vector into every collection it creates, so for those `hybrid`
  is a pure query-time switch — flip it, restart, compare — and one collection can
  serve a dense-vs-hybrid A/B.

  A collection created before this existed is dense-only and cannot gain the vector
  retroactively, so **ingest rebuilds it on the next run** — no flag to remember. It
  prints the reason and the number of points it discards, because a rebuild
  re-embeds the corpus and that is billed gateway traffic, and it fires only on a
  real defect: with `hybrid: false` a dense-only collection is perfectly usable and
  is left alone. A tokenizer change is treated the same way and regardless of the
  switch, since writing new-format terms into an old-format index would corrupt it;
  the format version is recorded per collection and compared on every run exactly as
  `embed_model` is. The app and `make check` cannot ingest, so they **refuse to
  start** and name the one command that fixes it, rather than running dense-only
  behind a config that claims otherwise.
  [Hybrid retrieval](https://aihpi.github.io/pilotprojekt-rag-template/retrieval/)
  covers the settings and when a reranker becomes worth its cost.

  `retrieval.score_threshold` bounds only the semantic leg — a lexical match has
  no comparable similarity score — so with `hybrid: true` a chunk can reach the
  assistant on one shared term. If that threshold is what keeps off-topic
  questions unanswered, re-check it after switching on. For the same reason
  `verify_claim` deliberately stays semantic-only: it is the one place a score is
  compared against a fixed bar, and a fused score is a rank, not a similarity.
- **The active configuration is visible in the header, and copyable.** A chip names
  the instance, and opens a panel listing the models, collection, chunking
  strategy, retrieval mode and figure handling actually in effect — resolved
  values, after environment overrides, not what the YAML file says. One icon
  copies it as YAML, which is what you paste into an issue when an answer looks
  wrong.
- **`models.fallback_chat_model`.** When the primary chat model is unreachable or
  errors, the request is retried on this one, so a gateway hiccup on one model does
  not take the instance down.

### Fixed

- **The search index had no healthcheck, so dependents could only wait for the
  container to exist.** `depends_on: service_started` is satisfied the moment
  Compose creates the container, not when Qdrant answers, so `make check` raced
  it: the AI-service checks that run first happened to take longer than Qdrant's
  ~0.8 s startup, and that accident was the only thing keeping the check green.
  On a loaded machine the order inverts and the failure looks exactly like the
  one above, which makes it unpleasant to diagnose twice. Qdrant now has a
  healthcheck and both dependents wait for `service_healthy`. Written as `CMD`
  with an explicit `bash`, not `CMD-SHELL`: that form runs `/bin/sh`, which is
  dash in this image, and the check uses bash's `/dev/tcp` because the image
  ships no `curl`, `wget` or `nc`.

- **`make check` reported the search index as unreachable on every clean
  machine.** The target ran with `--no-deps`, which tells Compose to ignore the
  `depends_on` block, and that block is the only thing that starts Qdrant. So
  the check resolved `qdrant:6333` on a network where no such container existed,
  failed with `Name or service not known`, skipped the hybrid-search check as a
  consequence, and then advised running `docker compose up -d`, which is what
  the reader had been told to do after `make check`. The flag is gone; Compose
  starts the search index, and only that, since `ingest` does not depend on
  Postgres. `make check` now leaves `rag-qdrant` running, which is what the
  following `make up` wants anyway.

- **Retrieved chunks were cut at 1200 characters, so a third of the corpus was
  searchable but never deliverable.** A term at offset 2312 of a 3434-character
  chunk ranked that chunk first and the assistant still answered that the term did
  not appear in the documents — correct, from what it was given. The model's own
  escalation could not recover it either: `expand_context` widens to more chunks,
  each also cut at 1200, and so never returns the text it already had. 31% of
  chunks exceeded the cap; 37% of the corpus was affected. Chunks now arrive whole,
  as the chunker sized them.

  The cap was also the only bound on the payload, so `tools.max_context_chars`
  replaces it — default 120000, derived from the model's context window rather than
  from one corpus, and above the largest measured real payload. It bounds the text
  the assistant actually receives, provenance lines and numbering included, and
  drops **whole chunks from the tail**, never mid-text, saying so in the context
  when it does. A single chunk larger than the entire budget is delivered whole and
  over budget with a loud log, because splitting it is the bug being fixed and
  returning nothing reads as "not found". Context and citations are rendered by one
  function returning both, so a dropped chunk cannot leave the assistant citing a
  source it never received.

  `expand_context` is bounded too, by `tools.fetch_max_chunks` — it can never return
  more than `fetch_document` would. The window it keeps is **centred on the section
  that was asked about**, so clamping a large window cannot hand back the start of
  the document instead of the passage in question.

  **Lower `max_context_chars` if your gateway serves a smaller window than 128k.**
  Nothing can detect this — the gateway advertises no context length for any model
  — so it is the one setting a 32k deployment has to change by hand.
- **Sources were numbered by display order, so citations pointed at the wrong
  document.** The assistant cites the retrieval index it was given, while the panel
  renumbered from one as it rendered — with hybrid retrieval reordering results,
  `Quelle 3` in the text and `Quelle 3` in the sidebar were routinely different
  papers. Both now use the retrieval index. A source whose file cannot be resolved
  is logged rather than silently rendered as plain text, which is what made the
  original mismatch invisible.
- **`sources.served_extensions` did nothing.** It shipped set in three configs, was
  documented in the README and both adding-data pages with a `[.pdf, .txt, .md]`
  default, and had a test asserting that default, but the route hardcoded `.pdf`. So
  adding `.md` and expecting a Markdown source to open gave a silent 404, and the
  README's troubleshooting entry sent people to check a setting that could not be the
  cause. Both gates now read it, and the response carries the real media type via
  `mimetypes.guess_type` instead of claiming everything is a PDF. The path checks that
  matter are unchanged: no separators, membership in the directory listing, and
  containment under `sources.data_dir` after resolving.

- **A failed import now says what to do, and stops being buried in noise.** litellm
  prints a five-line "Give Feedback / Get Help" block for every failed call, so a
  reported failure log was roughly 90% that text with the useful lines lost inside it.
  That output is now suppressed. When a run does fail, it ends with the error, what it
  means, numbered steps, and a pointer to `make check`, instead of a forty-line
  traceback through httpx, openai and litellm whose last line is "Connection error."
  The advice comes from the same place `make check` uses, so the two cannot disagree.

- **Embeddings are retried, so one dropped connection no longer destroys a whole run.**
  Figure descriptions already retried and their failures were caught; a failed embedding
  aborted everything, throwing away work already paid for, including figures. Reported
  from the field on an unstable network.

- **A stable internet connection is now stated as a requirement.** Reading documents
  makes hundreds of calls to the AI service, so a connection that drops occasionally
  fails often, while a single chat message works and hides the cause. Both READMEs and
  the getting-started pages say so, and `docs/troubleshooting.md` opens with how to
  tell an unstable connection apart from wrong settings.

- **The chat history could be corrupted, and was.** It is SQLite in WAL mode and lived
  under `.chainlit/`, which Docker bind-mounts from the host. WAL needs a shared-memory
  companion file and real POSIX locking, and Docker Desktop only emulates both across
  the macOS/Windows filesystem boundary, so a write interrupted at the wrong moment left
  `sqlite3.DatabaseError: database disk image is malformed` on every chat start. Hit
  during development after a series of container restarts; the file was recoverable with
  `sqlite3 old ".recover"`.

  Under Docker the database now lives on a named `chat_db` volume, which is a real Linux
  filesystem inside the VM where WAL behaves correctly. An existing history is carried
  over once on startup via SQLite's backup API, not a file copy: in WAL mode a database
  is several files and recently committed rows can still sit in the `-wal`, so copying
  only the main file loses them. A damaged legacy file is deliberately not carried over
  and is left in place, with the recovery command printed.

  Running without Docker is unaffected and keeps the old path: a native filesystem
  handles WAL fine. Linux hosts were never affected either, since a bind mount there is
  the same kernel filesystem.

- **Self-registration never worked.** `POST /auth/register` was broken three
  independent ways, each sufficient on its own.

  `app.py` uses `from __future__ import annotations`, so FastAPI receives the
  handler's annotation as the string `"RegisterRequest"` and resolves it against
  module globals. The model was declared inside the `on_app_startup` hook, making
  the name a local. FastAPI did not raise: it downgraded the parameter to a
  **query** parameter. Every JSON body was ignored (422 with
  `loc: ["query", "request"]`), a query parameter reached the handler as a `str`
  and raised (500), and `/openapi.json` returned 500 because the schema could not
  be built. The model is now defined at module level.

  `/openapi.json` still failed afterwards for an unrelated reason:
  `chainlit.auth.cookie.OAuth2PasswordBearerWithCookie` subclasses FastAPI's
  `SecurityBase` but never sets `self.model`, which the schema generator reads.
  The missing metadata is now supplied, guarded so it goes inert once Chainlit
  fixes it upstream.

  `create_user` carried two `ON CONFLICT` clauses, one per unique constraint.
  PostgreSQL allows a single clause per statement, so registration failed with a
  syntax error even once the body parsed. One untargeted `ON CONFLICT DO NOTHING`
  covers both constraints.

- **Documents added to the folder were never indexed, and nothing said so.** The
  `ingest` compose service exited successfully as soon as the target collection
  existed, so dropping a PDF into `data/documents/` and restarting did nothing at
  all. See *Changed* below for the new behaviour.

- **Docling's PDF models were re-downloaded on every ingest run.** Roughly 500 MB,
  each time. They cache under `/root/.cache`, and the ingest container mounted only
  the project directory and ran with `--rm`. A named `model_cache` volume now keeps
  them. The README claimed this happened "once per update", which was wrong twice
  over.

- **`pdf_options.ocr: true` failed late and unhelpfully.** The shipped image
  installs no apt packages by design, so it has no OCR engine. The run now stops in
  the first second, naming the package to install and pointing out that PDFs with a
  text layer need no OCR at all.

- **Oversized figures lost their description silently.** The ingest step sent
  full-resolution PNG to the vision model, so a large figure exceeded the gateway's
  body-size limit and came back HTTP 413. The exception was swallowed and the
  figure stored without a description. In the shipped `papers` example this hit
  1 of 79 figures (`Alam_2026` fig1, 1.11 MB encoded).

  The describe call now downscales and re-encodes as JPEG the way the answer-time
  path already did (that figure: 1.11 MB → 215 KB, new
  `images.describe_image_max_px`, default 1536).

- **A failed figure description is no longer retried zero times.** The vision call
  passes `num_retries`, so a passing rate limit no longer costs a figure its
  description permanently.

- **A figure with no description and no caption is no longer stored.** The guard
  meant to drop such chunks tested the assembled text, which always begins
  "Abbildung N (Seite X)" and is therefore never empty, so the guard could never
  fire. A figure now needs either a description or a real caption to be indexed,
  and each document reports how many descriptions failed.

  **Re-ingest only if you need it.** Existing collections are fine unless a figure
  actually failed. If your ingest log showed figure-description errors, or your
  documents contain unusually large figures, read them in once more with
  `--recreate` (see [Figures & images](docs/images.md)); this re-describes every
  figure and is charged accordingly.

### Changed

- **Docling 2.72 to 2.118.** The locked Docling turned ligatures into raw glyph
  names in the extracted text: `/uniFB02 uorescence` for "fluorescence",
  `/uniFB01 nally` for "finally", `GLYPH<14>` for a degree sign. Any PDF set by
  a typesetting journal is affected; in one corpus of 84 papers it was 22 papers
  and about 6400 such tokens, and a search for "flow cytometry" cannot find those
  passages. The PDF backend fixed this in docling-parse 5.4.2 (docling issue
  3056); the lock had 4.7.3. Pinned to 2.118.0 with docling-core 2.90.0 and
  docling-parse 7.8.1, the set a corpus of 84 papers has been validated on, rather
  than the newest release; a later bump is a separate decision. The image sets
  `TORCH_COMPILE_DISABLE=1` because this Docling asks `torch.compile` for a kernel
  the slim image cannot build (no C++ compiler); eager mode converts a paper in
  about 20 s on CPU. `pyproject.toml` now requires at least 2.118.0, so a lock
  refresh cannot fall back to a broken version. Apple's `ocrmac` OCR engine is an
  optional extra of Docling from this version on and no longer installed by
  default; a local macOS run that sets `ocr_engine: mac` adds `docling[ocrmac]`.
  Documents converted by the old version stay readable, the parsers read the
  JSON as dictionaries. They still carry the old text, though. **Re-read your
  documents to get the fix:** `docker compose run --rm ingest python -m kb.ingest
  --recreate`. A plain run will not do it: ingest skips files whose checksum is
  unchanged, and upgrading Docling does not change a PDF. Instances that set
  `pdf_options.docling_json_dir` need their JSON exported again instead, since
  re-ingesting only re-reads the same converted files.

- **The document folders are watched, so changes need no command.** The app is told by
  the operating system when a source folder changes, and indexes whatever was added,
  edited or deleted, without a restart. Measured at 0.3 s from dropping a file in to
  the run starting. Putting a file into the folder is the whole workflow.

  Two stages, so watching is genuinely free: a poll compares size and modification
  time and reads no file contents, and only a hint of change starts the real run that
  hashes and decides. Files modified in the last few seconds are left alone, so a
  large file still being copied is never read half-written, and one pass at a time is
  enforced. The work runs in a thread, so parsing a PDF cannot stall open chats.

  Changes arrive as filesystem events (via `watchfiles`, already present through
  Chainlit), so a document is noticed in about 0.3 s rather than up to 20. A slow
  timeout tick remains, and is required rather than defensive: the settle rule holds
  back a file written a moment ago, and no further event follows it. The events are
  otherwise ignored, because the authoritative comparison runs anyway, so a missed or
  duplicated event costs one extra 2 ms sweep.

  On by default and **opt-out** via `DOCUMENT_WATCH=false`, so a `.env` copied before
  this existed gets the feature without being touched. `DOCUMENT_WATCH_INTERVAL` and
  `DOCUMENT_WATCH_SETTLE` tune it. This only became practical because of the change
  below; before it, any repeated run re-embedded everything.

- **Ingestion is incremental per file.** A plain `python -m kb.ingest` used to be
  all-or-nothing; it now reads only what changed. Every file is recorded with a
  sha256 of its contents, so a new file is ingested, an edited file is ingested
  again, and an unchanged file is skipped for free. Adding a document is therefore
  just putting it in the folder and starting the app.

  The record lives in a metadata point in the collection, keyed by file path rather
  than by the payload's `source_file`, because that key is parser-defined and
  inconsistent. Filtering happens in `kb/parsers/base.py:iter_source_files`, which
  every built-in parser already used, so the `ParserFn` extension point keeps its
  signature and custom parsers keep working.

  **Existing collections are adopted, not rebuilt.** A collection created before
  this has no record of its files. Treating it as empty would re-embed everything
  and, with `images.mode: describe`, re-describe every figure, so the first run
  instead records the current files without ingesting anything and cross-checks them
  against the collection, warning about any file that has no entries. Nothing to do
  and nothing charged.

  **A document deleted from the folder loses its entries.** Otherwise the assistant
  kept answering from, and citing, a file that no longer existed, with a source link
  that could only 404. Deletions are handled in the same run as additions, so
  replacing an entire corpus at once both removes the old entries and indexes the
  new documents.

  Two safeguards, because this makes ingestion destructive. A run limited by
  `--only` never prunes, since the sources it did not visit are not deletions. And
  if *no* file at all is found while the collection knows about some, nothing is
  removed and a warning explains why: an empty documents folder is almost always a
  mount that did not come up or a wrong `path`, not an intentional wipe. Use
  `--recreate` to empty a collection on purpose.

  Matching entries to a file relies on the `source_file` payload, so it works for
  PDF, Markdown and text sources. Entries are kept and reported, rather than
  silently left behind, in the two cases where they cannot be matched safely: a
  `json`/`csv` source whose `field_mapping` writes no `source_file`, and a name that
  another file on disk still uses.

- **Duplicate file names are now reported.** `doc_id`, and therefore each Qdrant
  point id, is derived from the file name alone, so two documents called `intro.pdf`
  in different folders of one collection collide and the second silently overwrites
  the first. That is not new and is not fixed here, because changing the derivation
  would invalidate every existing point id and force a full re-ingest of every
  instance. Each run now names the clashing files and says one of them is being lost.

  `--recreate` is unchanged and still the right tool after altering `chunking`,
  chunk sizes or `images.mode`. `--skip-if-exists` still works but is no longer
  useful, since it is what suppressed added, edited and deleted files alike.

- **Open-weight models everywhere by default.** All shipped configs, the schema
  defaults, `docker-compose.yml` and the docs now use `gpt-oss-120b` (Apache-2.0),
  `octen-embedding-8b` and `gemma-4-31b` instead of proprietary hosted models, so
  no instance depends on a single vendor. Model names remain gateway-specific;
  the configs point at `GET /v1/models` and list other open alternatives.

### Removed

The following were dropped as domain- or workflow-specific. They remain available
in the `backup/pre-template-cleanup` branch.

- The IT-Grundschutz example instance, its custom parser and its documentation
  pages.
- The `data/` directory of source documents at the repo root.
- The `scripts/` and `notebooks/` directories.
- Orphaned helper scripts, including `export_docling_md.py` (use Docling's own
  CLI: `docling --to json <pdf>`) and `export_chats.py` (use the
  `/export/all-chats` route or the sidebar export button).
