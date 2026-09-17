<p align="center">
  <img src="00_aisc/img/logo_aisc_bmftr.jpg" alt="AISC / BMFTR">
</p>

# Modular RAG Template

**🇩🇪 [Deutsche Version](README.md)** · 📖 **[Documentation](https://aihpi.github.io/pilotproject-rag-template/)**

A chat assistant that answers questions about your own documents, and shows you
the exact page each answer came from.

You give it a folder of PDFs. It reads them, and from then on you can ask
questions in normal language. Every answer links back to the source, so you can
check it yourself.

Setting it up means editing one settings file. You do not need to write any code.

> Three research papers come with the project
> ([sources & licence](apps/chainlit/data/documents/SOURCES.md)), so you can try a
> working assistant before you change anything.

<details>
<summary><b>Which tools this is built on</b></summary>

[Chainlit](https://chainlit.io) for the chat window, [LiteLLM](https://litellm.ai)
to talk to the AI models, [Qdrant](https://qdrant.tech) to store the searchable
text, and [Docling](https://github.com/DS4SD/docling) to read PDFs.
</details>

---

## What you need

Three things to install, plus a network connection:

| | |
|---|---|
| **[Docker](https://docs.docker.com/get-docker/)** | Runs everything. On Windows it uses WSL2, which Docker Desktop sets up for you. |
| **Git** | To download the project. If you would rather not install it, use the green *Code* button on GitHub and pick *Download ZIP*. |
| **A text editor** | To fill in one settings file. Any editor works. |
| **A stable internet connection** | With access to an AI service. Reading documents makes hundreds of calls, so a connection that drops occasionally will fail often. A VPN or a busy shared network is the usual cause of trouble. |

You do not need Python, uv, pip, Node, Qdrant or PostgreSQL. Those are all inside
the container.

**One thing is not included: the AI model.** The project ships the chat app, the
database and the search index, but no model. You point it at an existing service
by putting an address and a key into the `.env` file. That can be a service your
organisation provides, or a model server running on your own machine.

Whether your service is reachable and your key is right is what `make check` in
the [Quickstart](#quickstart) confirms, before anything is read in.

## Quickstart

Copy these lines into a terminal, one block at a time:

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit

cp .env.example .env            # copies the template, now fill in .env
```

Open the new `.env` file and fill in the address and key for your AI service.
Then check them before you start:

```bash
make check                      # reachable? key correct?
docker compose up -d --build    # starts everything
```

The first check builds the container too, so it takes a few minutes; after that it
runs in seconds. The build has to happen anyway, and this way a wrong key turns up
before your documents are read in rather than halfway through. If something is
wrong, the output tells you what to check.

Then open <http://localhost:8000> and log in with `admin` / `admin`. Change that
password before anyone else can reach the app.

The assistant starts out with the three example papers already loaded, and all
features switched on. Its settings are in
[`examples/papers/rag.config.yaml`](apps/chainlit/examples/papers/rag.config.yaml),
which is a good file to look at first.

<details>
<summary><b>Without Docker</b></summary>

```bash
cd apps/chainlit
uv sync                                   # use uv, not pip (see Updating below)
docker run -p 6333:6333 qdrant/qdrant     # storage for the searchable text

export RAG_CONFIG=examples/papers/rag.config.yaml
uv run python -m kb.ingest                # read the documents in
uv run chainlit run app.py                # http://localhost:8000
```
</details>

> Which AI models to use: the example uses freely available models
> (`gpt-oss-120b`, `octen-embedding-8b`, and `gemma-4-31b` for reading images).
> Nothing here ties you to a paid provider.
>
> Model names differ from service to service, so the names above may not work
> with yours. Ask your service which models it offers, then write those names
> into the settings file. The file lists more open alternatives in a comment
> (Qwen3, Llama 3.x, Mistral, BGE-M3, multilingual-E5).

## Updating to a newer version

If it is already running and you want the latest changes, it is three commands.

With Docker, the usual way:

```bash
git pull
cd apps/chainlit
docker compose up -d --build
```

The `--build` at the end matters. Without it, Docker starts your old app again and
you will not see any of the changes, and no error warns you. Rebuilding takes about
half a minute. (`make up` does the same thing.)

Ingest runs as part of this and normally finishes in seconds, since it only touches
files that changed. Two cases cost more, and they deliberately behave differently. If
an update changes the lexical tokenizer, ingest rebuilds the collection by itself: it
names the reason and the number of points affected, then re-embeds the corpus, which on
a large one costs time and billed gateway calls. Switching the embedding model instead
makes it refuse the run and ask for an explicit `--recreate`, because that is your
decision rather than a side effect of updating.

Without Docker:

```bash
git pull
cd apps/chainlit
uv sync
```

Use `uv sync`, not `pip install -e .`. Pip picks different versions of some packages,
and the app can fail to start.

Then open <http://localhost:8000> and ask a question. Click one of the sources under the
answer and the PDF should open on the right. That is the quickest way to see it worked.

### Good to know

- Your documents, indexed data and chat history all survive an update, so you
  normally do not need to ingest again. Files you add, change or delete in the
  documents folder are picked up by themselves, within seconds.
- One exception: if an earlier ingest reported failed figure descriptions, read
  your documents in once more so those figures get described. Only worth doing if
  you saw those errors, see [Figures & images](docs/images.md).
- The very first document import is slow, because the app downloads the models it
  uses to read PDFs (roughly 500 MB). They are kept afterwards, so this happens
  once, not once per update.
- Old versions take up disk space, which `docker image prune` frees again.
- If something is acting up, stopping everything and starting fresh usually helps:
  `docker compose down && docker compose up -d --build`. If the error persists, it
  is probably in [Troubleshooting](docs/troubleshooting.md).

## Use your own documents

First make your own copy of the settings file, so the examples stay intact:

```bash
cp apps/chainlit/examples/papers/rag.config.yaml apps/chainlit/my-rag.yaml
```

1. Put your PDFs into `apps/chainlit/data/documents/`. They stay on your machine.
   Only the example papers belong to the project, and you can delete them.
2. Open `my-rag.yaml` and give `vector_store.collection` a new name. This keeps
   your documents separate from the examples.
3. Let the app read your documents:
   `RAG_CONFIG=my-rag.yaml docker compose up -d`

It also handles Markdown, JSON and CSV files. See
[Adding your data](docs/adding-data.md).

Changing your documents later needs no commands at all, because the app watches
the folder. Add a file and it is read within seconds. Correct one and it is read
again, and delete one and it stops appearing in answers. Everything else is left
untouched, so you never pay twice for the same document, and nothing has to be
restarted. If you would rather trigger it yourself, switch it off with
`DOCUMENT_WATCH=false`.
→ [Changing your documents](docs/managing-documents.md)

## What you configure

Everything lives in one settings file. These are its sections:

| Section | What it controls |
|---|---|
| `models` | which AI models to use, and which ones users can pick in the app |
| `vector_store` | where the searchable text is stored |
| `data_sources[]` | where your documents are and what type they are |
| `chunking` | how documents get cut into pieces before they are searched |
| `retrieval` | how many pieces to look up for each question |
| `tools` | what the assistant is allowed to do besides searching → [more](docs/tools.md) |
| `images` | what happens with pictures and charts in your PDFs → [more](docs/images.md) |
| `citation` | how a source reference looks under an answer |
| `prompt` | the instructions the assistant follows, and the example questions → [more](docs/prompts.md) |
| `evaluation` | score answers automatically to compare configurations (schema default off, on in `examples/papers`) → [more](docs/evaluation.md) |
| `app` | appearance and behaviour of the chat window |

Every single option is listed in [Configuration](docs/configuration.md).

## Features

- The assistant decides how to look things up. Besides plain search it can list
  all documents, read a whole document (needed for summaries), fetch more text
  around a hit, or double-check a claim. You choose which of these it may use.
  → [docs](docs/tools.md)
- Pictures and charts are included rather than skipped. They are described in
  words, so they can be found by a search, and a relevant figure is shown right
  above the paragraph that discusses it. → [docs](docs/images.md)
- Tables stay part of the text instead of being thrown away.
- Every claim has a source. Click it and the original PDF opens at the right page.
- If you do not write the assistant's instructions yourself, it writes them from
  your documents when it starts. → [docs](docs/prompts.md)
- Users can switch models and edit instructions in the settings panel, and their
  choice is remembered.
- Chat history, thumbs up/down and exports, with GitHub or local login.
- **Measure answer quality.** Every answer gets two scores: *faithfulness* (does
  the assistant only say things its sources actually support?) and *relevance*
  (does it answer the question that was asked?). A small badge above the chatbox
  shows the running average. Click it and you see exactly which claims
  passed, which did not, and why. A second tab compares different
  configurations side by side, so "did that change help?" becomes something you
  can look at. → [docs](docs/evaluation.md)

### Turning evaluation on

The bundled `examples/papers` instance has evaluation **on**, so a fresh clone
actually sees the badge. The schema default is off, so your own instance scores
nothing until you opt in. The evaluation service starts with the rest of the stack,
so one flag in your settings file is the whole switch:

```yaml
evaluation:
  enabled: true
```

Restart the app (`docker compose restart chainlit`), ask a question and wait
about fifteen seconds. A badge appears above the chatbox with the scores. Click
it for the full explanation: which claims passed, which did not, and why.

## How it fits together

Your documents are read, cut into pieces and stored so they can be searched. When
someone asks a question, the matching pieces are looked up and handed to the AI
model, which writes an answer with sources.

```
                        rag.config.yaml
                               │
  documents ──► kb/parsers ──► kb/chunkers ──► embeddings ──► Qdrant
  (pdf/md/json/csv/custom)                                      │
                                                                ▼
  Chainlit UI ◄── citations ◄── answer ◄── LLM + tools ◄── retrieval
```

Each step is one small folder you can extend with a single new file: document
types in `kb/parsers/`, ways of cutting text in `kb/chunkers/`, and abilities in
`tools/`. → [Extending](docs/extending.md)

## Documentation

| Page | |
|---|---|
| [Getting started](docs/getting-started.md) | install, ingest, run |
| [Changing your documents](docs/managing-documents.md) | add, correct, remove, rebuild |
| [Troubleshooting](docs/troubleshooting.md) | common errors and what to do |
| [Example corpus](docs/example-corpus.md) | what ships and how to swap it |
| [Adding your data](docs/adding-data.md) | formats, chunking, citations |
| [Agentic tools](docs/tools.md) | the five tools, writing your own |
| [Figures & images](docs/images.md) | `images.mode`, inline placement |
| [System prompts](docs/prompts.md) | generation, editing, model picker |
| [Checking answer quality](docs/evaluation.md) | scoring, badge, comparison |
| [Configuration](docs/configuration.md) | full schema reference |
| [Field-mapping DSL](docs/field-mapping.md) | JSON/CSV → chunks |
| [Extending](docs/extending.md) | custom parsers, chunkers, tools |

Published in English and German at
**<https://aihpi.github.io/pilotprojekt-rag-template/>**, or locally via
`uv run --only-group docs mkdocs serve`.

## Limitations

- This is a prototype and has not been security-checked, so look it over before
  using it with sensitive data or real users.
- Sources and follow-up questions only work in German. The app looks for German
  wording (`Quelle N: … (S.x)`, `Anschlussfragen:`), so set `language: de` for
  those two features. Your documents themselves can be in any language.
- Describing pictures costs money: with `images.mode: describe`, every figure is
  sent to an AI model once while your documents are being read in.
- If you change the model that indexes your text, every document has to be read
  in again (`--recreate`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Earlier project stages (the IT-Grundschutz
assistant, research notebooks and evaluation scripts) remain on the
`backup/pre-template-cleanup` branch.

## References

- [AI Service Centre Berlin Brandenburg (KI-Servicezentrum)](https://hpi.de/ki-servicezentrum/)
- [fghgsd.de](https://fghgsd.de)

## Licence

Code under the [MIT licence](LICENSE). The example papers are CC BY 4.0, see
[SOURCES.md](apps/chainlit/data/documents/SOURCES.md).

---

## Acknowledgement
<img src="00_aisc/img/logo_bmftr_de.png" alt="BMFTR" style="width:170px;"/>

The [AI Service Centre Berlin Brandenburg](http://hpi.de/kisz) is funded by the
[German Federal Ministry of Research, Technology and Space](https://www.bmbf.de/)
under grant number 01IS22092.
