# Running on a Mac with Ollama

This page sets up the template and its AI models together on one Mac with Apple silicon, such as a Mac Studio. The models run in [Ollama](https://ollama.com), so your documents and questions never leave the machine. The template talks to Ollama the same way it talks to a hosted AI service, which is why no code has to change.

The example uses Gemma 4 31B to write answers, describe figures and score answers, and Octen-Embedding-8B to make text searchable. Once the chat runs for you, [Running the Mac as a shared server](mac-server.md) keeps it running and opens it to colleagues.

## What you need

- A Mac with Apple silicon and macOS 14 (Sonoma) or newer.
- [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) and Git.
- [Ollama](https://ollama.com/download), installed as the normal Mac app.

Install Ollama as the Mac app and leave it out of Docker. Docker on macOS cannot use the Mac's graphics chip, so a model inside a container runs on the processor alone and answers many times slower.

## 1. Download the models

```bash
ollama pull gemma4:31b
```

```bash
ollama pull benjaminj/octen-embedding-8b
```

```bash
ollama cp benjaminj/octen-embedding-8b octen-embedding-8b
```

The last command gives the embedding model a second name without a slash. The template reads a name with a slash as `provider/model`, so it would take `benjaminj` for the provider and the call would fail. The new name is also the one the hosted service uses, so the `embed_model` line in the settings file stays as it is.

The Octen model is a community build that Octen did not publish themselves. It is compressed (quantized), so its results differ slightly from the full model on a hosted service. After you have read your documents in, run [the answer scoring](evaluation.md) once and compare the scores with the ones you had before.

Check that Gemma can do what the template asks of it:

```bash
ollama show gemma4:31b
```

Under `Capabilities` the template needs `tools`, because the assistant searches your documents by calling tools, and `vision` for describing figures. If `tools` is missing, pick another chat model that lists it, for example a Qwen3 model.

## 2. Check Gemma's context window

The context window is how much text a model reads at once. A single answer can send whole documents to the model: the template allows about 30,000 tokens per tool result (`tools.max_context_chars`, 120,000 characters) and calls tools several times per answer, so it needs a window of at least 128K tokens. When a prompt is longer than the window, Ollama drops part of it and the app shows no error.

Ollama chooses the window from how much memory the graphics chip may use, and with 48 GB or more it takes 256K, the most Gemma 4 31B can read. Load the model once and check what it chose:

```bash
ollama run gemma4:31b "Hallo"
```

```bash
ollama ps
```

The `CONTEXT` column shows the window. `262144` is 256K, the normal case on a Mac with plenty of memory, and nothing needs to change. On a Mac that others rely on, [pin the window](mac-server.md#pin-gemmas-window) so it stays that way.

??? info "If the window is smaller than 128K"

    Ollama picks a smaller window on a Mac with less memory, in older versions, or when the slider in the Ollama app is set lower. Make a copy of the model with a fixed window, and use the copy's name instead of `gemma4:31b` everywhere in step 4. For Gemma with 128K:

    ```bash
    printf 'FROM gemma4:31b\nPARAMETER num_ctx 131072\n' > Modelfile
    ```

    ```bash
    ollama create gemma4-31b-128k -f Modelfile
    ```

    The copy uses the files already downloaded for the model and keeps its window when Ollama or the Mac restarts. For another model, put its name after `FROM` and choose a window it supports.

    If your chat model reads less than 128K, also lower `tools.max_context_chars` in your settings file to about as many characters as the model's window has tokens, for example `32000` for a 32K model. Otherwise a single tool result can fill the whole window.

??? info "How much memory each window needs"

    The model itself takes about 20 GB. On top of that comes a cache that grows with the window, and Ollama reserves it in full as soon as it loads the model, even for short questions.

    | Window | Cache | Cache with the two settings below | Model and cache together |
    |---|---|---|---|
    | 4K, Ollama's default below 24 GB | 1.2 GB | 0.6 GB | about 21 GB |
    | 32K, default from 24 to 48 GB | 3.5 GB | 1.8 GB | about 24 GB |
    | 128K, what the template needs | 11.6 GB | 5.8 GB | about 32 GB |
    | 256K, default from 48 GB and Gemma's maximum | 22.3 GB | 11.2 GB | about 42 GB |

    The cache sizes are calculated from Gemma 4 31B's published architecture for one question at a time; every question answered in parallel needs its own cache. Ollama also needs a few GB of working memory, and Octen adds its own size, which `ollama list` shows.

    On a Mac with less memory, these two settings halve the cache at a small cost in precision:

    ```bash
    launchctl setenv OLLAMA_FLASH_ATTENTION 1
    ```

    ```bash
    launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
    ```

    Quit and reopen Ollama afterwards. Both settings are lost when the Mac restarts, unless you [start Ollama with them](mac-server.md#start-everything-after-a-restart). If the model still does not fit, [give the models more of the Mac's memory](mac-server.md#give-the-models-the-macs-memory).

A large window does not slow down short questions, but the Mac takes a while to read a very long prompt before it starts answering, so questions that pull in whole documents take longer.

## 3. Point the template at Ollama

Get the project as described in [Getting Started](getting-started.md):

```bash
git clone https://github.com/aihpi/pilotproject-rag-template.git
cd pilotproject-rag-template/apps/chainlit
cp .env.example .env
```

In `.env`, set the address and key to these values:

```
LITELLM_BASE_URL=http://host.docker.internal:11434/v1
LITELLM_API_KEY=ollama
```

Ollama does not check the key, but the app expects one, so any word will do. The address uses `host.docker.internal` because inside a container `localhost` means the container itself and not your Mac.

## 4. Put the model names into your settings file

Make your own settings file from the example and tell the app to use it with `RAG_CONFIG=my-rag.yaml` in `.env`:

```bash
cp examples/papers/rag.config.yaml my-rag.yaml
```

```bash
sed -i '' 's#\.\./\.\./data/documents#data/documents#' my-rag.yaml
```

The example finds the documents from its own folder, `examples/papers/`. The second command points the copy at `data/documents` from its new place; without it the read-in finds no documents at all.

In `my-rag.yaml`, replace the model names of the hosted service with the ones Ollama has:

```yaml
models:
  chat_model: gemma4:31b
  fallback_chat_model: null
  embed_model: octen-embedding-8b
  selectable_chat_models: [gemma4:31b]

images:
  vision_model: gemma4:31b
  vision_capable_models: [gemma4:31b]

evaluation:
  judge_model: gemma4:31b
```

`fallback_chat_model` is the model the app retries with when the first one fails. With a single chat model on the Mac there is nothing to fall back to.

`selectable_chat_models` fills the model list in the settings panel. Against a hosted service the app asks the service for that list by itself, but with an address that already ends in `/v1` the question goes to the wrong place, so the list has to be written out here. Add every chat model you pulled with Ollama that people may choose.

With one model on the machine, the judge scores answers its own model wrote. That is fine for comparing settings such as chunking, but scores from two different chat models are not comparable that way. See [Checking answer quality](evaluation.md).

If you later change `embed_model`, the stored documents no longer match the search model. Use a new collection name or read everything in again with `--recreate`, as described in [Changing your documents](managing-documents.md).

## 5. Check and start

```bash
make check
```

```bash
make up
```

`make check` tests the model address before anything else runs. `make up` builds and starts everything, reads in the documents and opens the chat at <http://localhost:8000>. With `images.mode: describe`, the first read-in sends every figure to Gemma once, which takes a while on a large collection.

Log in with `admin` / `admin`, ask a question about your documents and click a source under the answer. If the document opens, everything works.

## When answers are too slow

Ollama runs models through llama.cpp. Servers built on Apple's own MLX framework are often faster on a Mac, and two of them fit the template:

- [LM Studio](https://lmstudio.ai) with models in MLX format. Its server listens on port 1234, so the address becomes `http://host.docker.internal:1234/v1`. Model names in LM Studio contain a slash, so give each model a name without one when you load it.
- [vllm-mlx](https://vllm-mlx.is-a.dev/), which also handles requests from several people at once efficiently. It is a young community project, so test tool calls and figure descriptions before relying on it.

Whatever you use has to serve answers with tool calls, figure descriptions and embeddings from one address, under names without a slash. If one server cannot do all of it, put a [LiteLLM proxy](https://docs.litellm.ai/docs/simple_proxy) in front: it gives each model the name the template expects and forwards it to whichever server runs it. The template was built to talk to such a proxy.

Before you switch for good, run the [benchmark](evaluation.md) on both setups. It shows whether the new server is faster and whether the answers stay as good.
