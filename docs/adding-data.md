# Adding your data

Each set of documents is one entry under `data_sources[]` in your settings file.
An entry says **where** the files are, **what type** they are, and optionally how
to cut them up and label them. You can list several sets and search them all
together.

```yaml
data_sources:
  - name: handbook          # unique label (used in --only and fallback ids)
    path: ./data/handbook   # file or directory, RELATIVE TO THE CONFIG FILE
    format: pdf             # pdf | txt | md | json | csv | custom
    glob: "*.pdf"           # for directories
    chunking: {strategy: heading}   # optional per-source override
    extra_metadata: {topic: security}  # optional static metadata on every chunk
```

!!! note "Paths are relative to the config file"
    A `path` is counted **from the folder the settings file is in**, not from
    wherever you happen to be in the terminal. This trips people up. Paths that
    start at the root of the disk are used as written. Inside Docker, use the
    mounted paths (`/data/...`) or the `INGEST_DOCLING_JSON_DIR` setting.

## Several folders at once

Two folders, nothing optional, copy-pasteable. Add one `- name:` block per
folder:

```yaml
data_sources:
  - name: handbook
    path: ./data/handbook
    format: pdf
    glob: "*.pdf"

  - name: notes
    path: ./data/notes
    format: pdf
    glob: "*.pdf"

sources:
  data_dir: ./data/handbook
  served_extensions: [.pdf]
```

Both folders are read, searched together, and clicking a citation opens the file
from whichever folder it came from.

**You do not list your folders under `sources:`.** That block is only about
clicking citations: `served_extensions` says which file types may open, and
`data_dir` is just the folder checked first if the same filename exists twice
(it is also where figure images are written). Point it at your main folder and
leave it.

A third folder is another block:

```yaml
  - name: contracts
    path: ./data/contracts
    format: pdf
    glob: "*.pdf"
```

Folders may hold different file types. One can be PDFs and another Markdown;
just set each block's own `format`.

## 1. Put the files somewhere

Put your documents anywhere on your machine and point `path` at them. For
example, a `data/` folder next to the project:

```
pilotprojekt-rag-template/
  data/
    handbook/*.pdf
    notes/*.md
    faq.csv
  apps/chainlit/
    my-rag.yaml        # path: ../../data/handbook  (relative to this file)
```

## Documents on a Windows share (SMB)

If the documents live on a Windows file server, you do not copy them. Docker
**mounts** the shared folder into the containers, read-only, and the app reads
it in place at `/data/documents`. From the app's point of view that is just a
folder with files in it, exactly like the local `data/documents`: same parsing,
same incremental ingest, same citations, subfolders included. The files stay
on the server, and the app can never change them. Nothing in the code changes;
the source `path` is simply `/data/documents`, the path inside the container,
never a Windows path. This is the checklist for whoever runs the app inside
that network.

1. **Share the folder on the Windows Server.** Right-click the folder, *Share*,
   or in PowerShell:

    ```powershell
    New-SmbShare -Name documents -Path D:\Docs -ReadAccess DOMAIN\rag-reader
    ```

    Use a dedicated read-only account like `rag-reader`, not a personal login,
    and give it a password without commas: Docker splits the mount options on
    commas, so a comma in the password breaks the mount.
    A personal login stops working when its password rotates, and it ends up
    in a config file on the Docker host.

2. **Check the share is reachable from the Docker host** (any machine inside
   the network that runs Docker):

    ```bash
    smbclient -L //fileserver -U rag-reader
    ```

    On a Windows Docker host, `net view \\fileserver` does the same.

3. **Fill in `.env`**:

    ```
    RAG_CONFIG=examples/smb/rag.config.yaml
    SMB_SHARE=//fileserver/documents
    SMB_USER=rag-reader
    SMB_PASSWORD=...
    ```

4. **Dry run.** This mounts the share and lists what the ingest would read,
   without touching the search index:

    ```bash
    docker compose -f docker-compose.yml -f docker-compose.smb.yml run --rm ingest \
      python -m kb.ingest --dry-run --config "$RAG_CONFIG"
    ```

    It must list the files on the share, subfolders included: the example
    config uses `**/*.[pP][dD][fF]`, which walks every folder and takes `.pdf`
    and `.PDF`. Other formats (`md`, `txt`, `csv`, `json`) are further sources
    on the same path, see the commented block in the example. If the share is unreachable or the
    credentials are wrong, the container refuses to start with
    `error while mounting volume ... connection refused` (or `permission
    denied`). Check the share name, the account, and whether the Docker host
    has CIFS support (`apt install cifs-utils` on a Linux VM). Nothing in the
    search index is touched in that case.

5. **Start the app**: `make up-smb`. Then ask the chat a question whose
   answer is in one of the documents and check that the citation opens it.

Four things to know about running from a share:

- If the share is down at start, the container does not start, see above.
  If you use the bind-mount fallback from `docker-compose.smb.yml` instead, a
  missing mount shows up as an empty folder. Ingest then deletes nothing from
  the collection, see the warning in [step 4 below](#4-read-the-documents-in).
  Fix the mount and rerun.
- The share is mounted read-only. Figure images and descriptions are written
  under `sources.data_dir` next to the config, never onto the share.
- Changes on the share are noticed by polling every `DOCUMENT_WATCH_INTERVAL`
  seconds, not instantly. SMB does not deliver file-change events to Linux.
- The credentials are visible to anyone who can run `docker volume inspect`
  on the Docker host. That is one more reason for a read-only account.

## 2. Declare the source (by format)

=== "PDF"

    Point at a folder of PDFs. **Docling** reads them and recognises the
    structure, so it knows the headings, which text belongs to which section, and
    which page it was on. Switch on OCR if your PDFs are scans, meaning the text
    is really a photo and cannot be selected.

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      glob: "*.pdf"
      chunking: {strategy: heading}        # one chunk per section
      pdf_options: {ocr: true, ocr_engine: tesseract, ocr_lang: [eng, deu]}
    ```

    **Read the PDFs once and reuse the result.** Reading PDFs is slow, especially
    with OCR, and you will likely repeat it while getting your settings right. You
    can convert them once and point at that, which skips the slow step from then
    on. The result is identical, it is purely about speed:

    ```bash
    docling --to json --output ../../data/handbook_json ../../data/handbook
    ```
    ```yaml
    - name: handbook
      path: ../../data/handbook_json
      format: pdf
      pdf_options: {docling_json_dir: ../../data/handbook_json}
      chunking: {strategy: passthrough}   # sections are already heading-delimited
    ```

=== "Text / Markdown"

    Each file becomes one section. Works well with the `fixed_size` way of
    splitting.

    ```yaml
    - name: notes
      path: ../../data/notes
      format: md          # or txt
      glob: "*.md"
    ```

=== "CSV"

    One piece per row. You describe which columns to use with a
    [field-mapping](field-mapping.md). Use `passthrough` so each row stays whole.

    ```yaml
    - name: faq
      path: ../../data/faq.csv
      format: csv
      chunking: {strategy: passthrough}
      field_mapping:
        delimiter: ";"
        text_template: "Q: {question}\n\nA: {answer}"
        metadata: {title: question}
    ```

=== "JSON"

    Simple lists as well as deeply nested files. The full walkthrough is in
    [Field-Mapping DSL](field-mapping.md).

    ```yaml
    - name: articles
      path: ../../data/articles.json
      format: json
      field_mapping:
        record_path: items
        text_fields: [title, body]
        metadata: {title: title}
    ```

=== "Custom"

    If your files have an unusual structure that none of the above fits, someone
    can write a small piece of Python for it. See [Extending](extending.md).

    ```yaml
    - name: mine
      path: ../../data/mine
      format: custom
      parser_name: my_format
      chunking: {strategy: passthrough}
    ```

### Which files a source picks up: `glob`

For a source whose `path` is a directory, `glob` decides which files in it belong to
that source. The patterns are Python's `pathlib`:

| Pattern | Matches | Example against the nine bundled papers |
|---|---|---|
| `*` | any run of characters, including none | `*.pdf` takes all nine |
| `?` | exactly one character | `*_202?_*.pdf` takes the six from 2020 on |
| `[seq]` | one character from the set | `Kage_20[12]*` takes Kage_2018 and Kage_2020 |
| `[!seq]` | one character not in the set | `[!K]*.pdf` takes the seven not starting with K |
| `**/` | descend into subdirectories | `**/*.pdf` |

Two things that cost debugging time:

- **Matching is case-sensitive.** `*.pdf` skips a file named `report.PDF`.
- **Brace expansion does not work.** `{a,b}*.pdf` is not an error, it simply matches
  nothing, so the source ingests zero files and the run looks successful. Use two
  sources, or a character class.

If `path` names a single file rather than a directory, `glob` is ignored.

## 3. Choose a chunking strategy

Documents are cut into pieces before they are stored, because searching works
better on small pieces than on whole documents. There are several ways to cut:

| Strategy | What it does | Use for |
|---|---|---|
| `fixed_size` | Cuts every so many characters, with a little overlap so sentences are not lost at the seam | Plain documents with no clear structure |
| `heading` | One piece per section, splitting only sections that are too long | Documents with proper headings |
| `passthrough` | One piece per record, never split | Rows from JSON/CSV files |
| `semantic` | Cuts where the topic changes instead of at a fixed length. More accurate, but costs extra because it analyses the text while reading it in | Long flowing text without usable headings |
| `docling_hybrid` | Docling's own method. Handles tables and figures itself and sizes the pieces so they always fit the model | PDFs only |

Set one default under `chunking:` for everything, and override it for a single
set of documents with a `chunking:` block inside that entry.

## 4. Read the documents in

!!! tip "Just adding or removing a document?"
    [Changing your documents](managing-documents.md) is the short, plain-language
    version of this step. The rest of this page is about formats and chunking.

```bash
export RAG_CONFIG=my-rag.yaml
python -m kb.ingest                         # embed + upsert into the collection
python -m kb.ingest --only faq              # just one set of documents
```

A plain run keeps the collection in step with the folder. Each file is remembered
with a fingerprint of its contents, so:

- a **new** file is read in,
- an **edited** file is read again, because its fingerprint changed,
- an **unchanged** file is skipped, costing nothing,
- a **deleted** file has its entries removed, so it stops turning up in answers.

Managing your documents is therefore just managing the folder: add, replace or
delete files and run the same command again. Replacing your whole set of documents
in one go works too, removing the old entries and reading the new files in the same
run. The app also does this by itself: it watches the folders and runs the same thing
within seconds of a change, so in normal use you never call this manually. Set
`DOCUMENT_WATCH=false` to turn that off.

!!! warning "One deliberate exception"
    If the folder turns out to be **completely empty** while the collection knows
    about files, nothing is deleted. An empty folder is almost always a mount that
    did not come up or a wrong `path`, and quietly wiping the collection over that
    would be worse than doing nothing. You get a warning saying so. To empty a
    collection on purpose, use `--recreate`.

    Deleting entries needs to know which ones belong to the file. That works for
    PDF, Markdown and text sources. A `json` or `csv` source whose `field_mapping`
    writes no `source_file` cannot be matched up, so those entries are kept and
    reported, and `--recreate` clears them.

!!! danger "Give every document a unique file name"
    Documents are identified by their **file name alone**, not by which folder they
    are in. So two files both called `intro.pdf`, in different folders of the same
    collection, are the same document as far as the app is concerned: only one of
    them ends up being searchable and the other is lost. Deleting one of them is
    also refused, because its entries cannot be told apart from the other's.

    A run now warns you when it sees a repeated name. If you get that warning,
    rename the files so each is unique and read them in again with `--recreate`.

!!! warning "When you do need `--recreate`"
    Fingerprints only cover the files. If you change something that affects how
    every document is cut up or searched, the existing entries are stale and the
    whole collection has to be rebuilt:

    ```bash
    python -m kb.ingest --recreate
    ```

    That applies to a different `chunking` strategy, different chunk sizes, or
    switching `images.mode` from `none`. Switching `embed_model` is refused
    outright, because old and new vectors cannot be compared; use `--recreate` or
    a new `vector_store.collection`.

    The old `--skip-if-exists` flag still exists but is no longer useful: it stops
    the run entirely when the collection exists, which is exactly what prevents
    added, edited and deleted files from being noticed.

## 5. Make citations open the source file

For a click on a source to open the actual document, its file type has to be
listed as allowed:

```yaml
sources:
  data_dir: ../../data/handbook
  served_extensions: [.pdf, .txt, .md]
```

**You do not have to list your folders here.** Files are served from `data_dir`
*and* from every folder in `data_sources[]`, so a corpus split across several
folders has all of its citations clickable with no extra configuration. `data_dir`
is searched first, so if the same filename exists in two folders, the copy in
`data_dir` is the one that opens.

`data_dir` still matters for two other things: it is where figure images and
figure descriptions are written (`<data_dir>/figures`, `<data_dir>/descriptions`).

The reference under an answer is assembled from what the app noted while reading:
file name, title and page. The built-in readers fill this in automatically. If
someone writes a [custom parser](extending.md), it should do the same. To show
additional fields of your own in a citation, list them under
`citation.extra_fields`.

## 6. Split one instance into parts of a corpus

A corpus is often made of parts you want to search separately, because they differ in
length, have different readers, or simply do not answer the same question. Rather than
running several instances, you can keep them in one and let the user choose which part
is searched.

Three places have to agree: each part gets its own data source with a label, the label
is allowed to be filtered on, and a role filters on it.

```yaml
data_sources:
  - name: handbooks
    path: docs/handbooks
    format: pdf
    extra_metadata: { category: handbook }
  - name: leaflets
    path: docs/leaflets
    format: pdf
    extra_metadata: { category: leaflet }
    chunking: { strategy: heading }   # short documents, different chunking

retrieval:
  payload_indexes: [category]                  # Qdrant index for the field
  filterable_fields: [source_file, category]   # allow-list

profiles:
  - id: handbooks
    name: "Handbooks"
    retrieval_filters: { category: handbook }
  - id: all
    name: "All documents"                      # no filter: searches everything
```

`extra_metadata` is copied onto every chunk the source produces, so the label travels
with the text. It is not the only thing you can filter on: the parsers already put
`source_file` (the filename), `page_start`, `page_end`, `section_title` and
`section_index` on every chunk, and any of those can go in `filterable_fields` too.
`source_file` is the reason the assistant can scope a search to one document. `filterable_fields` is an allow-list: a filter on a field that is not
listed is **silently ignored**, which is the usual reason a profile appears to do
nothing. `payload_indexes` builds the Qdrant index for it; without one, filtering
still works but scans.

A profile can filter on one category or on several at once:

```yaml
profiles:
  # one category
  - id: handbooks
    name: "Handbooks"
    retrieval_filters: { category: handbook }

  # several categories, OR
  - id: up-to-2023
    name: "Up to 2023"
    retrieval_filters: { period: [up_to_2019, "2020_2023"] }

  # several fields, AND
  - id: older-handbooks
    name: "Older handbooks"
    retrieval_filters: { period: up_to_2019, category: handbook }

  # no filter: searches everything
  - id: all
    name: "All documents"
```

`category` and `period` are example names here: they are the labels you set yourself in
`extra_metadata`.

The two forms pull in opposite directions. A list on one field is an OR and makes the
search **wider** — a chunk matches if any of the values does. Several fields are an AND
and make it **narrower**, because a chunk then has to satisfy every condition.

That is why AND only makes sense across *different* fields. A chunk has exactly one
period, so "period up_to_2019 and period 2020_2023" would always be empty: no chunk can
be both. Two values of the same field need the list form. Writing the key twice does not
help either — YAML simply keeps the second one.

Each part can also be chunked differently, which is often the real win: a two-page
leaflet and a twenty-page handbook do not want the same strategy.

!!! warning "A filter is not a permission"
    `retrieval_filters` scopes what is *searched*. Anyone who can use the app can pick
    a role that has no filter and reach every part, and nothing in this template
    grants or withholds access per document. If one part has a different audience than
    the others, give it its own collection and its own instance. A filter is not a
    boundary.

One limit worth knowing: the assistant can narrow a search to a single document on its
own (the `search` tool takes a `document` argument when `source_file` is filterable),
but it cannot choose a category. Categories come from the role the user picked.

A runnable version of exactly this lives in
`examples/papers/rag.config.multi-source.yaml`. It splits the nine shipped papers into
three parts by publication period, with one role per part and one that searches all of
them.

To run it, point `RAG_CONFIG` at it in `apps/chainlit/.env`:

```bash
RAG_CONFIG=examples/papers/rag.config.multi-source.yaml
```

then `docker compose up -d --build`. The app and the ingest service read that same
variable, so one edit switches both, and ingest builds the collection before the app
starts.

It writes its own collection, so it sits alongside the annotated example rather than
replacing it: switching `RAG_CONFIG` back leaves both intact.
