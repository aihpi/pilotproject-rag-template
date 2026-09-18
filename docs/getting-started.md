# Getting Started

From nothing to a working assistant that answers questions about your own
documents. Copy each block into a terminal in the order shown.

## What you need

- **[Docker](https://docs.docker.com/get-docker/)**. On Windows it runs through
  WSL2, which Docker Desktop sets up for you.
- **Git**, to download the project. Or the *Download ZIP* button on GitHub.
- **Access to an AI service**: an address and an access key.
- **A stable internet connection.** Reading documents in makes hundreds of calls to
  that service, so a connection that drops now and then will fail often. A VPN or a
  busy shared network is the usual cause.

You do not need Python, uv, pip, Qdrant or PostgreSQL on your machine. They are
all inside the container.

!!! note "No model is included"
    The project brings the chat app, the database and the search index, but no AI
    model. It always talks to a service somewhere else, which is what the address
    and key in step 1 are for.

    Running a model server on your own machine works too, but inside a container
    `localhost` means the container itself, not your machine. Use
    `http://host.docker.internal:11434/v1` instead of
    `http://localhost:11434/v1` (11434 is the usual port for a local model
    server). You will also have to put the model names your server offers into the
    settings file, since the preset names come from a hosted service.

## 1. Get it running

```bash
git clone https://github.com/aihpi/pilotproject-rag-template.git
cd pilotproject-rag-template/apps/chainlit
cp .env.example .env
```

Open the new `.env` file and fill in the address and key of your AI service.
Nothing works until those two are set.

Check them before starting anything else:

```bash
make check
```

It tries each model a few times and tells you whether a problem is your settings,
your connection, or the service itself, with the steps to take for each.

This first run builds the container, so it takes a few minutes; later runs take
seconds. The build has to happen anyway, and doing it here means a wrong address
or key turns up now, instead of part-way through reading your documents in. When
the check comes back green, start everything:

```bash
docker compose up -d --build
```

The first start takes a few minutes: it downloads the parts, reads the three
example papers, and starts the chat window. Watch it work with
`docker compose logs -f chainlit`.

Then open <http://localhost:8000> and log in with `admin` / `admin`. Ask a
question about the example papers and click a source under the answer. If the PDF
opens, everything works.

## 2. Use your own documents

Put your PDFs into `data/documents/`, then make your own settings file so the
example stays intact:

```bash
cp examples/papers/rag.config.yaml my-rag.yaml
```

Open `my-rag.yaml` and change three things:

```yaml
models:
  chat_model: gpt-oss-120b        # a model your AI service offers
  embed_model: octen-embedding-8b # a search model your AI service offers

vector_store:
  collection: my_docs             # any new name you invent
```

- The two **models** must be names your service actually offers. If you are not
  sure, ask it for its list.
- The **collection** is a name you make up. It keeps your documents separate from
  the examples. Always pick a new one, otherwise the two get mixed together.

Finally, tell the app to use your file instead of the example, by setting
`RAG_CONFIG=my-rag.yaml` in `.env`.

Every available setting is listed in the
[Configuration Reference](configuration.md).

## 3. Read your documents in

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
```

This reads every document and stores it so it can be searched. Depending on how
many you have, it takes anywhere from a minute to much longer.

You only need this command for the *first* load, or after changing settings that
affect every document. Adding, editing or deleting a document later needs nothing:
the app watches the folder and picks changes up within seconds. See
[Changing your documents](managing-documents.md).

!!! warning "Describing pictures costs money"
    With `images.mode: describe` (the default in the example), every picture is
    sent to an AI model once to be described. On a large collection that adds up.
    Set `images.mode: none` if you do not need pictures to be searchable.

    If a picture cannot be described, it is left out rather than stored without a
    description. The run reports how many were affected per document, so watch for
    that line. See [Figures & images](images.md).

## 4. Restart and try it

```bash
docker compose up -d
```

Use `up -d`, not `restart`. A restart reuses the old settings, so your change to
`RAG_CONFIG` in step 2 would be ignored and the assistant would keep answering
from the example papers.

Open <http://localhost:8000> again and ask something only your documents can
answer. If you get a sensible answer with a working source link, you are done.

If the assistant says it cannot find anything, the most likely cause is that step
3 read in nothing. Check that your PDFs really are in `data/documents/` and that
`RAG_CONFIG` points at your file.

## 5. See how good the answers are (optional)

The assistant is working, but how do you know it is answering *well*? Evaluation
scores every answer on two things and shows the result in a small badge above the
chatbox:

- **Faithfulness** checks whether the assistant only said things its sources actually
  support, or did it add claims of its own? The score is the share of claims that
  are backed up: 1.0 means every claim checks out, 0.5 means half of them do not.
- **Relevance** checks whether the answer addresses the question that was asked, rather than
  being correct but beside the point?

Both are checked by a second AI model (the *judge*) that reads the answer and the
sources and decides. No hand-written "correct answers" are needed, so it works on
the questions people are already asking.

The bundled `examples/papers` instance ships with evaluation on, so you should see
the badge on your first question. It costs a judge call per answer, so the schema
default is off for your own instances. The service runs either way; one flag in your
settings file decides whether anything is scored:

```yaml
evaluation:
  enabled: true
```

Restart the app (`docker compose restart chainlit`), ask a question, and wait
about fifteen seconds. A badge appears above the chatbox with the scores. Click
it for the full explanation: which claims passed, which did not, and why.

The full guide is at [Checking answer quality](evaluation.md).

## Without Docker

Only needed if you cannot use Docker, or if you are developing on the app itself.
You need Python 3.12 or newer and a running Qdrant.

```bash
cd apps/chainlit
uv sync                                   # use uv, not pip
docker run -p 6333:6333 qdrant/qdrant     # storage for the searchable text

export RAG_CONFIG=my-rag.yaml
uv run python -m kb.ingest --recreate     # read the documents in
uv run chainlit run app.py                # http://localhost:8000
```

The `export` line has to be repeated in every new terminal window.

## Docs locally

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
