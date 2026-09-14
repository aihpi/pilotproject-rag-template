# When something goes wrong

Errors that have actually happened, with what causes them and what to do.

## Start here

Most problems are one of three things: your settings, your connection, or the AI
service. This tells you which, from `apps/chainlit`:

```bash
make check
```

It tries every model a few times and reports each one. Anything other than all
green is explained below.

## The build stops with "network timeout"

`docker compose up -d --build` installs around 470 MB of packages, and uv fetches many
of them at the same time. On a slow or shared line one of those downloads can sit idle
long enough that uv gives up, which stops the whole build:

```
× Failed to download `tokenizers==0.22.2`
╰─▶ Failed to download distribution due to network timeout.
```

A different package is named each time. That is the clue: the packages are fine, the
line is the problem.

Run the build again. Docker keeps every step that already finished, but the step that
failed starts its downloads over, so simply repeating it is not enough on a really slow
connection. The lever that works is fewer downloads at once, because each one then gets
more of the line and is less likely to stall:

```
UV_HTTP_TIMEOUT=1800 UV_CONCURRENT_DOWNLOADS=2 docker compose build
```

A wired connection, or leaving the VPN off, helps for the same reason.

## Some calls work and others fail

The check reports something like `only 3 of 5 attempts worked`, or reading documents
produces many `Connection error` messages while a few figures succeed.

That pattern means your settings are fine. A wrong address or key fails every time,
not sometimes. The connection between you and the service is dropping requests.

Reading documents is where this hurts, because it makes hundreds of calls: a
connection that loses one request in three will fail dozens of times, while a single
chat message may work fine and hide the problem.

What to try, in order:

1. **Turn the VPN off**, if you use one. It is the most common cause.
2. **Use a different network.** A busy shared network in a full room does this.
3. **Wait and retry.** The service itself may be overloaded.
4. **Read documents in without pictures first**, by setting `images.mode: none`. That
   removes most of the calls, so you can confirm everything else works before paying
   for figure descriptions.

## "database disk image is malformed"

The file holding your **chat history** got damaged. Nothing else is affected: your
documents and everything the assistant learned from them are stored separately.

**Why it happened.** That file used to be kept in a folder shared between your
computer and the app. Sharing a folder like that is convenient, but this kind of file
does not tolerate it: if the app is stopped at exactly the wrong moment while it is
writing, the file can break. Restarting the app repeatedly makes it more likely.

**Who it affects.** Only Mac and Windows. On Linux the shared folder behaves
differently and the problem cannot happen.

**Already fixed.** The file has been moved somewhere the app has to itself, and your
existing history was carried over automatically. You should not see this again.

**If you are seeing it now**, rescue the old messages like this, from
`apps/chainlit`:

```bash
docker compose stop chainlit
mv .chainlit/chat_history.sqlite3 .chainlit/chat_history.broken
sqlite3 .chainlit/chat_history.broken ".recover" | sqlite3 .chainlit/chat_history.sqlite3
docker compose up -d
```

The third line builds a healthy file out of everything that is still readable. Keep
the `.broken` file until you have checked that your history looks right.

## A new PDF does not show up

Put the file in `apps/chainlit/data/documents/` and wait a few seconds. The app
watches the folder and reads new files by itself.

If nothing happens, check in order:

1. **Is the app running?** `docker compose ps`
2. **What does it say?** `docker compose logs -f chainlit` and look for `[watch]`
   lines. They name every file that was added, changed or removed.
3. **Is the name unique?** Documents are recognised by file name only. A second file
   called `intro.pdf` counts as the same document, and the run warns about it.
4. **Is it the right folder?** If you use your own settings file, `data_sources[]`
   decides which folder is watched, and paths count from the folder the settings file
   is in.

See [Changing your documents](managing-documents.md).

## "the 'tesseract' binary was not found"

You switched on `ocr: true`, which reads text out of pages that are really pictures.
The program that does that is not included, on purpose, to keep the download small.

Most PDFs do not need it. A normal PDF already contains real text you can select and
copy, and the default setting reads those correctly. You only need this for scans,
where somebody photographed or scanned a paper page.

If your documents really are scans, the error message prints the exact three lines to
add so the program gets installed.

## The app cannot reach a model running on my own computer

Inside a container, `localhost` means the container itself, not your machine. Use
`host.docker.internal` instead:

```
LITELLM_BASE_URL=http://host.docker.internal:11434/v1
```

Remember that the model names in the settings file come from a hosted service and
will not exist on your own server, so change those too.

## Port 8000 is already in use

Something else on your machine is using it. Pick another port in `.env`:

```
APP_PORT=8080
```

The same works for `QDRANT_PORT` and `POSTGRES_PORT`. Only the port on your computer
changes, so nothing inside the app needs adjusting.

## The assistant answers, but no sources appear

Source references and follow-up questions are recognised by German wording, so they
only work with `language: de` in your settings file. Your documents themselves can be
in any language. This is a known limitation.

## A source is named but clicking it does nothing

The reference appears under the answer as plain text rather than a link, or the
source panel skips a number (Quelle 2, 3, 5 with no 4).

The app says which file it could not link. Look for this in the log:

```bash
docker compose logs --tail 100 chainlit | grep citation_unlinkable
```

Each line names the file the answer cited and the number it was cited as. Three
things cause it:

1. **The file type is not served.** Only extensions listed in
   `sources.served_extensions` can be opened. A `.docx` source is indexed and
   answerable but not clickable until you add `.docx` there.
2. **The file is not on disk any more.** The search index and the documents folder
   disagree: the chunks were indexed from a file that has since been renamed,
   moved or deleted. Re-reading the documents fixes it.
3. **The file is in a subfolder.** Files are served from `sources.data_dir` and
   from every folder in `data_sources[]`, but not from folders *inside* those. A
   source indexed with a `**/*.pdf` glob can therefore be cited and not opened.

You do **not** need to list your folders under `sources:`. If a corpus spread
across several `data_sources[]` folders only links its `data_dir` files, that is a
bug from before this behaviour existed, not a configuration mistake.

The skipped number in the panel is deliberate. The number the answer shows is the
position the model actually read, so an unlinkable source leaves a gap rather than
shifting every later number by one. A gap is a true statement about what was
retrieved.

## Reading documents stopped with an error

The run now ends with an explanation rather than a wall of text: what the error was,
what it means, and numbered steps. It also names `make check`, which tests your
connection and settings on their own.

One thing to know: documents indexed by earlier runs are safe, but whatever was being
read when it stopped was not saved. Running it again reads those files once more.

## The score badge does not appear

The badge above the chatbox shows how well each answer scored. If it never appears,
check these in order:

1. **Is evaluation turned on?** The schema default is off, but the bundled
   `examples/papers` instance ships it on — so check which config you are running
   before changing anything. For your own config, `evaluation: {enabled: true}`, and
   the app has to be restarted after adding it. The evaluation service starts with
   the rest of the stack — check it is up with `docker compose ps` if scores never
   appear.
2. **Did you ask a question that uses the knowledge base?** Evaluation only scores
   answers that looked something up. If the assistant answered from general
   knowledge or said "not in the documents", there is nothing to score.
3. **Wait about fifteen seconds.** Scoring happens in the background after the
   answer is already shown. The badge appears once the score arrives.

If none of that helps, check the app log for lines starting with `[WARN]
eval_status_unavailable`. That means the app cannot reach the evaluation service.

## Scores are all empty (NULL)

The badge appears but the numbers are missing. This means the scoring model (the
*judge*) tried to grade the answer but failed.

The most common cause is that the judge model is not available at the AI service
right now. Check:

```bash
docker compose logs --tail 20 eval
```

If you see `500 Internal Server Error` or `Connection error`, the judge model is
down at the service. Wait and try again, or point `evaluation.judge_model` in your
settings file at a different model that your service offers.

A failed score is stored as empty, never as zero. That way a temporary outage does
not drag the averages down.

## The badge shows numbers I do not trust

Two things to check:

**Are you comparing across different models?** If `evaluation.judge_model` is not
set, the model that *answered* also grades its own answer. That makes scores
unreliable when you switch models in the settings panel, because each model judges
itself differently. Set `judge_model` to a fixed model that is not the one being
judged.

**What do the metrics actually measure?**

- **Faithfulness** checks whether the answer's claims are supported by the text
  passages that were retrieved. It breaks the answer into individual statements,
  then checks each one against the sources. The formula is simply: supported
  claims divided by all claims. A score of 0.5 does not mean "mediocre". It means
  half the statements in that answer were not backed by the sources. The badge
  panel lists every claim with a tick or cross and the judge's reason, so you can
  see exactly which one failed.

- **Relevance** checks whether the answer actually addresses the question. It
  generates questions from the answer and compares them with the original question
  using embedding similarity. If the generated questions are close to the real one,
  the answer is relevant. A relevance of 0% usually does not mean the answer was
  off-topic. It typically means the assistant declined to answer ("not in the
  documents"), which is correct behaviour when the knowledge base does not cover
  the question.

Both scores come from the [RAGAS](https://docs.ragas.io/) evaluation framework.
They are *reference-free*: no hand-written correct answers are needed. The trade-off
is that they measure what the judge model *thinks*, which carries its own opinions
and noise. Compare changes between runs ("did this config change help?"), do not
read a single number as a verdict.

## Nothing here matches

The log is the fastest way to find out more:

```bash
docker compose logs --tail 100 chainlit
```

Errors while documents are being read start with `[ingest]`, and messages from the
folder watcher start with `[watch]`.
