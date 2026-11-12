# Example Corpus

The template comes with a few documents already loaded, so it works the moment
you clone it. You get a working chatbot first and can swap in your own documents
later, once you have seen what it does.

## What is included

Three freely available research articles in `apps/chainlit/data/documents/`, all
published in *Scientific Reports* under the **CC BY 4.0** licence:

| File | Article | Reference |
|---|---|---|
| `Kage_2018_SciReports.pdf` | Luminescence lifetime encoding in time-domain flow cytometry | *Sci Rep* **8**, 16715 (2018), DOI [10.1038/s41598-018-35137-5](https://doi.org/10.1038/s41598-018-35137-5) |
| `Schmidt_2022_SciReports.pdf` | A multiparametric fluorescence assay for screening aptamer–protein interactions based on microbeads | *Sci Rep* **12**, 2961 (2022), DOI [10.1038/s41598-022-06817-0](https://doi.org/10.1038/s41598-022-06817-0) |
| `Lin_2024_SciReports.pdf` | Coupling a recurrent neural network to SPAD TCSPC systems for real-time fluorescence lifetime imaging | *Sci Rep* **14**, 3286 (2024), DOI [10.1038/s41598-024-52966-9](https://doi.org/10.1038/s41598-024-52966-9) |

Full credit (titles, authors, licence links) is in
`apps/chainlit/data/documents/SOURCES.md`. That file is the official one, so it
is not repeated here.

## The matching instance

These papers belong to the settings file `examples/papers/rag.config.yaml`, which
stores them under the name `papers`. It is the showcase setup with everything
switched on:

- the assistant may use all five [abilities](tools.md): search, list documents,
  read a whole document, fetch surrounding text, and double-check a claim;
- documents are split where the topic changes, rather than at a fixed length;
- pictures and charts are described in words so they can be found, and shown
  above the paragraph that mentions them (see [Figures & Images](images.md)).

Both `.env.example` and `docker-compose.yml` already point at this file, so
`make up` starts exactly this setup without any changes.

**Why these papers:** they contain real tables and figures. That makes them a
good demonstration of keeping tables intact, describing figures, and searching
across several documents at once. A corpus of plain text would not show any of
that.

## Using your own corpus

1. **Put your PDFs into `apps/chainlit/data/documents/`.** Your files stay on
   your machine and are never uploaded to the project. You can delete the three
   examples; nothing depends on them.

    ```bash
    cp ~/my-papers/*.pdf apps/chainlit/data/documents/
    ```

2. **Copy the settings file and give it a new collection name.** Changing
   `vector_store.collection` is **required**. Without it your documents get mixed
   into the same pile as the examples. While you are in the file, also rewrite
   `prompt.starter_questions`, because the ones supplied ask about the example
   papers.

    ```bash
    cp examples/papers/rag.config.yaml examples/papers/my-rag.yaml
    ```

    The copy stays next to the example. Paths in a settings file are relative to
    the file, so this way the paths to the documents stay right.

    ```yaml
    vector_store:
      collection: my_papers        # new name, do not reuse `papers`

    prompt:
      starter_questions:
        - "Which documents are in the knowledge base?"
    ```

3. **Read your documents in.**

    ```bash
    RAG_CONFIG=examples/papers/my-rag.yaml python -m kb.ingest --recreate
    ```

    Run this again whenever you add, remove or edit documents.

Then start the app as usual. Other file types and ways of splitting text are
covered in [Adding your data](adding-data.md), and the full first-run walkthrough
is in [Getting Started](getting-started.md).

!!! note "Switching the embedding model"
    Each collection remembers which model made its text searchable. If you change
    `models.embed_model`, read everything in again with `--recreate`, or use a new
    collection name. The app will refuse a collection built with a different
    model, because the two kinds of data cannot be compared.
