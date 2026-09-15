"""Load a :class:`~config.schema.RagConfig` from YAML + environment overrides.

Precedence (highest first): explicit environment variable → YAML value →
pydantic default. Keeping the legacy env-var names means existing ``.env``
files and ``docker-compose.yml`` ``environment:`` blocks keep working, and
secrets/infra never need to appear in the YAML.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from config.schema import RagConfig

# apps/chainlit/ — used to resolve the default config path and load .env.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load apps/chainlit/.env so RAG_CONFIG and the override vars below are visible.
load_dotenv(BASE_DIR / ".env", override=False)

CONFIG_PATH_ENV = "RAG_CONFIG"
DEFAULT_CONFIG = BASE_DIR / "config" / "default.yaml"

# env var -> dotted path inside RagConfig. Intentionally small: secrets, infra,
# and the handful of knobs docker-compose already sets.
_ENV_OVERRIDES: dict[str, str] = {
    "LITELLM_BASE_URL": "models.litellm_base_url",
    "LITELLM_API_KEY": "models.litellm_api_key",
    "CHAT_MODEL": "models.chat_model",
    "FALLBACK_CHAT_MODEL": "models.fallback_chat_model",
    "EMBED_MODEL": "models.embed_model",
    "QDRANT_URL": "vector_store.url",
    "QDRANT_API_KEY": "vector_store.api_key",
    "QDRANT_COLLECTION": "vector_store.collection",
    "TOP_K": "retrieval.top_k",
    "MAX_TOP_K": "retrieval.max_top_k",
    "MAX_SOURCE_LINKS": "retrieval.max_source_links",
    "SCORE_THRESHOLD": "retrieval.score_threshold",
    "CHUNK_MAX_CHARS": "chunking.max_chars",
    "CHUNK_OVERLAP": "chunking.overlap",
    "STREAMING_ENABLED": "app.streaming_enabled",
    "STREAMING_DOUBLE_PASS": "app.streaming_double_pass",
    "PERSONALIZATION_ENABLED": "app.personalization_enabled",
    "PROFILE_MIN_MESSAGES": "app.profile_min_messages",
    "PROFILE_TOPIC_LIMIT": "app.profile_topic_limit",
    "PROFILE_RELEVANCE_THRESHOLD": "app.profile_relevance_threshold",
    "PERSONALIZED_FOLLOWUPS_COUNT": "app.personalized_followups_count",
    "SYSTEM_PROMPT_PATH": "prompt.system_prompt_path",
    "CITATION_MAP_PATH": "citation.map_path",
    "SOURCE_PDF_FALLBACK": "citation.source_pdf_fallback",
    "DATA_RAW_DIR": "sources.data_dir",
    "IMAGES_MODE": "images.mode",
    "IMAGES_VISION_MODEL": "images.vision_model",
    # Both differ per environment rather than per instance, which is what this map
    # is for: enabled is how you switch scoring on for one run without editing a
    # committed config, and service_url is "eval" under compose but "localhost"
    # when the app runs on the host.
    "EVALUATION_ENABLED": "evaluation.enabled",
    "EVALUATION_SERVICE_URL": "evaluation.service_url",
    # Worth an override of its own: the judge should not be the model being judged,
    # and when one model on the gateway is unavailable you want to repoint the judge
    # without touching a committed config.
    "EVALUATION_JUDGE_MODEL": "evaluation.judge_model",
}

# List-valued env vars split on this separator (matches legacy _getenv_list).
_ENV_LIST_OVERRIDES: dict[str, str] = {
    "STARTER_QUESTIONS": "prompt.starter_questions",
    "RAG_TOOLS_ENABLED": "tools.enabled",
}
_LIST_SEP = "||"


def _set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    node = target
    for key in keys[:-1]:
        existing = node.get(key)
        if not isinstance(existing, dict):
            existing = {}
            node[key] = existing
        node = existing
    node[keys[-1]] = value


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    for env_name, dotted in _ENV_OVERRIDES.items():
        val = os.getenv(env_name)
        if val is not None and val != "":
            _set_dotted(raw, dotted, val)
    for env_name, dotted in _ENV_LIST_OVERRIDES.items():
        val = os.getenv(env_name)
        if val is not None and val.strip():
            items = [item.strip() for item in val.split(_LIST_SEP) if item.strip()]
            _set_dotted(raw, dotted, items)
    _apply_source_env_overrides(raw)
    return raw


def _apply_source_env_overrides(raw: dict[str, Any]) -> None:
    """Point PDF sources with a Docling JSON folder at a container path.

    ``INGEST_DOCLING_JSON_DIR`` only rewrites sources that already declare a
    ``docling_json_dir`` — it never turns a live-conversion source into a
    JSON one. This lets the Docker ingest job use ``/data/...`` without a
    separate YAML.
    """
    docling_dir = os.getenv("INGEST_DOCLING_JSON_DIR")
    if not docling_dir:
        return
    for src in raw.get("data_sources") or []:
        if not isinstance(src, dict) or src.get("format") != "pdf":
            continue
        opts = src.get("pdf_options")
        if isinstance(opts, dict) and opts.get("docling_json_dir"):
            opts["docling_json_dir"] = docling_dir


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Which config file a load would read, absolute.

    Split out of :func:`load_config` so nothing has to re-derive it: anything
    that reports the active config (``/config-info``, diagnostics) resolves it
    the same way the loader does, including the ``BASE_DIR`` join and the
    empty-``RAG_CONFIG``-means-default behaviour.
    """
    if path is None:
        path = os.getenv(CONFIG_PATH_ENV) or DEFAULT_CONFIG
    path = Path(path)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


def load_config(path: str | Path | None = None) -> RagConfig:
    """Load and validate a config. Uncached — use for tests / explicit paths."""
    path = resolve_config_path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"RAG config not found: {path}. Set the {CONFIG_PATH_ENV} env var "
            f"to a valid YAML config, or create {DEFAULT_CONFIG}."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"RAG config {path} must be a YAML mapping, got {type(raw).__name__}.")
    raw = _apply_env_overrides(raw)
    cfg = RagConfig.model_validate(raw)
    cfg._config_dir = path.resolve().parent
    return cfg


@lru_cache(maxsize=1)
def get_config() -> RagConfig:
    """Return the process-wide config singleton (loaded from ``RAG_CONFIG``)."""
    return load_config()
