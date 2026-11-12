from __future__ import annotations

from typing import Any

import litellm

from settings import (
    CHAT_MODEL,
    EMBED_MODEL,
    FALLBACK_CHAT_MODEL,
    LITELLM_API_KEY,
    LITELLM_BASE_URL,
)

# litellm prints a five-line "Give Feedback / Get Help ... _turn_on_debug()" block for
# every single failed call. During an ingest that means hundreds of them, which buries
# the messages that actually say which document and which figure went wrong. A reported
# failure log was roughly 90% this text.
litellm.suppress_debug_info = True

# Figure descriptions run once per figure during a batch ingest, where a transient
# gateway error (typically a rate limit) used to be swallowed and the figure stored
# with an empty description forever. Retrying is always the right answer here, so
# this is a constant rather than a config knob. litellm handles the backoff.
_DESCRIBE_RETRIES = 3

# Embeddings need this even more than figure descriptions do. A failed description
# costs one figure and is caught; a failed embedding used to abort the entire ingest,
# so a single dropped connection threw away all the work, including figures already
# paid for. Reported from the field on a flaky network.
_EMBED_RETRIES = 3

# Texts per embedding request. The ingest already sends small batches, but the
# semantic chunker embeds every sentence of a section in one call, and a long
# section has hundreds. Ollama rejects such a request outright (measured: 129
# texts pass, 150 fail with "connection reset by peer" on its tokenize step), so
# the whole ingest died on the first long section. Splitting here covers every
# caller and costs a hosted gateway nothing but a few more round trips.
_EMBED_BATCH = 64


def _slices(texts: list[str]) -> list[list[str]]:
    return [texts[i : i + _EMBED_BATCH] for i in range(0, len(texts), _EMBED_BATCH)]


def _vectors(response) -> list[list[float]]:
    return [item["embedding"] for item in sorted(response.data, key=lambda item: item["index"])]


def _client_args(model: str | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if LITELLM_BASE_URL:
        args["api_base"] = LITELLM_BASE_URL
    if LITELLM_API_KEY:
        args["api_key"] = LITELLM_API_KEY
    # Bare gateway model names (no "provider/" prefix) need an explicit provider
    # for the litellm SDK, which otherwise raises "LLM Provider NOT provided".
    # This gateway is OpenAI-compatible. Models that already carry a prefix
    # (e.g. "anthropic/…") keep their own provider.
    if model and "/" not in model:
        args["custom_llm_provider"] = "openai"
    return args


def _fallbacks(model: str) -> list[str]:
    """``models.fallback_chat_model`` in the shape litellm expects, or nothing.

    Skipped when it *is* the model being called, so a request already targeting
    the fallback does not list itself as its own backup.

    Both models live on the same OpenAI-compatible gateway, so the ``api_base``,
    ``api_key`` and ``custom_llm_provider`` already in the payload apply to the
    retry too — which is why this is a plain model list rather than a second
    client configuration.
    """
    if FALLBACK_CHAT_MODEL and FALLBACK_CHAT_MODEL != model:
        return [FALLBACK_CHAT_MODEL]
    return []


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = "auto",
    model: str | None = None,
):
    resolved = model or CHAT_MODEL
    payload: dict[str, Any] = {
        "model": resolved,
        "messages": messages,
        **_client_args(resolved),
    }
    if fallbacks := _fallbacks(resolved):
        payload["fallbacks"] = fallbacks
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
    return await litellm.acompletion(**payload)


async def stream_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, tool_choice: str | None = None):
    payload: dict[str, Any] = {
        "model": CHAT_MODEL,
        "messages": messages,
        "stream": True,
        **_client_args(CHAT_MODEL),
    }
    if fallbacks := _fallbacks(CHAT_MODEL):
        payload["fallbacks"] = fallbacks
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
    return await litellm.acompletion(**payload)


async def embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for part in _slices(texts):
        response = await litellm.aembedding(
            model=EMBED_MODEL,
            input=part,
            encoding_format="float",
            num_retries=_EMBED_RETRIES,
            **_client_args(EMBED_MODEL),
        )
        vectors.extend(_vectors(response))
    return vectors


_MODEL_LIST_CACHE: list[str] | None = None
# Meta/alias ids the gateway advertises that are not directly callable as models.
_META_MODEL_IDS = {"all-team-models"}


def list_chat_models() -> list[str]:
    """Best-effort list of model ids the gateway advertises via ``/v1/models``.

    Blocking network call — warm it once at startup, then read the result via
    :func:`cached_chat_models`. Only a SUCCESSFUL response is cached (an empty
    result from a gateway that only exposes a meta alias counts as success); a
    transient failure (timeout, network error) is NOT cached, so it can be
    retried rather than poisoning the cache for the process lifetime."""
    global _MODEL_LIST_CACHE
    if _MODEL_LIST_CACHE is not None:
        return _MODEL_LIST_CACHE
    try:
        import json
        import urllib.request

        base = (LITELLM_BASE_URL or "").rstrip("/")
        req = urllib.request.Request(f"{base}/v1/models")
        if LITELLM_API_KEY:
            req.add_header("Authorization", f"Bearer {LITELLM_API_KEY}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [
            m["id"]
            for m in data.get("data", [])
            if isinstance(m, dict) and m.get("id") and m["id"] not in _META_MODEL_IDS
        ]
    except Exception:  # noqa: BLE001 — transient: return empty WITHOUT caching
        return []
    _MODEL_LIST_CACHE = ids
    return ids


def cached_chat_models() -> list[str]:
    """Return the warmed model list without triggering a network call (safe to
    call from async handlers). Empty until :func:`list_chat_models` has run."""
    return _MODEL_LIST_CACHE or []


def describe_image_sync(image_data_uri: str, prompt: str, model: str) -> str:
    """Blocking single-image vision completion via the gateway.

    Used by the PDF parser's figure step, which runs inside ``plan_ingest`` (a
    synchronous stage executing inside ``ingest_all``'s event loop), so the async
    ``chat`` would raise "cannot be called from a running event loop". Blocking is
    fine for the batch ingest CLI. Mirrors :func:`embed_sync`."""
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_uri}},
                ],
            }
        ],
        num_retries=_DESCRIBE_RETRIES,
        **_client_args(model),
    )
    return (response.choices[0].message.content or "").strip()


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Blocking embedding call.

    Used by the semantic chunker, which runs inside ``plan_ingest`` — a
    synchronous stage that itself executes inside ``ingest_all``'s event loop,
    so the async ``embed`` (via ``asyncio.run``) would raise "cannot be called
    from a running event loop". A blocking call is fine for the batch CLI.
    """
    vectors: list[list[float]] = []
    for part in _slices(texts):
        response = litellm.embedding(
            model=EMBED_MODEL,
            input=part,
            encoding_format="float",
            num_retries=_EMBED_RETRIES,
            **_client_args(EMBED_MODEL),
        )
        vectors.extend(_vectors(response))
    return vectors


def message_to_dict(message: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return data
