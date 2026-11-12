# Figures & images

By default, when the app reads a PDF it **throws the pictures away** and keeps
only their captions as text. For papers and handbooks whose whole argument sits
in a diagram, that loses a lot. The `images:` block turns pictures and charts
into content the assistant can actually find and show.

```yaml
images:
  mode: none            # none | describe | attach
  vision_model: gemma-4-31b
  images_scale: 2.0     # Docling render scale (2.0 = higher-res crops)
  inline_figures: true
  show_unmarked_figures: true
  inline_figure_caption: false
  max_attach_images: 3
  attach_image_max_px: 768
  vision_capable_models: [gemma-4-31b]
```

You can switch the mode without editing the file, using the `IMAGES_MODE`
setting. That makes it easy to compare two modes on the same documents.

## Three modes

| Mode | While reading documents in | When someone asks a question |
|---|---|---|
| `none` | Pictures are ignored | Nothing (cheapest) |
| `describe` | Every picture is cut out and described in words by `vision_model` | The description is searched and quoted like any other text |
| `attach` | Same as `describe` | Additionally, the **picture itself** is shown to a model that can see images |

With `describe`, each picture becomes an ordinary searchable entry whose text is
the description the model wrote, in the language of your setup. It is stored with
a note that it is a figure, plus its page number and where the image file lives.

That has a useful consequence: because searching then only ever touches text,
`describe` works with **any** chat model afterwards. The model that can see
images is only needed once, while reading the documents in.

`attach` uses exactly the same reading step and adds the picture to the answer on
top.

## Cost, and when you must re-ingest

The rule is simple: **one call to the image model per picture, once, while
reading documents in.** Answering questions costs nothing extra in `describe`.

!!! warning "Switching modes"
    Going from `none` to `describe` or `attach` means **reading all documents in
    again**, because the pictures were never cut out or described the first time.
    Switching between `describe` and `attach` does **not**: images and
    descriptions are already stored, only the behaviour when answering changes.
    The flags for reading in again are in [Adding your data](adding-data.md).

Reading documents in again does not mean describing the pictures again. Each
description is written to a file next to your documents and reused, so a rebuild,
a change to how documents are cut into chunks, or an import that failed halfway
all cost nothing for pictures that were already described.

They are plain Markdown, one file per picture, grouped by document, so you can
read what the model wrote:

```
data/documents/
├── Kage_2018_SciReports.pdf
├── figures/
│   └── Kage_2018_SciReports__fig0.png
└── descriptions/
    └── Kage_2018_SciReports/
        ├── fig0.md
        └── fig1.md
```

Each file starts with a short header, a fingerprint of the picture, the prompt and
the image model, followed by the description itself. A stored description is
reused only while that fingerprint still matches, so editing `describe_prompt`,
switching `vision_model` or changing `describe_image_max_px` correctly asks for
fresh ones.

You can correct a description by hand, and the header is what decides whether it
is kept, so your text survives. It does not reach the assistant straight away
though: the automatic update only reacts to changes in the documents themselves,
so a hand-edited description is picked up on the next **full rebuild**.

To force fresh descriptions for one document, delete its folder under
`descriptions/` and read your documents in again with `--recreate`. `--recreate` is
needed because an unchanged PDF is otherwise skipped, see
[Changing your documents](managing-documents.md).

## Showing figures in the answer

Two separate switches decide what people actually see:

| `inline_figures` | `show_unmarked_figures` | Result |
|---|---|---|
| `true` | `true` | The relevant picture appears **above** the paragraph about it, and any other pictures found appear as small previews below the answer (default) |
| `true` | `false` | Only pictures the model explicitly pointed at appear. Tidiest, but nothing shows if the model forgets to point |
| `false` | `true` | No pictures in the text, but every picture found appears as a small preview below the answer |
| `false` | `false` | No pictures shown at all. The descriptions stay searchable and quotable |

Set `inline_figure_caption: true` to also print the original caption in italics
under the picture.

## How the figure marker works

Worth understanding, because it explains both the good cases and the odd ones:

1. Along with the text, the model is given one extra line per picture, a kind of
   placeholder: `Abbildungs-Marker: {{ABB:<file name>}}`.
2. It is asked to copy that placeholder, unchanged and on its own line, directly
   before the paragraph that talks about the picture.
3. Afterwards the app swaps the placeholder for the real image.

If a placeholder cannot be matched, it is quietly removed (nobody ever sees a raw
`{{ABB:…}}`) and the picture simply appears below the answer instead. You can
reword the instruction given to the model via `images.figure_marker_prompt`.

## If your figures have no descriptions

Sometimes describing a figure fails, usually because the AI service is briefly
busy or the figure is unusually large. The app retries a few times, and if it
still fails the figure is left out rather than stored without a description.

**How to spot it.** Watch the output while your documents are read in. Each
document reports its failures:

```
[ingest] Alam_2026_SciReports.pdf: 2 of 17 figure descriptions failed
```

No such line means nothing failed. Do not judge this from the stored entries
themselves: a described figure is often split into several pieces, one of which
can be as short as `Abbildung 7 (Seite 3)`. That is normal and does not mean the
description is missing.

**How to fix it.** Only needed if you actually saw failures. There is no way to
repair single figures, so this redoes the whole collection:

```bash
make reingest        # with Docker: rebuilds and restarts the app
```

Or, spelled out:

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
docker compose up -d
```

Without Docker:

```bash
RAG_CONFIG=examples/papers/my-rag.yaml uv run python -m kb.ingest --recreate
```

!!! warning "This costs money again"
    Every figure is described once more, so the whole collection is charged again.
    The example corpus has 170 figures. Check with `images.mode: none` first if
    you only want to test that ingestion works at all.

## Where the images live

Pictures are saved as PNG files in `<sources.data_dir>/figures/` (change the
location with `images.figure_store_dir`). They are only handed out to logged-in
users, the same rule that applies to your source documents. The folder is
deliberately excluded from version control: it is rebuilt every time documents
are read in, so it does not belong in the project.

## `attach` needs a vision-capable chat model

Not every chat model can look at pictures, and the AI service does not announce
which ones can. So you have to list them yourself under
`images.vision_capable_models`. If the model in use is not on your list, the app
quietly falls back to a normal text answer and notes a warning. The answer is
still correct, just without the picture.

Before sending, each picture is shrunk so its longest side is at most
`attach_image_max_px`, and converted to JPEG. Do not remove that step:
full-resolution pictures make AI services reject the request outright (error
**413**). `max_attach_images` limits how many pictures a single answer may carry.

!!! note "A cosmetic quirk"
    The chat window shows images in a wide frame of fixed proportions. Tall
    pictures, such as portrait diagrams or stacked charts, therefore get empty
    margins left and right. Nothing is cut off; clicking the image opens it in
    full.
