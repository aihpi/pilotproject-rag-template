"""Typed configuration schema for the RAG template.

A single declarative config file (YAML) describes an entire RAG instance:
data sources, chunking, models, vector store, retrieval, citations, prompt,
profiles and UI strings. The models here are the single source of truth for
that schema — the docs "Config Reference" page is generated from them via
mkdocstrings, so keep the docstrings accurate.

Secrets/infra fields (API keys, URLs) default to ``None`` in YAML and are
filled from environment variables by :mod:`config.loader`, so they never need
to live in the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class ModelsConfig(BaseModel):
    """Chat and embedding model selection (all routed through LiteLLM).

    The defaults are deliberately **open-weight** models, so a fresh instance
    never depends on a proprietary vendor. The exact strings are gateway-specific
    — query ``GET /v1/models`` and use whatever your gateway advertises.
    """

    chat_model: str = "gpt-oss-120b"
    """LiteLLM ``provider/model`` string for the chat model. Default is OpenAI's
    Apache-2.0 open-weight release, not the hosted GPT API."""
    fallback_chat_model: str | None = None
    """Model to retry a chat call on when ``chat_model`` fails, via LiteLLM
    ``fallbacks=``. Covers an outage of one model group on the gateway: without
    it a single unavailable model takes down answering *and* the startup prompt
    generation, which then silently serves the bundled default prompt. Leave
    ``null`` to disable. Not applied to embeddings (a different vector space) or
    to the evaluation judge (deliberately pinned, so scores stay comparable)."""
    embed_model: str = "octen-embedding-8b"
    """LiteLLM ``provider/model`` string for embeddings."""
    selectable_chat_models: list[str] = Field(default_factory=list)
    """Chat models offered in the settings-panel model selector. Merged with
    whatever the gateway advertises via ``/v1/models`` and the active
    ``chat_model``. Use this when the gateway does not enumerate its models."""
    litellm_base_url: str | None = None
    """LiteLLM gateway base URL (env ``LITELLM_BASE_URL`` overrides)."""
    litellm_api_key: str | None = None
    """LiteLLM gateway API key (env ``LITELLM_API_KEY`` overrides)."""


class VectorStoreConfig(BaseModel):
    """Vector database connection and collection."""

    provider: Literal["qdrant"] = "qdrant"
    url: str = "http://localhost:6333"
    """Qdrant URL (env ``QDRANT_URL`` overrides)."""
    api_key: str | None = None
    """Qdrant API key (env ``QDRANT_API_KEY`` overrides)."""
    collection: str = "documents"
    """Collection name (env ``QDRANT_COLLECTION`` overrides)."""
    distance: Literal["cosine", "dot", "euclid"] = "cosine"


class ChunkingConfig(BaseModel):
    """How parsed sections are split into chunks before embedding."""

    strategy: Literal[
        "fixed_size", "heading", "passthrough", "semantic", "docling_hybrid"
    ] = "fixed_size"
    """``fixed_size`` = sliding window; ``heading`` = one chunk per parsed
    section (with an oversize guard); ``passthrough`` = one chunk per section,
    never split (for structured JSON/CSV records); ``semantic`` = split each
    section at embedding-similarity breakpoints (embeds sentences at ingest —
    not free); ``docling_hybrid`` = Docling's native token-aware chunker
    (PDF only; serializes tables/figures itself, sizes by the embed tokenizer)."""
    max_chars: int = Field(default=3000, gt=0)
    overlap: int = Field(default=300, ge=0)
    min_section_chars: int = Field(default=40, ge=0)
    semantic_breakpoint_percentile: float = Field(default=95.0, ge=0.0, le=100.0)
    """``semantic`` only: consecutive-sentence distances above this percentile
    start a new chunk. Lower = more, smaller chunks."""
    hybrid_max_tokens: int | None = Field(default=None, gt=0)
    """``docling_hybrid`` only: token cap per chunk. ``None`` uses the
    tokenizer's model default."""

    @model_validator(mode="after")
    def _check_overlap(self) -> "ChunkingConfig":
        if self.overlap >= self.max_chars:
            raise ValueError(
                f"chunking.overlap ({self.overlap}) must be smaller than "
                f"max_chars ({self.max_chars})"
            )
        return self


class IterStep(BaseModel):
    """One level of descent for the nested-JSON ``record_specs`` DSL."""

    model_config = ConfigDict(populate_by_name=True)

    path: str | list[str]
    """A key to descend into, or several sibling list-keys to iterate together
    (e.g. ``[basis, standard, erhoeht]``)."""
    as_: str | None = Field(default=None, alias="as")
    """Bind the current element under this name for later ``{name.field}`` refs."""
    object: bool = False
    """Descend into an object without iterating (single dict, not a list)."""
    bind_key_as: str | None = None
    """When ``path`` is a list of sibling keys, capture which key we came
    through under this name (referenced as ``@name``)."""


class RecordSpec(BaseModel):
    """A nested-iteration record producer for the JSON field-mapping DSL."""

    iterate: list[IterStep]
    text_template: str
    """f-string over the bound namespace, e.g. ``"{req.titel}\\n\\n{req.inhalt}"``."""
    id_template: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    """payload_key -> value spec (dotted path, ``@bound_key``, ``{const: X}`` or ``{template: "..."}``)."""


class FieldMapping(BaseModel):
    """Declarative mapping from JSON/CSV records to chunk text + metadata."""

    delimiter: str = ","
    """CSV column delimiter."""
    record_path: str | None = None
    """Dotted path to the list of records (flat JSON). Omit for top-level lists."""
    text_template: str | None = None
    """f-string over a record's fields to build the chunk text."""
    text_fields: list[str] | None = None
    """Alternative to ``text_template``: join these fields with newlines."""
    id_template: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    """payload_key -> value spec (see :class:`RecordSpec`)."""
    record_specs: list[RecordSpec] | None = None
    """For nested/multi-record JSON — overrides the flat fields above."""


class PdfOptions(BaseModel):
    """PDF/Docling extraction options for a data source."""

    docling_json_dir: str | None = None
    """Folder of converted documents, one Docling JSON per PDF, same file stem.
    Reading these skips Docling entirely. A PDF under ``path`` that has no file
    here yet is converted on the next ingest, with this source's options, and
    saved here with a ``.stamp`` (Docling version, settings, PDF checksum); it is
    converted again when the stamp stops matching and only read otherwise. Files
    exported by hand (``docling --to json``) have no stamp and are left alone;
    delete one to reconvert that document."""
    ocr: bool = False
    """Read text off the page as an image. Only needed for scans: PDFs that
    already carry a text layer are read correctly with ``ocr: false``, and OCR
    makes ingestion much slower.

    **The shipped Docker image contains no OCR engine**, deliberately, to keep it
    small. Turning this on there fails immediately with an explanation. To use it,
    build a derived image that installs ``tesseract-ocr`` plus the language data
    you need, or run outside Docker with a ``tesseract`` on your PATH."""
    ocr_engine: Literal["tesseract", "mac"] = "tesseract"
    """``tesseract`` needs the binary on the PATH. ``mac`` uses the OCR built into
    macOS and needs nothing installed, but only works outside Docker."""
    ocr_lang: list[str] = Field(default_factory=lambda: ["eng", "deu"])
    """Language codes passed to the OCR engine. For ``tesseract`` each one needs
    its own package (e.g. ``tesseract-ocr-deu``)."""
    device: Literal["cpu", "mps", "cuda", "auto"] = "cpu"
    include_tables: bool = True
    """Serialize each table (as a Markdown grid) into the section it appears in,
    so table content is retrievable. Ignored by ``docling_hybrid`` (that chunker
    serializes tables itself). Applies to the ``heading``/``fixed_size``/
    ``semantic`` reconstruction path."""


class DataSourceConfig(BaseModel):
    """One corpus to ingest: where it is, its format, how to parse and chunk it."""

    name: str
    path: str
    """File or directory path (resolved relative to the config file)."""
    format: Literal["pdf", "txt", "md", "json", "csv", "custom"]
    glob: str | None = None
    """Glob for directory paths (e.g. ``*.pdf``)."""
    parser_name: str | None = None
    """Registered parser name — required when ``format == 'custom'``."""
    chunking: ChunkingConfig | None = None
    """Per-source override of the global chunking config."""
    field_mapping: FieldMapping | None = None
    """Required for ``json``/``csv`` formats."""
    pdf_options: PdfOptions | None = None
    extra_metadata: dict[str, Any] = Field(default_factory=dict)
    """Static metadata merged into every chunk from this source."""

    @model_validator(mode="after")
    def _check(self) -> "DataSourceConfig":
        if self.format in {"json", "csv"} and self.field_mapping is None:
            raise ValueError(
                f"data source '{self.name}': field_mapping is required for "
                f"format '{self.format}'"
            )
        if self.format == "custom" and not self.parser_name:
            raise ValueError(
                f"data source '{self.name}': parser_name is required for "
                f"format 'custom'"
            )
        return self


class RetrievalConfig(BaseModel):
    """Retrieval knobs and which payload fields are indexed/filterable."""

    top_k: int = Field(default=5, ge=1)
    max_top_k: int | None = None
    """Upper bound the model may request. Defaults to ``top_k`` when unset."""
    max_source_links: int = Field(default=8, ge=0)
    score_threshold: float = Field(default=0.0, ge=0.0)
    """Minimum cosine similarity for a hit. With ``hybrid`` on this bounds the
    **dense leg only** — the lexical leg has no comparable score, so a chunk that
    matches lexically can enter the fused results below this similarity."""
    hybrid: bool = False
    """Search dense *and* sparse (lexical) vectors and fuse the two rankings.
    Helps where embeddings are weakest: standard numbers, compound technical
    terms, proper names. Ingest always writes the sparse vector, whether this is on
    or off, so it is a pure query-time switch: no re-ingest either way, and one
    collection can serve a dense-vs-hybrid comparison. Off here as the schema
    default; the bundled ``examples/papers`` instance turns it on."""
    fusion: Literal["rrf", "dbsf"] = "rrf"
    """``hybrid`` only. ``rrf`` fuses on rank position, ``dbsf`` on
    distribution-normalized scores. RRF is the safer default; DBSF can win when
    one of the two retrievers is clearly stronger. Measure before switching."""
    prefetch_limit: int = Field(default=30, ge=1)
    """``hybrid`` only: candidates each leg retrieves before fusion. Must be at
    least ``max_top_k`` (enforced when ``hybrid`` is on) — below it, two legs
    returning the same candidates can fuse to fewer than ``top_k`` results."""
    payload_indexes: list[str] = Field(default_factory=list)
    """Metadata fields to build Qdrant keyword indexes on."""
    filterable_fields: list[str] = Field(default_factory=list)
    """Metadata fields ``retrieve()`` and profiles are allowed to filter on."""

    @model_validator(mode="after")
    def _default_max_top_k(self) -> "RetrievalConfig":
        if self.max_top_k is None:
            self.max_top_k = self.top_k
        elif self.max_top_k < self.top_k:
            raise ValueError(
                f"retrieval.max_top_k ({self.max_top_k}) must be >= top_k ({self.top_k})"
            )
        # Only when hybrid is on: prefetch_limit is a fusion knob, and enforcing it
        # unconditionally rejected configs that never read it — a plain
        # `max_top_k: 50` (or MAX_TOP_K=50 in the environment) would fail at import.
        if self.hybrid and self.prefetch_limit < self.max_top_k:
            raise ValueError(
                f"retrieval.prefetch_limit ({self.prefetch_limit}) must be >= "
                f"max_top_k ({self.max_top_k}) when retrieval.hybrid is on; a smaller "
                f"candidate pool can fuse to fewer than top_k results"
            )
        return self


class FilenameRule(BaseModel):
    """One declarative rule mapping stored source metadata to a served file."""

    when_field: str
    """Payload key to test (e.g. ``source``, ``doc_type``, ``source_file``)."""
    equals: str | None = None
    matches: str | None = None
    """Regex matched against the field value."""
    in_: list[str] | None = Field(default=None, alias="in")
    serve: str
    """Filename to serve when the rule matches."""

    model_config = ConfigDict(populate_by_name=True)


class CitationConfig(BaseModel):
    """How sources are rendered as citations and served to the UI."""

    segments: list[str] = Field(
        default_factory=lambda: ["{title}", "{source_file}", "p. {page}"]
    )
    """Ordered format-string segments; a segment is emitted only if every
    ``{field}`` it references resolves to a non-empty value."""
    separator: str = " — "
    token_word: str = "Source"
    """In-text citation token label; drives the clickable-citation regexes."""
    page_abbr: str = "p."
    panel_title: str = "Sources & Evidence"
    extra_fields: list[str] = Field(default_factory=list)
    """Optional metadata keys exposed to citation segments (e.g. domain fields)."""
    map_path: str | None = None
    """Optional bibliographic citation map JSON (env ``CITATION_MAP_PATH`` overrides)."""
    """Optional regex for domain IDs used as an extra citation-matching signal."""
    source_pdf_fallback: str | None = None
    """Fallback served filename for chunks without a resolvable source file."""
    labels: dict[str, str] = Field(default_factory=dict)
    """UI label overrides (file/page/section/open/source/…)."""


class SourcesConfig(BaseModel):
    """Where served source files live and which types are served."""

    data_dir: str = "data"
    """Primary directory of served source files (env ``DATA_RAW_DIR`` overrides).

    Not the only one served: the ``/sources`` routes also serve from every
    ``data_sources[].path``, so a corpus assembled from several folders has all of
    its citations clickable without listing those paths a second time here. This
    one is searched first, so it wins a duplicate filename, and it stays the anchor
    for ``<data_dir>/figures`` and ``<data_dir>/descriptions``."""
    served_extensions: list[str] = Field(
        default_factory=lambda: [".pdf", ".txt", ".md"]
    )
    """File types the ``/sources/...`` links may serve. A citation pointing at
    anything else returns 404, so add an extension here before expecting the app to
    open that kind of file."""
    filename_map: list[FilenameRule] = Field(default_factory=list)
    """Declarative rules for mapping stored names to served files."""


class PromptConfig(BaseModel):
    """System prompt and starter questions."""

    system_prompt_path: str | None = None
    """Path to a markdown system prompt (env ``SYSTEM_PROMPT_PATH`` overrides)."""
    starter_questions: list[str] | dict[str, list[str]] = Field(default_factory=list)
    """Questions offered on the welcome screen. Either one list, or one list per
    interface language (``{de: [...], en: [...]}``) for an instance whose users do not
    all read the same one. Resolve with ``settings.starter_questions(language)``."""
    starter_icons: list[str] = Field(default_factory=list)
    auto_generate: bool = True
    """When no system prompt is configured/loaded, generate one at startup with
    the chat model, grounded in ``template_path`` and a sample of the indexed
    chunks. Cached as ``.generated_system_prompt.<collection>.md`` next to the
    config (set env ``REGENERATE_SYSTEM_PROMPT=true`` to force a rebuild)."""
    template_path: str | None = None
    """Structural template for auto-generation (default: repo-root ``system.md``,
    else the bundled ``config/prompts/default_system.md``)."""
    sample_size: int = Field(default=40, ge=1)
    """How many indexed chunks to sample when auto-generating the prompt."""


class ToolConfig(BaseModel):
    """The retrieval (``search``) tool exposed to the model."""

    name: str = "rag_retrieve"
    description: str = "Search the knowledge base for documents relevant to the query."
    query_param_description: str = "The search query."
    top_k_param_description: str = "Number of documents to retrieve."


class ToolsConfig(BaseModel):
    """Which pluggable agentic-RAG tools are exposed to the model.

    ``enabled`` lists registry ids from the ``tools/`` package (order = schema
    order). Defaults to ``["search"]`` so instances that only declare the legacy
    ``tool:`` block behave exactly as before. The ``search`` tool takes its name
    and descriptions from ``tool:``; other tools' descriptions come from their
    language-aware defaults unless overridden in ``descriptions``.
    """

    enabled: list[str] = Field(default_factory=lambda: ["search"])
    descriptions: dict[str, str] = Field(default_factory=dict)
    """tool_id -> OpenAI function description override (``search`` uses ``tool:``)."""
    fetch_max_chunks: int = Field(default=200, ge=1)
    """Whole-document chunk cap for ``fetch_document``. Bounds the Qdrant scroll —
    it does **not** bound what reaches the model; ``max_context_chars`` does."""
    max_context_chars: int = Field(default=120000, ge=1000)
    """Character budget for one rendered tool context, enforced by dropping **whole
    chunks** from the tail — never by cutting inside a chunk.

    Sized from the model's context window, not from the corpus: ~30k tokens at the
    conventional chars/4, roughly a quarter of a 128k window, leaving room for the
    system prompt, the conversation so far and the answer. That is deliberately
    above the largest real document this corpus produces (~99,000 chars for a
    102-chunk paper), so ``fetch_document`` keeps its contract of returning a whole
    document. It bounds the case no window could take: ``fetch_max_chunks`` (200) x
    the chunker's ceiling (6000) is 1.2M chars, ~300k tokens.

    **Lower it if your gateway serves a smaller window.** Nothing can detect this —
    the gateway advertises no context length for any model — so this is the one knob
    that has to be set by hand for a 32k deployment.

    Cutting inside a chunk is what this replaces: a fixed 1200-char cut made 37% of
    the corpus searchable but never deliverable, and a term at offset 2312 was
    invisible even though search had ranked its chunk first."""
    expand_window: int = Field(default=1, ge=0)
    """Default neighbor window for ``expand_context``."""


class ImagesConfig(BaseModel):
    """How PDF figures are handled at ingest and query time.

    ``none``   — figures dropped (default; today's behavior).
    ``describe``— at ingest, render each figure and have ``vision_model`` write a
                 description, stored as a searchable/citable figure chunk (works
                 with any chat model).
    ``attach`` — same ingest as describe, plus at query time the figure pixels are
                 fed to a vision-capable chat model.
    Figure images are persisted (and rendered as UI thumbnails when cited) in both
    ``describe`` and ``attach``. Switching ``none``→``describe``/``attach`` needs a
    re-ingest; ``describe``↔``attach`` does not.
    """

    mode: Literal["none", "describe", "attach"] = "none"
    vision_model: str = "gemma-4-31b"
    """Vision-language model (via the gateway) used to describe figures, and the
    expected chat model for ``attach``."""
    images_scale: float = Field(default=2.0, gt=0)
    """Docling figure render scale (2.0 = higher-res crops)."""
    describe_prompt: str = (
        "Beschreibe diese Abbildung aus einem wissenschaftlichen Dokument sachlich "
        "und detailliert auf Deutsch. Nenne die dargestellten Objekte, "
        "Beschriftungen, Achsen und Verläufe sowie die Kernaussage. Gib "
        "ausschließlich die Beschreibung aus, ohne Einleitung."
    )
    inline_figures: bool = True
    """Render a marked figure as a markdown image directly ABOVE the paragraph that
    describes it. ``build_context`` advertises a per-figure marker token, the model
    copies it on its own line, and the answer post-processing swaps it for
    ``![](/sources/figure/...)``. Every retrieved figure the model did NOT mark keeps
    the legacy behavior (a ``cl.Image`` thumbnail below the answer) — never both.
    Inert when ``mode == "none"``."""
    figure_marker_prompt: str = (
        "Zu jeder Abbildung im Kontext steht eine Zeile "
        '"Abbildungs-Marker: {{ABB:...}}". Wenn du eine Abbildung beschreibst, '
        "setze deren Marker unverändert in eine eigene Zeile direkt VOR den Absatz, "
        "der die Abbildung beschreibt. Kopiere ihn exakt inklusive der geschweiften "
        "Klammern (hier ausdrücklich erlaubt, abweichend von der Klammer-Regel für "
        "Zitate). Verwende jeden Marker höchstens einmal, erfinde keine Marker und "
        "schreibe niemals selbst Bild-Links oder Markdown-Bilder."
    )
    """Ephemeral per-request system message teaching the marker protocol. Used
    VERBATIM — never ``.format()`` it, that would collapse ``{{`` to ``{``."""
    inline_figure_caption: bool = False
    """Also emit the figure caption as an italic line under the inlined image."""
    show_unmarked_figures: bool = True
    """Whether retrieved figures the answer does NOT reference are still shown as
    thumbnails below the message. ``false`` = only figures the model actually
    marked appear (cleaner, but nothing is shown if it forgets the marker).
    With ``inline_figures: false`` every figure counts as unmarked, so ``false``
    here hides figure thumbnails entirely (descriptions stay searchable)."""
    max_attach_images: int = Field(default=3, ge=1)
    """``attach`` only: cap on figure images fed to the vision call per answer."""
    attach_image_max_px: int = Field(default=768, ge=64)
    """``attach`` only: downscale each figure so its longest side ≤ this (JPEG)
    before sending to the vision model (keeps the request under the gateway size
    limit)."""
    describe_image_max_px: int = Field(default=1536, ge=64)
    """Ingest only: same treatment for the ``describe`` call, which used to send
    raw full-res PNG and lost oversized figures to HTTP 413. Larger than
    ``attach_image_max_px`` on purpose: describing a dense chart benefits from
    detail the answer-time path does not need, and most of the size reduction
    comes from JPEG rather than from downscaling."""
    figure_store_dir: str | None = None
    """Where figure PNGs are saved. ``None`` → ``<sources.data_dir>/figures``."""
    vision_capable_models: list[str] = Field(default_factory=lambda: ["gemma-4-31b"])
    """Chat models allowed to receive figure pixels in ``attach`` mode (the gateway
    exposes no per-model capability flag, so this list is authoritative)."""

    @model_validator(mode="after")
    def _check(self) -> "ImagesConfig":
        if self.mode == "attach" and not self.vision_capable_models:
            raise ValueError(
                "images.mode='attach' requires images.vision_capable_models to be "
                "non-empty."
            )
        return self


class ProfileConfig(BaseModel):
    """An optional persona/role that scopes prompt and retrieval."""

    id: str
    name: str
    icon: str | None = None
    description: str | None = None
    markdown_description: str | None = None
    prompt_context: str = ""
    retrieval_filters: dict[str, Any] = Field(default_factory=dict)
    """Metadata field -> value(s) to scope retrieval for this profile."""


class AppConfig(BaseModel):
    """Runtime/behavioral knobs (streaming, personalization)."""

    streaming_enabled: bool = False
    streaming_double_pass: bool = False
    personalization_enabled: bool = True
    profile_min_messages: int = Field(default=5, ge=1)
    profile_topic_limit: int = Field(default=8, ge=0)
    profile_relevance_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    personalized_followups_count: int = Field(default=2, ge=0)
    show_settings: bool = True
    """Whether the Chainlit settings panel is shown (even without profiles)."""


class UiTextConfig(BaseModel):
    """Neutral UI/prompt strings (overridable per language)."""

    retry_tool: str = "Call the {tool} tool first before answering."
    forced_final: str = (
        "Answer the question using only the retrieved context above. "
        "If the answer is not in the context, say so."
    )


class EvaluationConfig(BaseModel):
    """Automatic answer-quality scoring (off by default).

    Scoring does NOT run in this process. It runs in the separate ``eval_app``
    service, which owns the metric library and the eval database; this app only
    POSTs ``(question, answer, contexts)`` after an answer is sent and renders the
    numbers that come back. Two reasons: the metric library drags in a dependency
    chain that has no business in the Chainlit image, and a judge call must never
    sit between the user and their answer.

    The two live metrics are *reference-free* — they need no ground-truth answer, so they
    work on real chats — but each costs a judge call, and ``relevance``
    additionally costs one embedding call. That is why this is opt-in.

    Treat the numbers as deltas, not absolutes: a faithfulness of 0.87 means
    little on its own, while 0.87 → 0.71 after a chunking change means something.
    """

    enabled: bool = False
    """Master switch. While false, nothing is imported, posted or rendered, and
    the app behaves exactly as it does without this section."""
    metrics: list[Literal["faithfulness", "relevance"]] = Field(
        default_factory=lambda: ["faithfulness", "relevance"]
    )
    """Which scores to compute. ``faithfulness`` = are the answer's claims actually
    supported by the retrieved chunks (catches hallucination); ``relevance`` = does
    the answer address the question that was asked."""
    judge_model: str | None = None
    """Model that grades the answer. ``None`` → ``models.chat_model``. Judging a
    model with itself inflates the scores, so prefer naming a different model here
    if anyone is going to read the absolute values."""
    show_badge: bool = True
    """Show the running conversation score in a badge above the chatbox. ``false``
    still records everything for the dashboard, which is what you want when you would
    rather not put a number in front of workshop participants."""
    gold_min_faithfulness: float | None = 0.9
    """An answer scoring at least this on faithfulness (and clearing
    ``gold_min_relevance``) makes the badge offer to save the conversation as a gold
    reference — a yellow ``!`` appears on the badge, the save action sits in its
    panel. ``null`` turns the suggestion off entirely."""
    gold_min_relevance: float | None = 0.8
    """The relevance half of the gold suggestion threshold. Set lower than
    faithfulness by default because relevance is the noisier judge metric."""
    service_url: str = "http://eval:8001"
    """Base URL of the ``eval_app`` service. The default resolves on the compose
    network; running locally with ``uv run`` it is ``http://localhost:8001``."""

    @model_validator(mode="after")
    def _check(self) -> "EvaluationConfig":
        if self.enabled and not self.metrics:
            raise ValueError(
                "evaluation.enabled is true but evaluation.metrics is empty, so "
                "nothing would be scored. List at least one metric, or set "
                "evaluation.enabled: false."
            )
        return self


class RagConfig(BaseModel):
    """Top-level configuration for a RAG instance."""

    name: str = "rag"
    language: str = "en"
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    data_sources: list[DataSourceConfig] = Field(default_factory=list)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    citation: CitationConfig = Field(default_factory=CitationConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    tool: ToolConfig = Field(default_factory=ToolConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    profiles: list[ProfileConfig] = Field(default_factory=list)
    profiles_path: str | None = None
    """Optional path to a JSON profiles file (used when ``profiles`` is empty)."""
    default_profile: str | None = None
    app: AppConfig = Field(default_factory=AppConfig)
    ui_text: UiTextConfig = Field(default_factory=UiTextConfig)

    _config_dir: Path | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _check_custom_parsers(self) -> "RagConfig":
        # Deferred import to avoid a cycle at module import time.
        try:
            from kb.parsers import PARSER_REGISTRY
        except Exception:
            return self
        for src in self.data_sources:
            if src.format == "custom" and src.parser_name not in PARSER_REGISTRY:
                raise ValueError(
                    f"data source '{src.name}': unknown custom parser "
                    f"'{src.parser_name}'. Registered: {sorted(PARSER_REGISTRY)}"
                )
        return self

    @model_validator(mode="after")
    def _check_enabled_tools(self) -> "RagConfig":
        # Deferred import to avoid a cycle at module import time.
        try:
            from tools import TOOL_REGISTRY
        except Exception:
            return self
        for tool_id in self.tools.enabled:
            if tool_id not in TOOL_REGISTRY:
                raise ValueError(
                    f"tools.enabled: unknown tool '{tool_id}'. "
                    f"Registered: {sorted(TOOL_REGISTRY)}"
                )
        return self

    def resolve_path(self, value: str | Path) -> Path:
        """Resolve a config-relative path against the config file's directory."""
        p = Path(value)
        if p.is_absolute():
            return p
        base = self._config_dir or Path.cwd()
        return (base / p).resolve()
