# RAG Template

A chat assistant that answers questions about your own documents and shows the
exact page each answer came from.

You point it at a folder of files. It reads them, and afterwards people can ask
questions in normal language. To set it up you edit **one settings file**. No
programming needed.

Under the hood: **Chainlit** draws the chat window, **LiteLLM** talks to the AI
models, and **Qdrant** stores your text so it can be searched.

## What you can change from config

Everything below is a setting in that one file, so you never have to touch code.

| What | Setting | Your options |
|---|---|---|
| **Your documents** | `data_sources[]` | PDFs, plain text and Markdown, or spreadsheet-like `json`/`csv` files |
| **How text is split up** | `chunking.strategy` | by size, by heading, one piece per record, by meaning, or Docling's own way. Can differ per document type |
| **Which model answers** | `models.chat_model` | any model your AI service offers |
| **Which model makes text searchable** | `models.embed_model` | any search model your AI service offers |
| **How sources look** | `citation.*` | the wording and layout of the reference under an answer |
| **What the assistant is told** | `prompt`, `profiles` | its instructions, the example questions, and optional roles that limit what it may search |
| **What the assistant may do** | `tools.enabled` | search, list all documents, read a whole document, fetch surrounding text, double-check a claim |
| **Pictures and charts** | `images.mode` | ignore them, describe them in words so they are findable, or show them to a model that can see |

## How it fits together

Your documents are read, cut into pieces and stored so they can be searched. When
a question comes in, the matching pieces are looked up and given to the AI model,
which writes the answer and lists its sources.

```
data_sources ─► parser (by format) ─► chunker (by strategy) ─► embed ─► Qdrant
                                                                         │
user question ─► retrieve (top_k, optional filters) ◄────────────────────┘
             └─► LLM tool loop ─► answer + config-driven citations
```

- **[Getting Started](getting-started.md)**: install it, load your documents, run it.
- **[Running on a Mac with Ollama](mac-ollama.md)**: run the models on your own Mac.
- **[Running the Mac as a shared server](mac-server.md)**: keep it running and share it with colleagues.
- **[Example Corpus](example-corpus.md)**: the papers that come with it, and how to swap them out.
- **[Adding Your Data](adding-data.md)**: use your own files.
- **[Agentic Tools](tools.md)**: what the assistant is allowed to do.
- **[Figures & Images](images.md)**: how pictures and charts are handled.
- **[System Prompts](prompts.md)**: write the assistant's instructions, or let it write them.
- **[Configuration Reference](configuration.md)**: every setting, in detail (technical).
- **[Field-Mapping DSL](field-mapping.md)**: turn JSON/CSV into text (technical).
- **[Extending](extending.md)**: support a new file type (needs Python).
- **[Feedback Export](feedback-export.md)**: collect and download user ratings.
- **[Checking Answer Quality](evaluation.md)**: score answers and compare configurations.
