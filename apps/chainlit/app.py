from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import asyncpg
import httpx
import bcrypt
import chainlit as cl
from chainlit.auth import get_current_user
from chainlit.input_widget import Select, Switch, Tags, TextInput
from chainlit.types import Starter
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from chat_history import (
    add_chat_message,
    create_chat_session,
    export_all_sessions_openai_jsonl,
    export_session_openai_json,
    get_chat_session,
    get_session_messages,
    get_user_message_count,
    get_user_selected_chat_model,
    get_user_selected_chat_profile,
    init_chat_db,
    migrate_legacy_db,
    list_chat_sessions,
    set_session_title_if_missing,
    set_user_selected_chat_model,
    set_user_selected_chat_profile,
    update_chat_session_metadata,
    upsert_user_profile,
)
from evaluation import (
    conversation_turns,
    gold_suggested,
    post_feedback,
    post_gold,
    post_score,
    trend_sign,
)
from llm import cached_chat_models, chat, list_chat_models, message_to_dict
from tools import ToolContext, build_openai_tools
from native_chat import (
    check_user_exists,
    create_user,
    ensure_native_schema,
    export_all_chats_zip,
    export_feedback_csv,
    get_user_by_identifier,
    upsert_feedback,
)
from config import get_config
from rag_tool import context_with_source, extract_page, extract_source_file, render_context, retrieve
from figure_markers import (
    build_figure_candidates,
    figure_display_name,
    figure_url,
    normalize_figure_markers,
    render_figure_markers,
    sanitize_for_model,
    strip_figure_markers,
)
from settings import (
    CHAT_DB_PATH,
    CHAT_EXPORT_DIR,
    CHAINLIT_AUTH_PASSWORD,
    CHAINLIT_AUTH_USERNAME,
    CHAINLIT_INIT_DB,
    CHAT_MODEL,
    DATA_RAW_DIR,
    DATABASE_URL,
    DOCUMENT_WATCH,
    LEGACY_CHAT_DB_PATH,
    EMBED_MODEL,
    MAX_TOP_K,
    MAX_SOURCE_LINKS,
    PERSONALIZED_FOLLOWUPS_COUNT,
    PROFILE_MIN_MESSAGES,
    starter_questions,
    SYSTEM_PROMPT_PATH,
    TOP_K,
)
from user_profile import (
    _kw_key,
    load_user_profile,
    regenerate_keywords,
    update_keyword_embeddings,
    update_user_profile,
    UserProfile,
)


def _load_system_prompt(path: Path) -> str | None:
    if path.is_file():
        content = path.read_text(encoding="utf-8").strip()
        return content or None
    return None


SYSTEM_PROMPT = _load_system_prompt(SYSTEM_PROMPT_PATH)
CITATION_PANEL_CACHE: dict[str, str] = {}
CITATION_SIDEBAR_TITLE = get_config().citation.panel_title
CITATION_HISTORY_SIDEBAR_TITLE = get_config().citation.labels.get(
    "history_panel_title", f"{CITATION_SIDEBAR_TITLE} (Verlauf)"
)


def _served_suffixes() -> set[str]:
    """Extensions the /sources routes will serve, from ``sources.served_extensions``.

    Normalised, because a config may write ``pdf``, ``.PDF`` or ``.pdf``.
    """
    return {f".{e.lstrip('.').lower()}" for e in get_config().sources.served_extensions}


def _served_roots() -> list[Path]:
    """Every directory the ``/sources`` routes may serve a file from.

    ``sources.data_dir`` first, then each ``data_sources[].path``. A citation names
    a bare file (parsers store ``source_file`` as ``path.name``), so a corpus
    assembled from several folders had only its ``data_dir`` files resolvable and
    every citation into another folder stayed plain text.

    Derived from ``data_sources`` rather than a second list in ``sources`` on
    purpose: the paths are already declared once, and a config that repeats them
    is a config where the two copies drift.

    ``data_dir`` stays first so it wins a basename collision, which is what a
    single-folder instance did before. Collisions are already a known state on the
    ingest side, where a second ``intro.pdf`` counts as the same document and the
    run warns about it.
    """
    cfg = get_config()
    roots = [DATA_RAW_DIR]
    for source in cfg.data_sources:
        path = cfg.resolve_path(source.path)
        # `path` may name a single file; the folder holding it is what we serve.
        roots.append(path if path.is_dir() else path.parent)

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _resolve_source_pdf_path(file_name: str) -> Path | None:
    """Resolve a cited basename to a servable file, or ``None``.

    Checked in this order, all three load-bearing: the name must be a bare
    basename (no traversal, no subpath), its extension must be in
    ``served_extensions``, and the resolved file must stay inside the root it was
    found under. The suffix gate runs before any filesystem access, so a request
    for a disallowed type costs nothing.
    """
    if not file_name or file_name != Path(file_name).name:
        return None
    if Path(file_name).suffix.lower() not in _served_suffixes():
        return None

    roots = _served_roots()
    for root in roots:
        found = _inside(root / file_name, root)
        if found:
            return found

    # Not at the top of any root. A source indexed with a recursive glob
    # (`**/*.pdf`, which the SMB example uses because a department share is
    # folders all the way down) stores the bare file name like every other
    # parser, so the chunk is searchable and the citation had nowhere to point:
    # it rendered as plain text with a citation_unlinkable warning.
    #
    # Only reached when the direct lookup missed, and `rglob` with a literal
    # name scans directory entries rather than stat-ing each one: 4 ms over 5000
    # files in 40 folders, measured. Not worth a cache to invalidate.
    for root in roots:
        for candidate in root.rglob(file_name):
            found = _inside(candidate, root)
            if found:
                return found
    return None


def _inside(candidate: Path, root: Path) -> Path | None:
    """``candidate`` resolved, if it is a real file that stays inside ``root``.

    The containment check runs after resolving, so a symlink under the root
    pointing outside it cannot serve a file the root does not contain.
    """
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def _source_pdf_url(file_name: str) -> str:
    return f"/sources/pdf/{quote(file_name, safe='')}"


def _source_figure_url(file_name: str) -> str:
    # Delegates so the route prefix has a single definition (figure_markers also
    # emits and strips these URLs).
    return figure_url(file_name)


def _citation_panel_url(step_id: str) -> str:
    return f"/sources/citations/{quote(step_id, safe='')}"


async def _load_citation_panel_content(step_id: str) -> str | None:
    if not isinstance(step_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", step_id):
        return None
    cached = CITATION_PANEL_CACHE.get(step_id)
    if isinstance(cached, str) and cached.strip():
        return cached

    if not DATABASE_URL:
        return None

    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        row = await conn.fetchrow(
            'SELECT metadata FROM "Step" WHERE id = $1::uuid',
            step_id,
        )
        if row is None:
            return None
        raw_metadata = row.get("metadata")
        if not isinstance(raw_metadata, str):
            return None
        metadata = json.loads(raw_metadata)
        if not isinstance(metadata, dict):
            return None
        panel_content = metadata.get("citation_panel_content")
        if isinstance(panel_content, str) and panel_content.strip():
            _cache_citation_panel_content(step_id, panel_content)
            return panel_content
        return None
    except Exception:
        return None
    finally:
        if conn is not None:
            await conn.close()


def _cache_citation_panel_content(step_id: str, panel_content: str, *, max_items: int = 512) -> None:
    if not isinstance(step_id, str) or not step_id.strip():
        return
    if not isinstance(panel_content, str) or not panel_content.strip():
        return
    CITATION_PANEL_CACHE[step_id] = panel_content
    while len(CITATION_PANEL_CACHE) > max_items:
        oldest_key = next(iter(CITATION_PANEL_CACHE))
        CITATION_PANEL_CACHE.pop(oldest_key, None)


_CATCH_ALL_SUFFIX = "/{full_path:path}"


def _contains_catch_all(route: Any, _depth: int = 0, _seen: set[int] | None = None) -> bool:
    """True if ``route`` IS Chainlit's SPA catch-all, or nests it.

    FastAPI up to ~0.136 flattened ``include_router`` into ``app.router.routes``, so the
    catch-all was findable by path. Newer versions insert a single opaque
    ``_IncludedRouter`` whose children hang off ``original_router.routes`` — so matching
    on path alone silently stops finding it. That regression is what shadowed every
    custom GET route (citations, figures, exports) behind the SPA. Handle both layouts."""
    path = getattr(route, "path", None)
    if isinstance(path, str) and path.endswith(_CATCH_ALL_SUFFIX):
        return True
    if _depth >= 5:
        return False
    seen = _seen if _seen is not None else set()
    if id(route) in seen:
        return False
    seen.add(id(route))
    for holder in (getattr(route, "original_router", None), getattr(route, "router", None)):
        nested = getattr(holder, "routes", None)
        if isinstance(nested, list) and any(
            _contains_catch_all(child, _depth + 1, seen) for child in nested
        ):
            return True
    return False


def _find_idx(routes: list[Any], route_path: str) -> int | None:
    return next((i for i, r in enumerate(routes) if getattr(r, "path", None) == route_path), None)


def _ensure_route_precedes_catch_all(fastapi_app: Any, route_path: str) -> None:
    """Move ``route_path`` ahead of the SPA catch-all so it is not swallowed by it.

    Starlette matches in list order (``fastapi.routing.Router.app`` iterates
    ``self.routes``), so a catch-all sitting before our routes wins and the caller gets
    the SPA's HTML instead of their PDF/CSV. Every bail-out warns on purpose: this
    function silently became a no-op on a FastAPI upgrade and nobody noticed."""
    routes = getattr(getattr(fastapi_app, "router", None), "routes", None)
    if not isinstance(routes, list):
        print(f"[WARN] route_order: app exposes no route list; cannot protect {route_path}")
        return

    route_idx = _find_idx(routes, route_path)
    if route_idx is None:
        print(f"[WARN] route_order: {route_path} is not registered; nothing to reorder")
        return

    catch_all_idx = next((i for i, r in enumerate(routes) if _contains_catch_all(r)), None)
    if catch_all_idx is None:
        print(
            f"[WARN] route_order: no catch-all route found, so {route_path} cannot be "
            "protected — did the FastAPI route layout change again?"
        )
        return

    if route_idx < catch_all_idx:
        return

    routes.insert(catch_all_idx, routes.pop(route_idx))

    # Confirm it actually took effect rather than trusting the mutation.
    new_route_idx = _find_idx(routes, route_path)
    new_catch_all_idx = next((i for i, r in enumerate(routes) if _contains_catch_all(r)), None)
    if new_route_idx is None or new_catch_all_idx is None or new_route_idx > new_catch_all_idx:
        print(f"[WARN] route_order: reordering {route_path} did not take effect")

# Chat profiles configuration
def _load_chat_profiles() -> dict[str, Any]:
    """Load chat profiles: config-defined profiles win; otherwise the JSON file.

    Profiles are optional and domain-neutral — a profile scopes retrieval via
    generic ``retrieval_filters`` (metadata field -> value(s))."""
    cfg = get_config()
    if cfg.profiles:
        return {
            "profiles": [
                {
                    "id": p.id,
                    "name": p.name,
                    "icon": p.icon,
                    "description": p.description,
                    "markdown_description": p.markdown_description,
                    "prompt_context": p.prompt_context,
                    "retrieval_filters": p.retrieval_filters,
                }
                for p in cfg.profiles
            ],
            "default_profile": cfg.default_profile,
        }
    # A config may point at a JSON profiles file instead of inline profiles.
    profiles_file = cfg.resolve_path(cfg.profiles_path) if cfg.profiles_path else None
    if profiles_file and profiles_file.is_file():
        try:
            return json.loads(profiles_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] Failed to load profiles file {profiles_file}: {e}")
    return {"profiles": [], "default_profile": None}


CHAT_PROFILES_CONFIG = _load_chat_profiles()


def _get_profile_by_name(profile_name: str) -> dict[str, Any] | None:
    """Get a profile configuration by its name."""
    for profile in CHAT_PROFILES_CONFIG.get("profiles", []):
        if profile.get("name") == profile_name:
            return profile
    return None


def _active_retrieval_filters() -> dict[str, Any]:
    """Generic metadata filters for the active chat profile (empty if none)."""
    profile = cl.user_session.get("chat_profile_config") or {}
    filters = profile.get("retrieval_filters") if isinstance(profile, dict) else None
    return filters if isinstance(filters, dict) else {}


# Build the OpenAI tool schemas + the {function_name -> Tool} router from the
# enabled tools in the config (tools/ package registry). Search-only by default.
TOOLS, TOOL_BY_FUNCTION_NAME = build_openai_tools(get_config())
# The search tool's name, still used by the "call the tool first" retry nudge.
TOOL_NAME: str = get_config().tool.name

# Strong references to in-flight answer-scoring tasks. asyncio holds only a weak one,
# so without this they are collectable and simply never run — the same trap the
# document watcher documents at its own create_task.
_SCORING_TASKS: set[asyncio.Task] = set()
# Threads with a score in flight, so /eval-status can say "working on it" instead of
# leaving the badge silent for the ~16s a judge takes. Measured: that cost is gateway
# round-trip per structured-output call, not model size — ministral-3-14b, gemma-4-31b
# and llama-3-3-70b all land within a second of each other — so it is a wait to
# explain rather than one to optimise away.
_SCORING_THREADS: set[str] = set()


def _forget_scoring_task(task: asyncio.Task) -> None:
    _SCORING_TASKS.discard(task)
    # A detached task swallows its exception unless somebody asks for it, and scoring
    # that fails in silence is exactly how a dropped task went unnoticed once already.
    if not task.cancelled() and task.exception() is not None:
        print(f"[WARN] evaluation_scoring_failed: {task.exception()!r}")


def _make_public_assets_revalidate() -> None:
    """Make browsers re-check ``/public/`` assets instead of guessing.

    Chainlit serves that directory with ``ETag`` and ``Last-Modified`` but no
    ``Cache-Control``. With no directive a browser falls back to *heuristic* caching
    and may reuse a file without ever asking, so editing ``custom.css`` or one of the
    badge scripts can silently do nothing until somebody thinks to hard-reload. In a
    template whose whole point is that people customise those files, that is a trap —
    and it cost a full round of "your fix changed nothing" here.

    ``no-cache`` does not mean "do not store", it means "revalidate before reuse":
    the browser keeps the file and gets a small 304 when nothing changed. The cost is
    one conditional request per asset per load; the gain is that an edit always lands.

    Registered at import time on purpose. Starlette refuses ``add_middleware`` once
    the application has started, so doing this from the startup hook that registers
    our routes raises "Cannot add middleware after an application has started" — it
    is logged and swallowed by Chainlit, which is a silent no-op.
    """
    from chainlit.server import app as _app

    @_app.middleware("http")
    async def _revalidate_public_assets(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/public/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


try:
    _make_public_assets_revalidate()
except Exception as exc:  # noqa: BLE001 — a stale asset is not worth a dead app
    print(f"[WARN] public_asset_revalidation_unavailable: {exc.__class__.__name__}: {exc}")


def _forced_ui_language() -> str | None:
    """The language ``[UI] language`` pins everyone to, or ``None`` to follow the browser.

    Chainlit resolves its own interface strings from ``navigator.language`` unless that
    key is set, and it ships no language picker — so the browser *is* the setting. Our
    two badges are static files under ``/public`` and cannot read ``config.toml``, so
    they ask here and fall back to ``navigator.language`` themselves. That keeps our
    strings agreeing with Chainlit's chrome whichever way the language was decided,
    which a switch of our own could not do.
    """
    from chainlit.config import config as chainlit_config

    return chainlit_config.ui.language or None


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _truncate(text: str, max_len: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def _build_personalization_prompt(user_profile: UserProfile) -> str:
    """Build personalization context for the system prompt.

    Keywords are used only for the 'Bezug zu Ihren Interessen' section —
    they do NOT influence retrieval or chunk filtering.
    """
    if not get_config().app.personalization_enabled:
        return ""
    if not user_profile or not user_profile.personalization_enabled:
        return ""

    active_kws = user_profile.active_keyword_values()
    if not active_kws:
        return ""

    topics_str = ", ".join(active_kws)
    personalized_followups = PERSONALIZED_FOLLOWUPS_COUNT

    return f"""## PERSONALISIERTER KONTEXT
Der Nutzer hat sich häufig mit folgenden Themen beschäftigt: {topics_str}

## PERSONALISIERTE ANTWORT-SEKTION
- Füge nach der Hauptantwort eine kurze Sektion hinzu mit dem Header: "**Bezug zu Ihren Interessen:**"
- Beziehe die Antwort kurz auf die bekannten Interessen des Nutzers (max 50 Wörter)
- Diese Sektion soll nur erscheinen, wenn ein sinnvoller Bezug herstellbar ist

## PERSONALISIERTE ANSCHLUSSFRAGEN
- {personalized_followups} der 3 Anschlussfragen sollten sich auf die Nutzerinteressen beziehen
- Beispiel: Wenn der Nutzer sich für Webserver interessiert, könnte eine Anschlussfrage lauten: "Welche speziellen Anforderungen gelten für Webserver in diesem Kontext?"
"""


def _current_chat_session_id() -> str | None:
    value = cl.user_session.get("chat_history_session_id")
    return value if isinstance(value, str) and value.strip() else None


def _empty_source_catalog() -> dict[str, Any]:
    return {"next_id": 1, "key_to_id": {}, "entries": {}}


def _clean_section_title(section_title: str | None) -> str | None:
    if not isinstance(section_title, str):
        return None
    cleaned = re.sub(r"\s+", " ", section_title).strip()
    return cleaned or None


def _source_catalog_key(
    file_name: str,
    page_start: int | None,
    page_end: int | None,
    section_title: str | None,
) -> str:
    payload = {
        "file": file_name.strip().lower(),
        "page_start": page_start if isinstance(page_start, int) else None,
        "page_end": page_end if isinstance(page_end, int) else None,
        "section": (_clean_section_title(section_title) or "").lower(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _sanitize_source_catalog(raw_catalog: Any) -> dict[str, Any]:
    if not isinstance(raw_catalog, dict):
        return _empty_source_catalog()

    key_to_id_raw = raw_catalog.get("key_to_id")
    entries_raw = raw_catalog.get("entries")
    next_id_raw = raw_catalog.get("next_id")

    key_to_id: dict[str, int] = {}
    if isinstance(key_to_id_raw, dict):
        for key, value in key_to_id_raw.items():
            if not isinstance(key, str):
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                key_to_id[key] = parsed

    entries: dict[str, dict[str, Any]] = {}
    if isinstance(entries_raw, dict):
        for source_id_raw, entry_raw in entries_raw.items():
            if not isinstance(source_id_raw, str) or not isinstance(entry_raw, dict):
                continue
            try:
                source_id = int(source_id_raw)
            except (TypeError, ValueError):
                continue
            if source_id <= 0:
                continue
            file_name = entry_raw.get("file")
            if not isinstance(file_name, str) or not file_name.strip():
                continue
            page_start = entry_raw.get("page_start")
            page_end = entry_raw.get("page_end")
            section = _clean_section_title(entry_raw.get("section"))
            normalized_entry: dict[str, Any] = {"file": file_name}
            if isinstance(page_start, int):
                normalized_entry["page_start"] = page_start
            if isinstance(page_end, int):
                normalized_entry["page_end"] = page_end
            if section:
                normalized_entry["section"] = section
            entries[str(source_id)] = normalized_entry

    valid_ids = {int(source_id) for source_id in entries}
    key_to_id = {key: source_id for key, source_id in key_to_id.items() if source_id in valid_ids}

    max_id = max(valid_ids, default=0)
    try:
        next_id = int(next_id_raw)
    except (TypeError, ValueError):
        next_id = 1
    if next_id <= max_id:
        next_id = max_id + 1
    if next_id < 1:
        next_id = 1

    return {"next_id": next_id, "key_to_id": key_to_id, "entries": entries}


def _load_session_source_catalog(session_id: str | None) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id.strip():
        return _empty_source_catalog()
    session = get_chat_session(CHAT_DB_PATH, session_id)
    if not isinstance(session, dict):
        return _empty_source_catalog()
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return _empty_source_catalog()
    return _sanitize_source_catalog(metadata.get("source_catalog"))


def _persist_session_source_catalog(session_id: str | None, catalog: dict[str, Any]) -> None:
    if not isinstance(session_id, str) or not session_id.strip():
        return
    session = get_chat_session(CHAT_DB_PATH, session_id)
    if not isinstance(session, dict):
        return
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["source_catalog"] = _sanitize_source_catalog(catalog)
    update_chat_session_metadata(CHAT_DB_PATH, session_id, metadata)


def _register_source_in_catalog(
    catalog: dict[str, Any],
    *,
    file_name: str,
    page_start: int | None,
    page_end: int | None,
    section_title: str | None,
) -> tuple[int, dict[str, Any], bool]:
    sanitized = _sanitize_source_catalog(catalog)
    if sanitized is not catalog:
        catalog.clear()
        catalog.update(sanitized)

    key_to_id = catalog["key_to_id"]
    entries = catalog["entries"]

    source_key = _source_catalog_key(file_name, page_start, page_end, section_title)
    existing_id = key_to_id.get(source_key)
    normalized_section = _clean_section_title(section_title)

    if isinstance(existing_id, int) and existing_id > 0:
        entry = entries.get(str(existing_id))
        changed = False
        if not isinstance(entry, dict):
            entry = {"file": file_name}
            entries[str(existing_id)] = entry
            changed = True
        if isinstance(page_start, int) and not isinstance(entry.get("page_start"), int):
            entry["page_start"] = page_start
            changed = True
        if isinstance(page_end, int) and not isinstance(entry.get("page_end"), int):
            entry["page_end"] = page_end
            changed = True
        if normalized_section and not isinstance(entry.get("section"), str):
            entry["section"] = normalized_section
            changed = True
        if not isinstance(entry.get("file"), str) or not entry["file"].strip():
            entry["file"] = file_name
            changed = True
        return existing_id, entry, changed

    next_id = 1
    while str(next_id) in entries:
        next_id += 1

    key_to_id[source_key] = next_id
    entry: dict[str, Any] = {"file": file_name}
    if isinstance(page_start, int):
        entry["page_start"] = page_start
    if isinstance(page_end, int):
        entry["page_end"] = page_end
    if normalized_section:
        entry["section"] = normalized_section
    entries[str(next_id)] = entry
    catalog["next_id"] = next_id + 1
    return next_id, entry, True


def _source_ids_from_citation_history(raw_history: Any) -> set[int]:
    ids: set[int] = set()
    for item in _sanitize_citation_history(raw_history):
        rows = item.get("source_rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = row.get("source_id")
            if isinstance(source_id, int) and source_id > 0:
                ids.add(source_id)
    return ids


def _prune_source_catalog(catalog: dict[str, Any], keep_ids: set[int]) -> bool:
    sanitized = _sanitize_source_catalog(catalog)
    changed = sanitized is not catalog
    if sanitized is not catalog:
        catalog.clear()
        catalog.update(sanitized)

    wanted_ids = {source_id for source_id in keep_ids if isinstance(source_id, int) and source_id > 0}
    entries = catalog.get("entries", {})
    key_to_id = catalog.get("key_to_id", {})

    pruned_entries = {
        source_id_str: entry
        for source_id_str, entry in entries.items()
        if isinstance(source_id_str, str)
        and source_id_str.isdigit()
        and int(source_id_str) in wanted_ids
        and isinstance(entry, dict)
    }
    pruned_key_to_id = {
        source_key: source_id
        for source_key, source_id in key_to_id.items()
        if isinstance(source_key, str) and isinstance(source_id, int) and source_id in wanted_ids
    }

    if pruned_entries != entries:
        catalog["entries"] = pruned_entries
        changed = True
    if pruned_key_to_id != key_to_id:
        catalog["key_to_id"] = pruned_key_to_id
        changed = True

    next_id = 1
    while str(next_id) in catalog["entries"]:
        next_id += 1
    if catalog.get("next_id") != next_id:
        catalog["next_id"] = next_id
        changed = True

    return changed


def _format_history_overview(limit: int = 15) -> str:
    sessions = list_chat_sessions(CHAT_DB_PATH, limit=limit)
    if not sessions:
        return "Keine gespeicherten Chats gefunden."
    lines = ["## Gespeicherte Chats", ""]
    for item in sessions:
        lines.append(
            "- "
            f"`{item['id']}` | {item['title']} | {item['message_count']} Nachrichten | "
            f"zuletzt: {item['updated_at']}"
        )
    lines.append("")
    lines.append("Nutze `/history <session_id>` für den Verlauf oder `/export <session_id>` für JSON.")
    return "\n".join(lines)


def _format_session_messages(session_id: str, limit: int = 20) -> str:
    messages = get_session_messages(CHAT_DB_PATH, session_id)
    if not messages:
        return f"Keine Nachrichten für Session `{session_id}` gefunden."
    tail = messages[-limit:]
    lines = [f"## Verlauf `{session_id}` (letzte {len(tail)} Nachrichten)", ""]
    for msg in tail:
        role = msg.get("role", "unknown")
        content = _truncate(str(msg.get("content", "")), max_len=280)
        created_at = msg.get("created_at", "")
        lines.append(f"- **{role}** ({created_at}): {content}")
    return "\n".join(lines)


async def _handle_control_message(message: cl.Message) -> bool:
    text = (message.content or "").strip()
    if not text.startswith("/"):
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/history":
        if arg:
            await cl.Message(content=_format_session_messages(arg)).send()
        else:
            await cl.Message(content=_format_history_overview()).send()
        return True

    if cmd == "/export":
        CHAT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _utc_stamp()
        if arg.lower() == "all":
            out_jsonl = CHAT_EXPORT_DIR / f"chat-export-openai-all-{stamp}.jsonl"
            export_all_sessions_openai_jsonl(CHAT_DB_PATH, out_jsonl)
            await cl.Message(
                content="OpenAI-Export für alle Chats erstellt (JSONL).",
                elements=[
                    cl.File(name=out_jsonl.name, path=str(out_jsonl), display="inline"),
                ],
            ).send()
            return True

        session_id = arg or _current_chat_session_id()
        if not session_id:
            await cl.Message(content="Keine aktive Session gefunden. Nutze `/export <session_id>` oder `/export all`.").send()
            return True
        out_json = CHAT_EXPORT_DIR / f"chat-export-openai-{session_id}-{stamp}.json"
        try:
            export_session_openai_json(CHAT_DB_PATH, session_id, out_json)
        except ValueError:
            await cl.Message(content=f"Session nicht gefunden: `{session_id}`").send()
            return True
        await cl.Message(
            content=f"OpenAI-Export erstellt für Session `{session_id}`.",
            elements=[cl.File(name=out_json.name, path=str(out_json), display="inline")],
        ).send()
        return True

    if cmd in {"/help-history", "/help"}:
        await cl.Message(
            content=(
                "Verfügbare Befehle:\n"
                "- `/history` zeigt gespeicherte Chats\n"
                "- `/history <session_id>` zeigt die letzten Nachrichten einer Session\n"
                "- `/export` exportiert den aktuellen Chat im OpenAI-Format (JSON)\n"
                "- `/export <session_id>` exportiert eine bestimmte Session im OpenAI-Format (JSON)\n"
                "- `/export all` exportiert alle Chats im OpenAI-Format (JSONL)\n"
                "- `/keywords` zeigt aktuelle Schlüsselwörter\n"
                "- `/keywords add <wort>` fügt ein manuelles Schlüsselwort hinzu\n"
                "- `/keywords remove <wort>` entfernt ein Schlüsselwort\n"
                "- `/keywords enable-all` aktiviert alle Schlüsselwörter\n"
                "- `/keywords disable-all` deaktiviert alle Schlüsselwörter\n"
                "- `/keywords regenerate` generiert Schlüsselwörter aus dem Chatverlauf neu\n"
                "- `/prompt show` zeigt den aktuellen System-Prompt\n"
                "- `/prompt reset` setzt den Prompt auf den Standard zurück\n"
                "- `/prompt set <text>` setzt einen benutzerdefinierten System-Prompt"
            )
        ).send()
        return True

    # --- Keyword management commands ---
    if cmd == "/keywords":
        user_profile: UserProfile | None = cl.user_session.get("user_profile")
        user_id = cl.user_session.get("current_user_id")

        if not arg:
            # Show current keywords
            if not user_profile or not user_profile.keywords:
                await cl.Message(content="Keine Schlüsselwörter vorhanden. Schreiben Sie mehr Nachrichten oder nutzen Sie `/keywords add <wort>`.").send()
                return True
            lines = []
            for kw in user_profile.keywords:
                status = "✅" if kw.get("active", True) else "❌"
                source = "auto" if kw.get("source") == "auto" else "manuell"
                lines.append(f"- {status} **{kw['value']}** ({source})")
            enabled = "aktiviert" if user_profile.personalization_enabled else "deaktiviert"
            header = f"**Schlüsselwörter** (Personalisierung: {enabled}):\n\n"
            await cl.Message(content=header + "\n".join(lines)).send()
            return True

        sub_parts = arg.split(maxsplit=1)
        sub_cmd = sub_parts[0].lower()
        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        if sub_cmd == "add" and sub_arg:
            if not user_profile:
                user_profile = UserProfile(user_id=user_id or "anonymous")
            # Check for duplicates
            existing_values = {_kw_key(k["value"]) for k in user_profile.keywords}
            if _kw_key(sub_arg) in existing_values:
                # Reactivate if deactivated
                target_key = _kw_key(sub_arg)
                for kw in user_profile.keywords:
                    if _kw_key(kw["value"]) == target_key:
                        kw["active"] = True
                await cl.Message(content=f"Schlüsselwort **{sub_arg}** aktiviert.").send()
            else:
                user_profile.keywords.append({"value": sub_arg, "active": True, "source": "manual"})
                await cl.Message(content=f"Schlüsselwort **{sub_arg}** hinzugefügt.").send()
            user_profile = await update_keyword_embeddings(user_profile)
            cl.user_session.set("user_profile", user_profile)
            _rebuild_system_prompt_in_session()
            return True

        if sub_cmd == "remove" and sub_arg:
            if user_profile:
                target = _kw_key(sub_arg)
                removed = False
                for kw in user_profile.keywords:
                    if _kw_key(kw["value"]) == target:
                        if kw.get("source") == "manual":
                            user_profile.keywords.remove(kw)
                        else:
                            kw["active"] = False
                        removed = True
                        break
                if removed:
                    user_profile = await update_keyword_embeddings(user_profile)
                    cl.user_session.set("user_profile", user_profile)
                    _rebuild_system_prompt_in_session()
                    await cl.Message(content=f"Schlüsselwort **{sub_arg}** entfernt.").send()
                else:
                    await cl.Message(content=f"Schlüsselwort **{sub_arg}** nicht gefunden.").send()
            return True

        if sub_cmd == "enable-all":
            if user_profile:
                for kw in user_profile.keywords:
                    kw["active"] = True
                user_profile = await update_keyword_embeddings(user_profile)
                cl.user_session.set("user_profile", user_profile)
                _rebuild_system_prompt_in_session()
                await cl.Message(content="Alle Schlüsselwörter aktiviert.").send()
            return True

        if sub_cmd == "disable-all":
            if user_profile:
                for kw in user_profile.keywords:
                    kw["active"] = False
                user_profile = await update_keyword_embeddings(user_profile)
                cl.user_session.set("user_profile", user_profile)
                _rebuild_system_prompt_in_session()
                await cl.Message(content="Alle Schlüsselwörter deaktiviert.").send()
            return True

        if sub_cmd == "regenerate":
            if not user_id:
                await cl.Message(content="Anmeldung erforderlich.").send()
                return True
            await cl.Message(content="Schlüsselwörter werden neu generiert...").send()
            user_profile = await regenerate_keywords(user_id)
            cl.user_session.set("user_profile", user_profile)
            _rebuild_system_prompt_in_session()
            active = user_profile.active_keyword_values()
            if active:
                await cl.Message(content=f"Neue Schlüsselwörter: {', '.join(active)}").send()
            else:
                await cl.Message(content="Keine Schlüsselwörter extrahiert. Schreiben Sie mehr Nachrichten.").send()
            return True

        await cl.Message(content="Unbekannter Unterbefehl. Nutze `/help` für Hilfe.").send()
        return True

    # --- Prompt management commands ---
    if cmd == "/prompt":
        user_profile: UserProfile | None = cl.user_session.get("user_profile")
        user_id = cl.user_session.get("current_user_id")

        sub_parts = arg.split(maxsplit=1) if arg else ["show"]
        sub_cmd = sub_parts[0].lower()
        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        if sub_cmd == "show":
            messages = cl.user_session.get("messages") or []
            current_prompt = ""
            if messages and messages[0].get("role") == "system":
                current_prompt = messages[0]["content"]
            if not current_prompt:
                current_prompt = SYSTEM_PROMPT or "(kein System-Prompt)"
            is_custom = user_profile and user_profile.custom_prompt
            label = "**Benutzerdefinierter Prompt:**" if is_custom else "**Standard-Prompt:**"
            await cl.Message(content=f"{label}\n\n```\n{current_prompt}\n```").send()
            return True

        if sub_cmd == "reset":
            if user_profile:
                user_profile.custom_prompt = None
                cl.user_session.set("user_profile", user_profile)
            if user_id:
                upsert_user_profile(CHAT_DB_PATH, user_id, custom_prompt=None)
            _rebuild_system_prompt_in_session()
            await cl.Message(content="System-Prompt auf Standard zurückgesetzt.").send()
            return True

        if sub_cmd == "set" and sub_arg:
            if not user_profile:
                user_profile = UserProfile(user_id=user_id or "anonymous")
            user_profile.custom_prompt = sub_arg
            cl.user_session.set("user_profile", user_profile)
            if user_id:
                upsert_user_profile(CHAT_DB_PATH, user_id, custom_prompt=sub_arg)
            _rebuild_system_prompt_in_session()
            await cl.Message(content="System-Prompt aktualisiert.").send()
            return True

        await cl.Message(content="Nutze `/prompt show`, `/prompt reset` oder `/prompt set <text>`.").send()
        return True

    return False

def _first_sentence(text: str, max_len: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    sentence = parts[0]
    if len(sentence) > max_len:
        sentence = sentence[: max_len - 3].rstrip() + "..."
    return sentence


def _extractive_answer_from_results(question: str, results: list[Any], max_points: int = 5) -> str:
    if not results:
        return "Im bereitgestellten Kontext nicht enthalten"

    seen: set[str] = set()
    bullets: list[str] = []
    for idx, result in enumerate(results, start=1):
        if len(bullets) >= max_points:
            break
        sentence = _first_sentence(getattr(result, "text", "") or "", max_len=280)
        if not sentence:
            continue
        key = re.sub(r"\s+", " ", sentence).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        bullets.append(f"- {sentence} [{idx}]")

    if not bullets:
        bullets = [
            f"- {(getattr(r, 'text', '') or '').strip()[:220]} [{i}]"
            for i, r in enumerate(results[:max_points], start=1)
            if (getattr(r, "text", "") or "").strip()
        ]
    if not bullets:
        bullets = ["- Relevante Fundstellen vorhanden, aber kein extrahierbarer Kurzsatz. [1]"]

    return (
        f"Ich habe relevante Inhalte im Kontext zur Frage \"{question}\" gefunden.\n\n"
        "Kernaussagen aus den Trefferstellen:\n"
        + "\n".join(bullets)
    )


def _strip_model_source_blocks(text: str) -> str:
    text = text.rstrip()
    patterns = [
        r"\n+\*\*Quellen\*\*[\s\S]*$",
        r"\n+Quellen[\s\S]*$",
        r"\n+Sources[\s\S]*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).rstrip()
    return text


def _page_label(page_start: int | None, page_end: int | None) -> str:
    # Very large ranges are usually fallback mappings; show a compact page anchor.
    if page_start and page_end and page_end >= page_start and (page_end - page_start) > 60:
        return f"S.{page_start}+"
    if page_start and page_end and page_end != page_start:
        return f"S.{page_start}-{page_end}"
    if page_start:
        return f"S.{page_start}"
    return "S.?"


def _judge_contexts(
    results: list[Any],
    source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]],
) -> list[str]:
    """The sources the user sees, labelled the way the answer cites them.

    The answer a judge scores is post-processed: the model's ``[1]`` becomes
    ``Quelle 1: Methods (S.7-8)``. Sending contexts labelled only
    ``Source: Methods — file.pdf — p. 7`` asked the judge to verify an attribution in
    a vocabulary the context never contained, and it answered — correctly — that the
    context "does not explicitly label it as 'Quelle 1'".

    Each alias is paired with its own chunk here, taken from the same ``source_rows``
    Chainlit renders in the panel, *after* ``_align_aliases_to_source_ids``. That
    pairing is the point: three numberings exist in this path — per-tool-call
    citations, the aggregated ``last_results`` position, and the displayed alias — and
    a judge context built by zipping two of them can put ``Quelle 1`` next to the
    chunk that belongs to another row. Pairing alias to chunk by construction cannot
    disagree, whatever the numbering does.

    Retrieved chunks with no panel row still go in, unlabelled and last: the panel
    dedupes by (file, page) and is capped by ``max_source_links``, so a claim can be
    supported by a chunk the model read and the panel never showed. Dropping those
    would trade a citation-verification bug for a content-verification one.
    """
    seen: set[int] = set()
    labelled: list[str] = []
    for source_id, alias, *_rest in source_rows:
        if not isinstance(source_id, int) or not 0 < source_id <= len(results):
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        labelled.append(f"{alias}\n{context_with_source(results[source_id - 1])}")

    unshown = [
        context_with_source(result)
        for idx, result in enumerate(results, start=1)
        if idx not in seen
    ]
    return labelled + unshown


def _source_alias(source_number: int, section_title: str | None, page_start: int | None, page_end: int | None) -> str:
    section = (section_title or "Abschnitt unbekannt").strip()
    section = re.sub(r"\s+", " ", section)
    if len(section) > 48:
        truncated = section[:45].rstrip()
        # Drop back before any unmatched `[` so Chainlit's frontend can wrap
        # the alias as a markdown link without the inner `[` breaking parsing.
        if truncated.count("[") > truncated.count("]"):
            truncated = truncated.rsplit("[", 1)[0].rstrip(" ,")
        section = f"{truncated}..."
    return f"Quelle {source_number}: {section} ({_page_label(page_start, page_end)})"


def _markdown_link(label: str, url: str) -> str:
    clean_label = re.sub(r"\s+", " ", label).strip()
    # Escape markdown control chars in link text so aliases like "[...]" remain clickable.
    clean_label = clean_label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"[{clean_label}]({url})"


def _resolve_section_title(metadata: dict[str, Any]) -> str | None:
    """Label for a chunk in citations: its section heading, else the doc title.

    Domain-specific ids can be surfaced instead via ``citation.segments`` /
    ``citation.extra_fields`` in the config — no hardcoding needed here."""
    for key in ("section_title", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _inject_clickable_refs(
    text: str,
    alias_by_index: dict[int, str],
    alias_by_number: dict[int, str] | None = None,
    url_by_index: dict[int, str] | None = None,
    url_by_number: dict[int, str] | None = None,
) -> str:
    if not text or (not alias_by_index and not alias_by_number):
        return text

    def repl(match: re.Match) -> str:
        idx = int(match.group(1))
        alias = alias_by_index.get(idx) or (alias_by_number or {}).get(idx)
        if not alias:
            return match.group(0)
        # Return the bare alias text so Chainlit's frontend can match it against an inline
        # cl.Pdf element name and render it as a side-panel opener. Markdown wrapping
        # would suppress that auto-detection and turn the citation into a new-tab link.
        return alias

    # Covers citations like: 【1†L1-L4】 and [1†L1-L4]
    text = re.sub(r"【(\d+)[^】]*】", repl, text)
    text = re.sub(r"\[(\d+)†[^\]]*\]", repl, text)
    # Covers citations like: [1]
    text = re.sub(r"\[(\d+)\]", repl, text)
    return text


def _alias_number_map(source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]]) -> dict[int, str]:
    alias_by_number: dict[int, str] = {}
    for src_idx, alias, *_ in source_rows:
        if isinstance(src_idx, int):
            alias_by_number[src_idx] = alias
        match = re.match(r"^\s*Quelle\s+(\d+)\s*:", alias, flags=re.IGNORECASE)
        if not match:
            continue
        alias_by_number[int(match.group(1))] = alias
    return alias_by_number


def _inject_named_source_refs(
    text: str,
    source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]],
) -> str:
    if not text or not source_rows:
        return text

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9äöüß]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    entries = []
    for _, alias, file_name, page_start, page_end, section_title, _ in source_rows:
        file_stem = Path(file_name).stem.lower()
        entries.append(
            {
                "alias": alias,
                "file_stem": norm(file_stem),
                "section": norm(section_title or ""),
                "page_start": page_start,
                "page_end": page_end,
                "is_kompendium": "kompendium" in file_stem,
            }
        )

    def replace_bracket(match: re.Match) -> str:
        raw = match.group(1).strip()
        # Keep pure numeric references for the numeric pass.
        if re.fullmatch(r"\d+", raw):
            return match.group(0)
        if "quelle " in raw.lower():
            return match.group(0)

        rnorm = norm(raw)
        if not rnorm:
            return match.group(0)

        page_match = re.search(r"(?:s\.?|seite)\s*(\d+)", raw, flags=re.IGNORECASE)
        wanted_page = int(page_match.group(1)) if page_match else None

        best_alias = None
        best_score = 0
        for entry in entries:
            score = 0
            if entry["file_stem"] and entry["file_stem"] in rnorm:
                score += 4
            if entry["section"] and any(tok in rnorm for tok in entry["section"].split()[:4]):
                score += 2
            if "kompendium" in rnorm and entry["is_kompendium"]:
                score += 2
            if "standard 200 2" in rnorm and "standard 200 2" in entry["file_stem"]:
                score += 3
            if wanted_page is not None:
                start = entry["page_start"]
                end = entry["page_end"] or start
                if isinstance(start, int) and isinstance(end, int) and start <= wanted_page <= end:
                    score += 2
            if score > best_score:
                best_score = score
                best_alias = entry["alias"]

        return best_alias if best_alias and best_score >= 3 else match.group(0)

    return re.sub(r"\[([^\[\]]{3,140})\]", replace_bracket, text)


def _normalize_source_alias_mentions(
    text: str,
    alias_by_index: dict[int, str],
    alias_by_number: dict[int, str] | None = None,
) -> str:
    if not text or (not alias_by_index and not alias_by_number):
        return text

    def repl(match: re.Match) -> str:
        idx = int(match.group(1))
        return alias_by_index.get(idx) or (alias_by_number or {}).get(idx, match.group(0))

    # Normalize free-form mentions like
    # "Quelle 1: Abschnittstitel ... (S.397)" or
    # "Quelle 2: Einleitung ... (Seite 12)" to exact alias token.
    text = re.sub(
        r"Quelle\s*([0-9]+)\s*:\s*[^\n]*?\((?:S\.?|Seite)\s*[^)\n]+\)",
        repl,
        text,
        flags=re.IGNORECASE,
    )
    # Also normalize bracket-wrapped alias mentions so they become clickable tokens.
    text = re.sub(
        r"【\s*Quelle\s*([0-9]+)\s*:\s*[^】]+\s*】",
        repl,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\[\s*Quelle\s*([0-9]+)\s*:\s*[^\]]+\s*\]",
        repl,
        text,
        flags=re.IGNORECASE,
    )
    # Normalize plain mentions like "Quelle 2" (without trailing ": ...").
    text = re.sub(
        r"\bQuelle\s*([0-9]+)\b(?!\s*:)",
        repl,
        text,
        flags=re.IGNORECASE,
    )
    return text


def _normalize_source_mentions_by_content(
    text: str,
    source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]],
) -> str:
    if not text or not source_rows:
        return text

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9äöüß]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    entries = []
    for _, alias, _, page_start, page_end, section_title, _ in source_rows:
        entries.append(
            {
                "alias": alias,
                "section": norm(section_title or ""),
                "page_start": page_start,
                "page_end": page_end,
            }
        )

    def repl(match: re.Match) -> str:
        raw = match.group(0)
        page_match = re.search(r"(?:S\.?|Seite)\s*(\d+)", raw, flags=re.IGNORECASE)
        wanted_page = int(page_match.group(1)) if page_match else None
        rnorm = norm(raw)

        best_alias = None
        best_score = 0
        for entry in entries:
            score = 0
            if wanted_page is not None:
                start = entry["page_start"]
                end = entry["page_end"] or start
                if isinstance(start, int) and isinstance(end, int) and start <= wanted_page <= end:
                    score += 3
            if entry["section"] and any(tok in rnorm for tok in entry["section"].split()[:5]):
                score += 2
            if score > best_score:
                best_score = score
                best_alias = entry["alias"]

        return best_alias if best_alias and best_score >= 2 else raw

    return re.sub(
        r"Quelle\s*\d+\s*:\s*[^\n]{1,260}?\((?:S\.?|Seite)\s*[^)\n]+\)",
        repl,
        text,
        flags=re.IGNORECASE,
    )


# Anchored on the literal word "Quelle". Tolerates (a) optional **/__ decorators
# around the span (stripped on replacement so Chainlit's frontend can wrap the
# alias as a clickable anchor), and (b) a missing "N: " prefix (filled in from
# the best-matching source_row alias). See the `_canonicalize_citations` docstring.
_CITATION_CANONICAL_RE = re.compile(
    r"(?P<pre>\*{1,2}|_{1,2})?"
    r"\bQuelle\s+"
    r"(?:(?P<num>\d+)\s*:\s*)?"
    r"(?P<body>[^\n]{1,220}?)"
    r"\((?:S\.?|Seite)\s*(?P<page>\d+)(?:\s*[-\u2013]\s*(?P<page_end>\d+))?\s*\)"
    r"(?P<post>\*{1,2}|_{1,2})?",
    flags=re.IGNORECASE,
)

_BSI_ID_RE = re.compile(r"[A-Z]{3,4}(?:\.\d+){1,3}(?:\.[ASH]\d+)?")


def _canonicalize_citations(
    text: str,
    source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]],
) -> str:
    """Final-pass canonicalizer: every `Quelle ...(S.X)` span in ``text`` is
    rewritten to the exact alias of the best-matching source_row, with any
    adjacent stray bold/italic markers stripped.

    Why this exists: Chainlit's frontend wires clicks to inline cl.Pdf elements
    via strict ``name`` equality against substrings in the assistant content.
    LLM output routinely deviates from the prescribed alias format in ways that
    defeat that equality check — unmatched ``**`` around the token, or a
    missing ``N: `` prefix. This pass is the single source of truth for
    mapping any citation-shaped span back to a registered element name.

    Scoring signals:
        * page in [page_start, page_end]  -> +3
        * BSI-ID appears verbatim in section -> +4
        * any of first-5 section tokens appears in match text -> +2

    Thresholds:
        * numbered form (``Quelle 3: ...``) -> require best_score >= 2,
          AND when best_score == 3 (pure page-range hit with no other
          signal) require a corroborating signal: BSI-ID in section,
          >= 1 section-token hit, OR the winning alias's ``Quelle N:``
          prefix number matches the LLM-provided number. This prevents
          silent mis-routing when multiple retrieved sources share a
          page and the LLM-supplied title doesn't match either.
        * numberless form (``Quelle <Abschnitt> ...``) -> require best_score >= 5
          AND either a BSI-ID match OR >= 2 section-token hits, to avoid
          rewriting prose like "Die Quelle der Daten (S.10)".

    Below-threshold matches are left unchanged (fails open). Idempotent when
    the pipeline already agrees.
    """
    if not text or not source_rows:
        return text

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9\u00e4\u00f6\u00fc\u00df]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    entries: list[dict[str, Any]] = []
    for _, alias, _, page_start, page_end, section_title, _ in source_rows:
        entries.append(
            {
                "alias": alias,
                "section": norm(section_title or ""),
                "page_start": page_start,
                "page_end": page_end,
            }
        )

    def repl(match: re.Match) -> str:
        raw = match.group(0)
        body = match.group("body") or ""
        num_group = match.group("num")
        wanted_page = int(match.group("page"))
        # Score the full match text so pre/post decorators don't perturb token
        # comparisons; the replacement below still consumes them.
        rnorm = norm(raw)

        bsi_match = _BSI_ID_RE.search(body)
        bsi_id = bsi_match.group(0).lower() if bsi_match else None

        best_alias: str | None = None
        best_score = 0
        best_section_token_hits = 0
        best_bsi_hit = False
        for entry in entries:
            score = 0
            section = entry["section"] or ""
            start = entry["page_start"]
            end = entry["page_end"] or start
            if isinstance(start, int) and isinstance(end, int) and start <= wanted_page <= end:
                score += 3
            bsi_hit = bool(bsi_id and bsi_id in section)
            if bsi_hit:
                score += 4
            section_tokens = section.split()[:5]
            token_hits = sum(1 for tok in section_tokens if tok in rnorm)
            if token_hits:
                score += 2
            if score > best_score:
                best_score = score
                best_alias = entry["alias"]
                best_section_token_hits = token_hits
                best_bsi_hit = bsi_hit

        if not best_alias:
            return raw

        if num_group is None:
            # Numberless form: demand strong signal to avoid rewriting prose.
            if best_score < 5 or not (best_bsi_hit or best_section_token_hits >= 2):
                print(
                    f"[WARN] citation.unresolved (numberless) fragment={raw[:160]!r} "
                    f"bsi_id={bsi_id} page={wanted_page} best_score={best_score}"
                )
                return raw
        else:
            # A bare page-range hit (+3) alone is not enough. When two
            # retrieved sources share a page and the LLM section title
            # doesn't match either, the earlier entry wins the strict `>`
            # tie-break and we'd silently rewrite `Quelle 3: ...` to the
            # wrong alias. Require a corroborating signal in that case.
            alias_num_match = re.match(
                r"^\s*Quelle\s+(\d+)\s*:", best_alias, flags=re.IGNORECASE
            )
            alias_num_matches = bool(
                alias_num_match and alias_num_match.group(1) == num_group
            )
            has_content_signal = (
                best_bsi_hit or best_section_token_hits >= 1 or alias_num_matches
            )
            if best_score < 2 or (best_score == 3 and not has_content_signal):
                print(
                    f"[WARN] citation.unresolved fragment={raw[:160]!r} "
                    f"bsi_id={bsi_id} page={wanted_page} best_score={best_score}"
                )
                return raw

        return best_alias

    return _CITATION_CANONICAL_RE.sub(repl, text)


def _inject_source_alias_links(
    text: str,
    alias_by_number: dict[int, str],
    url_by_number: dict[int, str],
) -> str:
    # Intentionally a no-op: in-text "Quelle N: ..." mentions stay as bare alias text
    # so Chainlit's frontend can match them against inline cl.Pdf element names and
    # render them as side-panel openers. Wrapping them as markdown links would
    # suppress that auto-detection and revert to opening the PDF in a new browser tab.
    return text


def _inject_naked_source_links(text: str) -> str:
    if not text:
        return text

    def repl(match: re.Match) -> str:
        label = match.group("label")
        if not isinstance(label, str):
            return match.group(0)
        # Strip the trailing "(/sources/pdf/...)" segment so the alias remains as bare
        # text — Chainlit's frontend will then match it against an inline cl.Pdf element
        # name and render it as a side-panel opener instead of a new-tab link.
        return label

    return re.sub(
        r"(?P<label>Quelle\s*\d+\s*:[^\n]{1,260}?\((?:S\.?|Seite)\s*[^)\n]+\))\((?:https?://[^\s)]+|/sources/pdf/[^)\s]+)\)",
        repl,
        text,
        flags=re.IGNORECASE,
    )


def _compact_visible_source_numbering(
    content: str,
    source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]],
    source_rows_for_session: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, str, str, int | None, int | None, str | None, str]], list[dict[str, Any]]]:
    if not source_rows:
        return content, source_rows, source_rows_for_session

    alias_remap: dict[str, str] = {}
    remapped_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]] = []
    for display_idx, row in enumerate(source_rows, start=1):
        source_id, old_alias, file_name, page_start, page_end, section_title, evidence = row
        new_alias = _source_alias(display_idx, section_title, page_start, page_end)
        alias_remap[old_alias] = new_alias
        remapped_rows.append(
            (
                source_id,
                new_alias,
                file_name,
                page_start,
                page_end,
                section_title,
                evidence,
            )
        )

    remapped_session_rows: list[dict[str, Any]] = []
    for row in source_rows_for_session:
        if not isinstance(row, dict):
            continue
        old_alias = row.get("alias")
        new_alias = alias_remap.get(old_alias) if isinstance(old_alias, str) else None
        if not isinstance(new_alias, str):
            remapped_session_rows.append(dict(row))
            continue
        updated = dict(row)
        updated["alias"] = new_alias
        remapped_session_rows.append(updated)

    updated_content = content
    for old_alias, new_alias in alias_remap.items():
        if old_alias == new_alias:
            continue
        escaped_old = old_alias.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        escaped_new = new_alias.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        updated_content = updated_content.replace(f"[{escaped_old}](", f"[{escaped_new}](")
        updated_content = updated_content.replace(old_alias, new_alias)
        updated_content = updated_content.replace(escaped_old, escaped_new)

    return updated_content, remapped_rows, remapped_session_rows


def _align_aliases_to_source_ids(
    content: str,
    source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]],
    source_rows_for_session: list[dict[str, Any]],
) -> tuple[str, list[tuple[int, str, str, int | None, int | None, str | None, str]], list[dict[str, Any]]]:
    if not source_rows:
        return content, source_rows, source_rows_for_session

    alias_remap: dict[str, str] = {}
    remapped_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]] = []
    for source_id, old_alias, file_name, page_start, page_end, section_title, evidence in source_rows:
        if isinstance(source_id, int) and source_id > 0:
            new_alias = _source_alias(source_id, section_title, page_start, page_end)
        else:
            new_alias = old_alias
        alias_remap[old_alias] = new_alias
        remapped_rows.append(
            (
                source_id,
                new_alias,
                file_name,
                page_start,
                page_end,
                section_title,
                evidence,
            )
        )

    remapped_session_rows: list[dict[str, Any]] = []
    for row in source_rows_for_session:
        if not isinstance(row, dict):
            continue
        old_alias = row.get("alias")
        new_alias = alias_remap.get(old_alias) if isinstance(old_alias, str) else None
        updated = dict(row)
        if isinstance(new_alias, str):
            updated["alias"] = new_alias
        remapped_session_rows.append(updated)

    updated_content = content
    for old_alias, new_alias in alias_remap.items():
        if old_alias == new_alias:
            continue
        escaped_old = old_alias.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        escaped_new = new_alias.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        updated_content = updated_content.replace(f"[{escaped_old}](", f"[{escaped_new}](")
        updated_content = updated_content.replace(old_alias, new_alias)
        updated_content = updated_content.replace(escaped_old, escaped_new)

        old_num_match = re.match(r"^\s*Quelle\s*(\d+)\s*:", old_alias, flags=re.IGNORECASE)
        new_num_match = re.match(r"^\s*Quelle\s*(\d+)\s*:", new_alias, flags=re.IGNORECASE)
        if old_num_match and new_num_match and old_num_match.group(1) != new_num_match.group(1):
            updated_content = re.sub(
                rf"\bQuelle\s*{re.escape(old_num_match.group(1))}\b",
                f"Quelle {new_num_match.group(1)}",
                updated_content,
                flags=re.IGNORECASE,
            )

    return updated_content, remapped_rows, remapped_session_rows


def _inject_alias_links_by_rows(text: str, source_rows: list[dict[str, Any]]) -> str:
    # Intentionally a no-op: aliases stay as bare text in the message body so Chainlit's
    # frontend can match them against inline cl.Pdf element names and render them as
    # side-panel openers. The previous final-pass markdown wrapping is what made
    # citations open in a new browser tab; that behaviour is now replaced by inline
    # PDF elements attached to the assistant message in main()/on_chat_resume.
    return text


def _desired_source_count(text: str, available: int) -> int:
    if available <= 0:
        return 0
    refs: list[int] = []
    for pattern in (
        r"\[(\d+)\]",
        r"【(\d+)[^】]*】",
        r"\[(\d+)†[^\]]*\]",
        r"Quelle\s*(\d+)\s*:",
        r"\bQuelle\s*(\d+)\b",
    ):
        refs.extend(int(x) for x in re.findall(pattern, text or ""))
    if refs:
        return min(max(refs), available)
    return available


def _result_key(item: Any) -> tuple[str, int | None, str]:
    metadata = getattr(item, "metadata", {}) or {}
    file_name = extract_source_file(metadata) or ""
    page = extract_page(metadata)
    snippet = re.sub(r"\s+", " ", (getattr(item, "text", "") or "").strip())[:120]
    return (file_name, page, snippet)


def _extract_followups(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if re.search(r"anschlussfragen|weitere fragen", line, flags=re.IGNORECASE):
            start_idx = i
            break
    if start_idx is None:
        return text, []

    questions: list[str] = []
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        raw = lines[j].strip()
        if not raw:
            if questions:
                end_idx = j
                break
            continue

        m = re.match(r"^(?:\d+[\).\:]|-|\*)\s+(.*)$", raw)
        candidate = m.group(1).strip() if m else raw
        candidate = re.sub(r"\s+", " ", candidate)
        # Prefer question-shaped lines, but keep numbered follow-ups even without '?'.
        if candidate.endswith("?") or re.match(r"^(?:\d+[\).\:]|-|\*)\s+", raw):
            questions.append(candidate)
        elif questions:
            end_idx = j
            break
        if len(questions) >= 3:
            end_idx = j + 1
            break

    # Fallback: collect up to 3 trailing numbered/bullet lines anywhere in the answer.
    if not questions:
        for raw in lines:
            m = re.match(r"^\s*(?:\d+[\).\:]|-|\*)\s+(.+)$", raw)
            if not m:
                continue
            candidate = re.sub(r"\s+", " ", m.group(1)).strip()
            if len(candidate) < 12:
                continue
            questions.append(candidate)
            if len(questions) >= 3:
                break

    if not questions:
        return text, []

    cleaned_lines = lines[:start_idx] + lines[end_idx:]
    cleaned_text = "\n".join(cleaned_lines).strip()
    return cleaned_text, questions


def _coerce_step_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("content")
        if isinstance(text, str):
            return text
    return str(value)


@cl.oauth_callback
async def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict[str, Any],
    default_user: cl.User,
) -> cl.User | None:
    """Handle OAuth login (e.g., GitHub).

    Returns a provider-specific user for GitHub, or the default user for other
    OAuth providers.
    """
    if provider_id == "github":
        return cl.User(
            identifier=raw_user_data.get("login"),  # GitHub username
            metadata={
                "provider": "github",
                "name": raw_user_data.get("name"),
                "email": raw_user_data.get("email"),
                "avatar_url": raw_user_data.get("avatar_url"),
                "github_id": str(raw_user_data.get("id")),
            },
        )
    # Accept all users from other configured OAuth providers
    return default_user


def _coerce_step_metadata(step: dict[str, Any]) -> dict[str, Any]:
    raw = step.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _sanitize_source_rows_payload(raw_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        file_name = row.get("file")
        alias = row.get("alias")
        page = row.get("page")
        if not isinstance(file_name, str) or not isinstance(alias, str):
            continue
        clean_row: dict[str, Any] = {"file": file_name, "alias": alias}
        source_id = row.get("source_id")
        if isinstance(source_id, int) and source_id > 0:
            clean_row["source_id"] = source_id
        if isinstance(page, int):
            clean_row["page"] = page
            clean_row["page_start"] = page
        page_start = row.get("page_start")
        if isinstance(page_start, int):
            clean_row["page_start"] = page_start
        page_end = row.get("page_end")
        if isinstance(page_end, int):
            clean_row["page_end"] = page_end
        section = row.get("section")
        if isinstance(section, str) and section.strip():
            clean_row["section"] = re.sub(r"\s+", " ", section).strip()
        evidence = row.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            clean_row["evidence"] = re.sub(r"\s+", " ", evidence).strip()
        cleaned.append(clean_row)
    return cleaned


def _sanitize_citation_history(raw_history: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_history, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        panel_content = item.get("panel_content")
        source_rows = _sanitize_source_rows_payload(item.get("source_rows"))
        if not isinstance(panel_content, str) or not panel_content.strip():
            continue
        cleaned.append({"panel_content": panel_content, "source_rows": source_rows})
    return cleaned


def _append_citation_history(
    history: list[dict[str, Any]],
    panel_content: str | None,
    source_rows: list[dict[str, Any]],
    *,
    max_entries: int = 60,
) -> list[dict[str, Any]]:
    if not isinstance(panel_content, str) or not panel_content.strip():
        return history
    cleaned_rows = _sanitize_source_rows_payload(source_rows)
    entry = {"panel_content": panel_content, "source_rows": cleaned_rows}

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*history, entry]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[-max_entries:]


def _build_citation_history_view(history: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    cleaned_history = _sanitize_citation_history(history)
    if not cleaned_history:
        return None, []

    lines = ["## Quellen & Belegstellen (Verlauf)", ""]
    merged_rows: list[dict[str, Any]] = []

    ordered_history = list(enumerate(cleaned_history, start=1))
    for answer_number, item in reversed(ordered_history):
        lines.append(f"## Antwort {answer_number}")
        answer_rows = _sanitize_source_rows_payload(item["source_rows"])
        if answer_rows:
            for row_idx, row in enumerate(answer_rows, start=1):
                page_start = row.get("page_start")
                if not isinstance(page_start, int):
                    page_start = row.get("page") if isinstance(row.get("page"), int) else None
                page_end = row.get("page_end") if isinstance(row.get("page_end"), int) else page_start
                section = row.get("section")
                section_for_alias = section.strip() if isinstance(section, str) and section.strip() else None
                if section_for_alias is None:
                    raw_alias = row.get("alias")
                    if isinstance(raw_alias, str) and raw_alias.strip():
                        section_for_alias = re.sub(r"^\s*Quelle\s*\d+\s*:\s*", "", raw_alias, flags=re.IGNORECASE).strip()
                        section_for_alias = re.sub(
                            r"\s*\((?:S\.?|Seite)\s*[^)]+\)\s*$",
                            "",
                            section_for_alias,
                            flags=re.IGNORECASE,
                        ).strip()

                source_id = row.get("source_id")
                if isinstance(source_id, int) and source_id > 0:
                    alias_display = _source_alias(source_id, section_for_alias, page_start, page_end)
                else:
                    alias = row.get("alias")
                    if isinstance(alias, str) and alias.strip():
                        alias_display = alias.strip()
                    else:
                        alias_display = _source_alias(row_idx, section_for_alias, page_start, page_end)
                lines.append(f"### {alias_display}")
                file_name = row.get("file")
                if isinstance(file_name, str) and file_name.strip():
                    lines.append(f"Datei: `{file_name}`")
                    pdf_url = _source_pdf_url(file_name)
                    page_for_link = page_start
                    if isinstance(page_for_link, int):
                        pdf_url = f"{pdf_url}#page={page_for_link}"
                    lines.append(f"PDF: [Öffnen]({pdf_url})")
                if isinstance(source_id, int) and source_id > 0:
                    lines.append(f"Quellen-ID: {source_id}")
                else:
                    lines.append(f"Quellen-ID: {row_idx}")
                if isinstance(page_start, int):
                    lines.append(f"Seiten: {_page_label(page_start, page_end if isinstance(page_end, int) else None)}")
                if isinstance(section, str) and section.strip():
                    lines.append(f"Abschnitt: {section.strip()}")
                evidence = row.get("evidence")
                if isinstance(evidence, str) and evidence.strip():
                    lines.append(f"Belegsnippet: \"{evidence.strip()}\"")
                lines.append("")
                merged_rows.append(row)
        else:
            parsed_aliases = re.findall(r"^###\s*\[\d+\]\s*(.+)$", item["panel_content"], flags=re.MULTILINE)
            if not parsed_aliases:
                parsed_aliases = re.findall(r"^###\s*(.+)$", item["panel_content"], flags=re.MULTILINE)
            if parsed_aliases:
                for alias in parsed_aliases:
                    normalized_alias = alias.strip()
                    if normalized_alias:
                        lines.append(f"### {normalized_alias}")
                    lines.append("")
            else:
                lines.append("Keine Zitierungen erkannt.")
        lines.append("")

    return "\n".join(lines).strip(), merged_rows


def _append_source_links_to_panel(panel_content: str, source_rows: list[dict[str, Any]]) -> str:
    if not isinstance(panel_content, str) or not panel_content.strip():
        return panel_content
    rows = _sanitize_source_rows_payload(source_rows)
    if not rows:
        return panel_content

    # TODO(citations-ux): Re-evaluate whether source access should be links only (current),
    # sidebar preview only, or dual mode. Links are currently preferred for reliability
    # across resume/reload and container restarts.
    base = re.sub(r"\n+### PDF öffnen[\s\S]*$", "", panel_content.strip(), flags=re.IGNORECASE).strip()

    lines: list[str] = []
    seen: set[tuple[str, int | None, str]] = set()
    for row in rows:
        file_name = row.get("file")
        alias = row.get("alias")
        page = row.get("page")
        if not isinstance(file_name, str) or not isinstance(alias, str):
            continue
        key = (file_name, page if isinstance(page, int) else None, alias)
        if key in seen:
            continue
        seen.add(key)
        pdf_url = _source_pdf_url(file_name)
        if isinstance(page, int):
            pdf_url = f"{pdf_url}#page={page}"
        label = alias
        if isinstance(page, int) and not re.search(r"\(S\.?\s*\d", alias, flags=re.IGNORECASE):
            label = f"{alias} (S.{page})"
        lines.append(f"- {_markdown_link(label, pdf_url)}")

    if not lines:
        return base or panel_content

    return f"{base}\n\n### PDF öffnen\n" + "\n".join(lines)


def _build_citation_elements(
    panel_content: str,
    source_rows: list[dict[str, Any]],
    *,
    include_panel_text: bool = True,
    citation_step_id: str | None = None,
) -> list[Any]:
    elements: list[Any] = []
    if include_panel_text:
        if isinstance(citation_step_id, str) and citation_step_id.strip():
            elements.append(cl.Text(name="CITATIONS_PANEL", url=_citation_panel_url(citation_step_id), display="side"))
        else:
            elements.append(cl.Text(name="CITATIONS_PANEL", content=panel_content, display="side"))
    return elements


def _build_inline_pdf_elements(source_rows: list[dict[str, Any]] | None) -> list[Any]:
    """Build one cl.Pdf element per cited source so Chainlit's frontend can match the
    bare alias text in the assistant message and open the PDF in the right side panel.
    De-dup is on alias because the alias is what appears in the message body."""
    elements: list[Any] = []
    if not source_rows:
        return elements
    seen: set[str] = set()
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        alias = row.get("alias")
        file_name = row.get("file")
        if not isinstance(alias, str) or not alias.strip():
            continue
        if not isinstance(file_name, str) or not file_name.strip():
            continue
        if alias in seen:
            continue
        if _resolve_source_pdf_path(file_name) is None:
            continue  # silently skip files outside DATA_RAW_DIR allowlist
        seen.add(alias)
        page = row.get("page_start") if isinstance(row.get("page_start"), int) else row.get("page")
        page_int = page if isinstance(page, int) else 1
        elements.append(
            cl.Pdf(
                name=alias,
                url=_source_pdf_url(file_name),
                page=page_int,
                display="side",
            )
        )
    return elements


def _build_inline_figure_elements(
    last_results: list[Any] | None,
    *,
    exclude_image_paths: set[str] | None = None,
    exclude_names: set[str] | None = None,
) -> list[Any]:
    """Build one cl.Image per retrieved figure that was NOT already inlined as a
    markdown image in the answer body.

    Chainlit renders every ``display="inline"`` element in the grid below the
    message regardless of the text, so a figure that is both inlined and
    elementized would appear TWICE. Excluded names are pre-seeded into ``seen``
    because Chainlit substring-matches element names against the body."""
    from kb.figure_store import figure_dir, resolve_figure_path

    elements: list[Any] = []
    if not last_results:
        return elements
    base = figure_dir(get_config())
    skip_paths = exclude_image_paths or set()
    seen: set[str] = set(exclude_names or set())
    for result in last_results:
        metadata = getattr(result, "metadata", None) or {}
        if not metadata.get("is_figure"):
            continue
        image_path = metadata.get("image_path")
        if not isinstance(image_path, str) or image_path in skip_paths:
            continue
        if resolve_figure_path(image_path, base) is None:
            continue
        name = figure_display_name(metadata)
        if name in seen:
            continue
        seen.add(name)
        # inline so the figure renders in the chat body (side elements only show
        # when their name is referenced in the answer text, which it isn't here).
        elements.append(
            cl.Image(name=name, url=_source_figure_url(image_path), display="inline", size="medium")
        )
    return elements


def _sanitize_followup_questions(raw_followups: Any, *, max_items: int = 8) -> list[str]:
    if not isinstance(raw_followups, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for question in raw_followups:
        if not isinstance(question, str):
            continue
        normalized = question.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _build_chat_actions(
    *,
    followup_questions: list[str],
    has_citations_panel: bool,
    source_step_id: str,
    citation_panel_content: str | None = None,
    citation_source_rows: list[dict[str, Any]] | None = None,
) -> list[cl.Action]:
    normalized_followups = _sanitize_followup_questions(followup_questions)
    base_payload: dict[str, Any] = {
        "source_step_id": source_step_id,
        "followup_questions": normalized_followups,
        "has_citations_panel": has_citations_panel,
    }
    actions: list[cl.Action] = []
    if has_citations_panel:
        if isinstance(citation_panel_content, str) and citation_panel_content.strip():
            base_payload["citation_panel_content"] = citation_panel_content
        cleaned_source_rows = _sanitize_source_rows_payload(citation_source_rows or [])
        if cleaned_source_rows:
            base_payload["citation_source_rows"] = cleaned_source_rows
        actions.append(
            cl.Action(
                name="open_all_citations",
                label="Quellen anzeigen",
                tooltip="Alle Quellen erneut im Seitenpanel anzeigen",
                payload={
                    **base_payload,
                    "show_history": True,
                },
            )
        )
    for question in normalized_followups:
        actions.append(
            cl.Action(
                name="ask_followup",
                label=question,
                tooltip=question,
                payload={
                    **base_payload,
                    "question": question,
                },
            )
        )
    return actions


async def _restore_actions_for_step(
    step_id: str | None,
    *,
    followup_questions: list[str],
    has_citations_panel: bool,
    citation_panel_content: str | None = None,
    citation_source_rows: list[dict[str, Any]] | None = None,
) -> None:
    if not isinstance(step_id, str) or not step_id.strip():
        return
    actions = _build_chat_actions(
        followup_questions=followup_questions,
        has_citations_panel=has_citations_panel,
        source_step_id=step_id,
        citation_panel_content=citation_panel_content,
        citation_source_rows=citation_source_rows,
    )
    if not actions:
        return
    for action in actions:
        await action.send(for_id=step_id)


async def _show_citation_sidebar(
    panel_content: str,
    source_rows: list[dict[str, Any]],
    *,
    citation_step_id: str | None = None,
    sidebar_title: str = CITATION_SIDEBAR_TITLE,
) -> None:
    elements = _build_citation_elements(
        panel_content,
        source_rows,
        citation_step_id=citation_step_id,
    )
    if not elements:
        return
    await cl.ElementSidebar.set_title(sidebar_title)
    # Force a refresh even when the sidebar key is unchanged.
    await cl.ElementSidebar.set_elements([], key="citations_panel")
    await cl.ElementSidebar.set_elements(elements, key="citations_panel")


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


@cl.password_auth_callback
async def auth_callback(username: str, password: str) -> cl.User | None:
    # Try database authentication first (if DATABASE_URL is configured)
    if DATABASE_URL:
        user = await get_user_by_identifier(DATABASE_URL, username)
        if user and user.get("password_hash"):
            if _verify_password(password, user["password_hash"]):
                metadata = json.loads(user.get("metadata") or "{}")
                metadata["provider"] = "local"
                return cl.User(identifier=user["identifier"], metadata=metadata)
            return None  # Wrong password for existing user

    # Fallback to environment variable authentication (for backwards compatibility / admin)
    expected_user = CHAINLIT_AUTH_USERNAME or "admin"
    expected_password = CHAINLIT_AUTH_PASSWORD
    if expected_password and username == expected_user and password == expected_password:
        return cl.User(identifier=expected_user, metadata={"provider": "password", "role": "admin"})

    return None


def _patch_cookie_security_openapi_model() -> None:
    """Give Chainlit's cookie security scheme the ``model`` FastAPI expects.

    ``chainlit.auth.cookie.OAuth2PasswordBearerWithCookie`` subclasses FastAPI's
    ``SecurityBase`` but never sets ``self.model``, so building the OpenAPI schema
    raises ``AttributeError: ... has no attribute 'model'`` and ``/openapi.json``
    (and therefore ``/docs``) returns 500. Upstream bug; this only adds the
    missing schema metadata, which nothing reads at request time.

    Guarded with ``hasattr`` so it goes inert once Chainlit ships a fix.
    """
    try:
        from chainlit.auth.cookie import OAuth2PasswordBearerWithCookie
        from fastapi.openapi.models import OAuth2 as OAuth2Model
        from fastapi.openapi.models import OAuthFlowPassword, OAuthFlows
    except ImportError as exc:  # noqa: BLE001 — upstream moved or renamed it
        print(f"[WARN] openapi_security: could not import Chainlit's cookie scheme: {exc}")
        return

    if getattr(OAuth2PasswordBearerWithCookie, "model", None) is not None:
        return

    OAuth2PasswordBearerWithCookie.model = OAuth2Model(
        flows=OAuthFlows(password=OAuthFlowPassword(tokenUrl="/login"))
    )
    print("[STARTUP] patched Chainlit cookie security scheme for /openapi.json")


class RegisterRequest(BaseModel):
    """Body of ``POST /auth/register``.

    MUST stay at module level. This file uses ``from __future__ import
    annotations``, so FastAPI sees the handler's annotation as the *string*
    ``"RegisterRequest"`` and resolves it against this module's globals. Defined
    inside the startup hook instead, the name is a local, resolution fails, and
    FastAPI silently downgrades the parameter to a **query** parameter: the JSON
    body is ignored (422 ``loc: ["query", "request"]``) and ``/openapi.json``
    returns 500. Nothing warns at startup.
    """

    username: str
    email: str
    password: str


def _retrieval_fields(cfg) -> dict:
    """The retrieval settings both the chip and its YAML report. One definition, so
    the panel cannot show a mode the copied config does not."""
    return {
        "top_k": cfg.retrieval.top_k,
        "score_threshold": cfg.retrieval.score_threshold,
        "hybrid": cfg.retrieval.hybrid,
        "fusion": cfg.retrieval.fusion,
        "prefetch_limit": cfg.retrieval.prefetch_limit,
    }


def _config_yaml(cfg) -> str:
    """The active settings as a pasteable config skeleton, for the chip's copy button.

    Built from the same curated fields the chip displays, deliberately **not** from
    ``cfg.model_dump()``: the real config object carries ``models.litellm_api_key``
    and ``vector_store.api_key``, and a copy button is exactly the wrong place to
    learn that. Nothing secret can reach here because nothing secret is collected.

    Each data source keeps its name, format, glob and *effective* chunking — the
    fields worth comparing between two configs. ``path`` is dropped: it is the one
    field that cannot transfer, and the schema requires it, so a paste fails with
    ``data_sources.0.path — Field required``, which names what to supply.
    """
    import yaml

    from tools import enabled_tool_ids

    def source_entry(src):
        chunking = src.chunking or cfg.chunking
        block = {
            "strategy": chunking.strategy,
            "max_chars": chunking.max_chars,
            "overlap": chunking.overlap,
            "min_section_chars": chunking.min_section_chars,
        }
        # Strategy-specific knobs only where they do something, so the output does
        # not imply a percentile matters to fixed_size.
        if chunking.strategy == "semantic":
            block["semantic_breakpoint_percentile"] = chunking.semantic_breakpoint_percentile
        if chunking.strategy == "docling_hybrid" and chunking.hybrid_max_tokens is not None:
            block["hybrid_max_tokens"] = chunking.hybrid_max_tokens
        entry = {"name": src.name, "format": src.format}
        if src.glob:
            entry["glob"] = src.glob
        entry["chunking"] = block
        return entry

    body = {
        "name": cfg.name,
        "language": cfg.language,
        "models": {
            "chat_model": cfg.models.chat_model,
            "fallback_chat_model": cfg.models.fallback_chat_model,
            "embed_model": cfg.models.embed_model,
        },
        "vector_store": {"collection": cfg.vector_store.collection},
        "data_sources": [source_entry(src) for src in cfg.data_sources],
        "retrieval": _retrieval_fields(cfg),
        "images": {"mode": cfg.images.mode, "vision_model": cfg.images.vision_model},
        "tools": {"enabled": enabled_tool_ids(cfg)},
    }
    # No header comment: the point is a config you can paste straight in, and three
    # lines of preamble is three lines to delete every time.
    return yaml.safe_dump(body, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _config_info_payload(cfg) -> dict:
    """What the ``/config-info`` header chip shows. Pure so it is testable
    without booting Chainlit. ``chat_model`` is the instance default — users
    may override it per-user in the settings panel."""
    from config.loader import resolve_config_path
    from tools import enabled_tool_ids

    return {
        "name": cfg.name,
        # Resolved through the loader's own helper: this chip is only worth having
        # if it cannot disagree with the loader about which file is loaded.
        "config_path": str(resolve_config_path()),
        "language": cfg.language,
        "collection": cfg.vector_store.collection,
        "chat_model": cfg.models.chat_model,
        "embed_model": cfg.models.embed_model,
        "sources": [
            {
                "name": src.name,
                "format": src.format,
                "chunking": (src.chunking or cfg.chunking).strategy,
            }
            for src in cfg.data_sources
        ],
        "retrieval": _retrieval_fields(cfg),
        "images_mode": cfg.images.mode,
        "tools": enabled_tool_ids(cfg),
        "yaml": _config_yaml(cfg),
    }


@cl.on_app_startup
async def on_app_startup() -> None:
    global SYSTEM_PROMPT
    # No system prompt configured? Generate one from the template + indexed docs
    # via the chat model (cached for future restarts).
    if not SYSTEM_PROMPT:
        from system_prompt_gen import ensure_system_prompt

        generated, cache_path = await ensure_system_prompt(get_config(), SYSTEM_PROMPT)
        if generated:
            SYSTEM_PROMPT = generated
            print(f"[STARTUP] auto-generated system prompt ({len(generated)} chars) -> {cache_path}")
        else:
            default_prompt = _load_system_prompt(
                Path(__file__).resolve().parent / "config" / "prompts" / "default_system.md"
            )
            if default_prompt:
                SYSTEM_PROMPT = default_prompt
                print("[STARTUP] system prompt: using bundled default_system.md fallback")

    # Warm the gateway model list once, off the event loop, so the settings
    # panel's model selector never triggers a blocking network call per session.
    import asyncio

    try:
        models = await asyncio.to_thread(list_chat_models)
        print(f"[STARTUP] gateway chat models: {models or '(none enumerable — using configured list)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"[STARTUP] model list warm-up skipped: {exc}")

    print(
        "[STARTUP] system_prompt_path=",
        str(SYSTEM_PROMPT_PATH),
        "exists=",
        SYSTEM_PROMPT_PATH.is_file(),
        "loaded=",
        bool(SYSTEM_PROMPT),
    )
    print(
        "[STARTUP] retrieval_tuning",
        "embed_model=",
        EMBED_MODEL,
        "top_k=",
        TOP_K,
        "| mode: simple_docling",
    )
    # Refuse to serve a collection that cannot answer as configured, rather than
    # running degraded while the config and the header chip both claim hybrid is on.
    # Ingest cannot catch this case: someone edits the config and restarts only the app.
    _startup_cfg = get_config()
    if _startup_cfg.retrieval.hybrid:
        from kb.ingestion_pipeline import get_client, verify_hybrid_compatible

        try:
            # to_thread: the check makes blocking Qdrant calls, and this runs on the
            # event loop during startup.
            await asyncio.to_thread(
                verify_hybrid_compatible,
                get_client(),
                _startup_cfg.vector_store.collection,
                hybrid=True,
            )
        except RuntimeError:
            raise  # a real incompatibility — refuse to serve it
        except Exception as exc:  # noqa: BLE001
            # Only the incompatibility is worth refusing to start over. The check
            # talks to Qdrant, and compose only waits for that container to *start*,
            # not to accept connections — so a cold start, a restart or a blip would
            # otherwise take the whole app down over a healthy collection. The
            # cached query-path check runs again later and still refuses then.
            print(
                f"[STARTUP] could not verify hybrid support for "
                f"'{_startup_cfg.vector_store.collection}' yet: {exc}"
            )

    from chainlit.server import app as chainlit_fastapi_app

    # Registered before the DATABASE_URL early return below: this route reads the
    # config singleton and touches no database, and auth_callback has an env-var
    # fallback, so a no-database instance still has logged-in users who would
    # otherwise get a header chip that never resolves.
    if not getattr(chainlit_fastapi_app.state, "config_info_route_added", False):

        @chainlit_fastapi_app.get("/config-info")
        async def config_info(current_user=Depends(get_current_user)):
            """The active instance configuration, for the header chip in the browser.

            Answers "which config is this container actually running?" without
            shell access: instance name, models, collection and the retrieval
            switches. Behind auth like every other route here — the values name
            your models, collection and document folders.
            """
            if current_user is None:
                raise HTTPException(status_code=401, detail="Unauthorized")
            return _config_info_payload(get_config())

        _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/config-info")
        chainlit_fastapi_app.state.config_info_route_added = True
        print("[STARTUP] config info route registered at /config-info")

    if DATABASE_URL and CHAINLIT_INIT_DB:
        await ensure_native_schema(DATABASE_URL)

    migrate_legacy_db(CHAT_DB_PATH, LEGACY_CHAT_DB_PATH)
    init_chat_db(CHAT_DB_PATH)
    CHAT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    if not DATABASE_URL:
        return

    if getattr(chainlit_fastapi_app.state, "native_export_route_added", False):
        return

    @chainlit_fastapi_app.get("/sources/pdf/{file_name:path}")
    async def source_pdf(file_name: str, current_user=Depends(get_current_user)):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")

        file_path = _resolve_source_pdf_path(file_name)
        if file_path is None:
            raise HTTPException(status_code=404, detail="Source PDF not found")

        return FileResponse(
            path=str(file_path),
            media_type=mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )

    @chainlit_fastapi_app.get("/sources/figure/{file_name:path}")
    async def source_figure(file_name: str, current_user=Depends(get_current_user)):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")

        from kb.figure_store import figure_dir, resolve_figure_path

        figure_path = resolve_figure_path(file_name, figure_dir(get_config()))
        if figure_path is None:
            raise HTTPException(status_code=404, detail="Figure not found")

        return FileResponse(
            path=str(figure_path),
            media_type="image/png",
            headers={"Content-Disposition": "inline"},
        )

    @chainlit_fastapi_app.get("/sources/citations/{step_id}")
    async def source_citations(step_id: str, current_user=Depends(get_current_user)):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")

        panel_content = await _load_citation_panel_content(step_id)
        if not isinstance(panel_content, str) or not panel_content.strip():
            raise HTTPException(status_code=404, detail="Citation panel not found")

        return PlainTextResponse(content=panel_content, media_type="text/plain; charset=utf-8")

    @chainlit_fastapi_app.get("/export/all-chats")
    async def export_all_chats(current_user=Depends(get_current_user)):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        user_id = getattr(current_user, "id", None)
        bundle = await export_all_chats_zip(
            database_url=DATABASE_URL,
            out_dir=CHAT_EXPORT_DIR,
            user_id=str(user_id) if user_id else None,
        )
        return FileResponse(path=str(bundle), media_type="application/zip", filename=bundle.name)

    @chainlit_fastapi_app.get("/ingest-status")
    async def ingest_status(current_user=Depends(get_current_user)):
        """What the folder watcher is doing, for the toast in the browser.

        The watcher is a background task with no Chainlit session, so it cannot push
        anything to a user. The browser polls this instead. Behind auth like every
        other route here, because the messages name your documents.

        ``lang`` rides along because the badge wording is chosen in the browser: one
        watcher serves every open tab, so a status built in one language would be
        wrong for half of them.
        """
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        lang = _forced_ui_language()
        if not DOCUMENT_WATCH:
            return {"state": "off", "message": "", "revision": 0, "lang": lang}
        from document_watch import get_status

        return {**get_status(), "lang": lang}

    # Registration endpoint for self-registration
    @chainlit_fastapi_app.post("/auth/register")
    async def register_user(request: RegisterRequest):
        # Validate input
        if not request.username or len(request.username) < 3:
            raise HTTPException(status_code=400, detail="Benutzername muss mindestens 3 Zeichen haben")
        if not request.email or "@" not in request.email:
            raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
        if not request.password or len(request.password) < 8:
            raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")

        # Check if user/email already exists
        exists = await check_user_exists(DATABASE_URL, request.username, request.email)
        if exists["username_exists"]:
            raise HTTPException(status_code=409, detail="Benutzername bereits vergeben")
        if exists["email_exists"]:
            raise HTTPException(status_code=409, detail="E-Mail-Adresse bereits registriert")

        # Create user with hashed password
        password_hash = _hash_password(request.password)
        user = await create_user(DATABASE_URL, request.username, request.email, password_hash)
        if user is None:
            raise HTTPException(status_code=500, detail="Registrierung fehlgeschlagen")

        return {"message": "Registrierung erfolgreich", "username": user["identifier"]}

    @chainlit_fastapi_app.get("/export/feedback")
    async def export_feedback(current_user=Depends(get_current_user)):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        user_meta = getattr(current_user, "metadata", None) or {}
        if user_meta.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        csv_file = await export_feedback_csv(
            database_url=DATABASE_URL,
            out_dir=CHAT_EXPORT_DIR,
        )
        return FileResponse(
            path=str(csv_file),
            media_type="text/csv; charset=utf-8",
            filename=csv_file.name,
        )

    @chainlit_fastapi_app.get("/eval-status")
    async def eval_status(thread_id: str | None = None, current_user=Depends(get_current_user)):
        """Running answer-quality numbers for the badge above the chatbox.

        Scoring happens in a background task with no live session, so it cannot push
        anything to a browser; the badge polls this instead, exactly like
        ``/ingest-status`` above. Behind auth like every other route here, because
        the numbers describe someone's own conversation.

        ``thread_id`` comes from the browser, which reads it out of a ``/thread/<uuid>``
        URL. It is required rather than guessed: without it there is nothing to
        report, and answering with the user's most recent conversation instead would
        put the previous chat's numbers above an empty composer. Chainlit routes to
        ``/thread/<uuid>`` as soon as the first message is sent, so a real
        conversation always has one.
        """
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        cfg = get_config()
        if not cfg.evaluation.enabled or not cfg.evaluation.show_badge:
            return {"enabled": False}
        # Which language to write the badge in. Carried on every enabled response
        # because the badge renders text on more than one of them.
        lang = _forced_ui_language()
        if not thread_id:
            return {"enabled": True, "answers": 0, "lang": lang}
        pending = thread_id in _SCORING_THREADS

        url = f"{cfg.evaluation.service_url.rstrip('/')}/api/thread/{thread_id}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = await client.get(url)
                response.raise_for_status()
                summary = response.json()
        except Exception as exc:
            # The eval service is optional; a badge that cannot reach it should go
            # quiet rather than turn into an error in the corner of the chat.
            print(f"[WARN] eval_status_unavailable: {exc.__class__.__name__}: {exc}")
            return {"enabled": True, "answers": 0, "pending": pending, "lang": lang}

        faithfulness = summary.get("faithfulness")
        relevance = summary.get("relevance")
        answers = summary.get("answers", 0)
        # One per metric. Both are running means over the same conversation, so a
        # trend on only one of them is a UI inconsistency rather than a statement
        # about the metrics.
        trend = trend_sign(faithfulness, summary.get("last_faithfulness"), answers)
        trend_relevance = trend_sign(relevance, summary.get("last_relevance"), answers)

        return {
            "enabled": True,
            "answers": answers,
            "faithfulness": faithfulness,
            "relevance": relevance,
            "trend": trend,
            "trend_relevance": trend_relevance,
            # A judge is working right now, so the badge can say so rather than
            # sitting silent for ~16s and looking broken.
            "pending": pending,
            # Why the last scored answer got those numbers, for the panel.
            "detail": summary.get("last_detail"),
            "lang": lang,
            # The quest marker: the newest answer cleared the gold thresholds and
            # is not yet a reference. The id keys the save request and lets the
            # browser remember a dismissal.
            "gold_suggest": gold_suggested(summary, cfg.evaluation),
            "last_message_id": summary.get("last_message_id"),
        }

    @chainlit_fastapi_app.get("/eval-stats")
    async def eval_stats(current_user=Depends(get_current_user)):
        """The per-configuration comparison, for the badge panel's second tab.

        Proxied server-side so the browser never needs the eval service's port:
        the eval container stays a pure backend, and this route inherits the
        app's auth and network position. Only ``configs`` is forwarded — the
        panel's table needs nothing else, and the gold turns in the full stats
        payload can be large.
        """
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        cfg = get_config()
        if not cfg.evaluation.enabled:
            return {"enabled": False}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = await client.get(f"{cfg.evaluation.service_url.rstrip('/')}/api/stats")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            print(f"[WARN] eval_stats_unavailable: {exc.__class__.__name__}: {exc}")
            return {"enabled": True, "configs": []}
        return {"enabled": True, "configs": payload.get("configs", [])}

    @chainlit_fastapi_app.post("/eval-gold")
    async def eval_gold(request: Request, current_user=Depends(get_current_user)):
        """Freeze a conversation as a gold reference, from the badge's suggestion.

        The turns come from the app's own chat history rather than a live session:
        this is a plain HTTP route (the badge script calls it), so there is no
        ``cl.user_session`` to ask — and the SQLite history survives reloads,
        which a session would not. The eval service swaps in the config signature
        of the scored answer, so the reference is filed under the model that
        actually produced it.
        """
        if current_user is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not get_config().evaluation.enabled:
            raise HTTPException(status_code=404, detail="evaluation disabled")
        body = await request.json()
        thread_id = body.get("thread_id")
        if not thread_id:
            raise HTTPException(status_code=422, detail="thread_id required")
        history = get_session_messages(CHAT_DB_PATH, thread_id)
        turns = conversation_turns(history)
        if not turns:
            raise HTTPException(status_code=409, detail="no completed turns")
        # The saver's one choice: the whole conversation, or just the final Q&A.
        # Only these two shapes replay coherently — an arbitrary subset would leave
        # later turns referring to context the replayed model never saw.
        if body.get("only_last"):
            turns = turns[-1:]
        response = await post_gold(
            turns=turns,
            message_id=body.get("message_id"),
            thread_id=thread_id,
        )
        if response is None:
            raise HTTPException(status_code=502, detail="eval service unreachable")
        return {"status": "ok", "turns": len(turns)}

    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/sources/pdf/{file_name:path}")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/sources/figure/{file_name:path}")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/sources/citations/{step_id}")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/export/all-chats")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/export/feedback")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/auth/register")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/ingest-status")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/eval-status")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/eval-stats")
    _ensure_route_precedes_catch_all(chainlit_fastapi_app, "/eval-gold")

    _patch_cookie_security_openapi_model()

    chainlit_fastapi_app.state.native_export_route_added = True
    print("[STARTUP] native export route registered at /export/all-chats")
    print("[STARTUP] feedback export route registered at /export/feedback")
    print("[STARTUP] registration route registered at /auth/register")

    if DOCUMENT_WATCH:
        from document_watch import watch_documents

        # Held on app state so the task is not garbage collected: asyncio keeps only
        # a weak reference, so a bare create_task() can be collected mid-flight.
        chainlit_fastapi_app.state.document_watch_task = asyncio.create_task(
            watch_documents()
        )
        print("[STARTUP] watching the document folders for changes")
    else:
        print("[STARTUP] document watching is off (DOCUMENT_WATCH=false)")

    if get_config().evaluation.enabled:
        # Benchmark jobs queued by the dashboard's play button. Polling rather than
        # an inbound endpoint keeps the dependency edge one-way: the eval service
        # never calls the app. Held on app state for the same weak-reference reason
        # as the watcher task above.
        chainlit_fastapi_app.state.benchmark_poll_task = asyncio.create_task(
            _poll_benchmark_jobs()
        )
        print("[STARTUP] polling the eval service for benchmark jobs")


async def _poll_benchmark_jobs() -> None:
    """Claim and run queued benchmark jobs, one at a time, forever.

    A replay takes minutes (turns × ~16s of judging each), so there is nothing to
    gain from concurrency — and the gateway is shared with live chats, which
    should not have to queue behind a benchmark burst.
    """
    import benchmark

    service_url = get_config().evaluation.service_url
    url = f"{service_url.rstrip('/')}/api/benchmark/next"
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = await client.get(url)
                response.raise_for_status()
                job = response.json()
            if job.get("id"):
                print(f"[benchmark] claimed job {job['id']} ({job['chat_model']})")
                try:
                    await benchmark.run_job(job, service_url=service_url)
                except Exception as exc:  # noqa: BLE001 — a failed run must not kill the poller
                    print(f"[WARN] benchmark_job_failed: {exc.__class__.__name__}: {exc}")
                    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                        await client.post(
                            f"{service_url.rstrip('/')}/api/benchmark/{job['id']}",
                            json={"status": "error", "error": str(exc)[:500]},
                        )
                continue  # check straight away — there may be more queued
        except Exception:  # noqa: BLE001 — the eval service being down is the normal case
            pass
        await asyncio.sleep(10)


@cl.on_feedback
async def on_feedback(feedback: cl.types.Feedback):
    if DATABASE_URL:
        await upsert_feedback(
            DATABASE_URL,
            feedback_id=feedback.id or str(__import__("uuid").uuid4()),
            step_id=feedback.forId,
            value=float(feedback.value),
            comment=feedback.comment,
        )
    # Also record it against the active configuration, which is what the evaluation
    # dashboard groups by. Outside the DATABASE_URL branch on purpose: the eval
    # store is a separate service, so thumbs stay measurable without Postgres.
    # Chainlit's own comment box supplies feedback.comment — nothing here prompts
    # for it — and classifying it happens in the eval service, not on this click.
    await post_feedback(
        rating="up" if feedback.value else "down",
        step_id=feedback.forId,
        thread_id=getattr(feedback, "threadId", None) or cl.context.session.thread_id,
        comment=feedback.comment,
        # The session's model, so the rating lands on the same signature its answer
        # did. Approximate by construction: switching models and then rating an older
        # answer files the thumb under the new one. Exact attribution needs the
        # forId join described in evaluation.post_feedback.
        chat_model=_session_chat_model(),
    )


@cl.on_chat_resume
async def on_chat_resume(thread: dict[str, Any]):
    thread_id = thread.get("id")
    session_source_catalog = _empty_source_catalog()
    if isinstance(thread_id, str) and thread_id.strip():
        create_chat_session(CHAT_DB_PATH, thread_id)
        cl.user_session.set("chat_history_session_id", thread_id)
        session_source_catalog = _load_session_source_catalog(thread_id)

    messages: list[dict[str, Any]] = []
    restored_panel_content: str | None = None
    restored_source_rows: list[dict[str, Any]] = []
    restored_followup_questions: list[str] = []
    restored_citation_history: list[dict[str, Any]] = []
    latest_assistant_step_id: str | None = None
    latest_assistant_has_actions = False
    # Per-step citation metadata so "Quellen anzeigen" can be restored on
    # every historical assistant step, not just the latest one. See the
    # loop below and the comment block in the assistant_message branch.
    historical_citation_steps: list[dict[str, Any]] = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})

    steps = thread.get("steps") or []
    sorted_steps = sorted(
        [s for s in steps if isinstance(s, dict)],
        key=lambda s: (s.get("start") or s.get("createdAt") or "", s.get("id") or ""),
    )
    for step in sorted_steps:
        step_type = str(step.get("type") or "").lower()
        if "user_message" in step_type:
            text = _coerce_step_text(step.get("output") or step.get("input"))
            if text:
                messages.append({"role": "user", "content": text})
        elif "assistant_message" in step_type:
            text = _coerce_step_text(step.get("output") or step.get("input"))
            if text:
                # The persisted text is the rendered one (it may contain inlined
                # figure images); strip them so the model does not learn to emit
                # image markdown itself. The displayed history is untouched.
                messages.append({"role": "assistant", "content": sanitize_for_model(text)})
            step_id = step.get("id")
            normalized_step_id: str | None = None
            step_has_actions = False
            if isinstance(step_id, str) and step_id.strip():
                normalized_step_id = step_id
                latest_assistant_step_id = step_id
                step_actions = step.get("actions")
                step_has_actions = isinstance(step_actions, list) and len(step_actions) > 0
                latest_assistant_has_actions = step_has_actions
            metadata = _coerce_step_metadata(step)
            panel_content = metadata.get("citation_panel_content")
            source_rows = metadata.get("citation_source_rows")
            followup_questions = metadata.get("followup_questions")
            if isinstance(panel_content, str) and panel_content.strip():
                restored_panel_content = panel_content
            valid_rows = _sanitize_source_rows_payload(source_rows)
            if valid_rows:
                restored_source_rows = valid_rows
            # Do NOT reattach inline cl.Pdf(display="side") elements on resume.
            # Chainlit pops the side panel whenever a display="side" element is
            # emitted (and 2.11+ codifies this in MessagesContainer), so
            # reattaching per-source PDFs on every resumed message would force
            # an unrequested sidebar open. Historical messages therefore render
            # `Quelle N:` references as plain text; the per-step "Quellen
            # anzeigen" action (restored below for EVERY historical step with
            # citation metadata, not just the latest) is the on-demand path
            # for citations in old answers. Fresh answers in the same session
            # still attach inline PDFs in the hot path, so the inline button
            # behavior is preserved for newly-rendered messages.
            if (
                normalized_step_id
                and isinstance(panel_content, str)
                and panel_content.strip()
            ):
                historical_citation_steps.append(
                    {
                        "step_id": normalized_step_id,
                        "has_actions": step_has_actions,
                        "panel_content": panel_content,
                        "source_rows": valid_rows,
                    }
                )
            restored_citation_history = _append_citation_history(
                restored_citation_history,
                panel_content if isinstance(panel_content, str) else None,
                valid_rows,
            )
            valid_followups = _sanitize_followup_questions(followup_questions)
            if valid_followups:
                restored_followup_questions = valid_followups

    cl.user_session.set("messages", messages)
    cl.user_session.set("citation_panel_content", restored_panel_content)
    cl.user_session.set("citation_source_rows", restored_source_rows)
    cl.user_session.set("followup_questions", restored_followup_questions)
    cl.user_session.set("citation_history", restored_citation_history)
    cl.user_session.set("source_catalog", session_source_catalog)

    citation_panel_for_actions: str | None = restored_panel_content
    citation_source_rows_for_actions: list[dict[str, Any]] = _sanitize_source_rows_payload(restored_source_rows)
    if isinstance(restored_panel_content, str) and restored_panel_content.strip():
        panel_with_links = restored_panel_content
        if "/sources/pdf/" not in panel_with_links:
            panel_with_links = _append_source_links_to_panel(restored_panel_content, citation_source_rows_for_actions)
        cl.user_session.set("citation_panel_content", panel_with_links)
        citation_panel_for_actions = panel_with_links

    # Intentionally do NOT auto-open the citation sidebar on resume.
    # Chainlit's ElementSidebar.set_title / set_elements force-open the sidebar
    # as a side effect of populating it, which pops an unrequested panel every
    # time the user returns to a chat. The sidebar is still reachable on demand
    # via the per-step "Quellen anzeigen" action (restored below) and via the
    # inline cl.Pdf buttons on each cited source.

    if not latest_assistant_has_actions:
        await _restore_actions_for_step(
            latest_assistant_step_id,
            followup_questions=restored_followup_questions,
            has_citations_panel=bool(isinstance(citation_panel_for_actions, str) and citation_panel_for_actions.strip()),
            citation_panel_content=citation_panel_for_actions,
            citation_source_rows=citation_source_rows_for_actions,
        )

    # Restore per-step "Quellen anzeigen" actions on every HISTORICAL
    # assistant step that had a citation panel but no persisted actions.
    # The latest step is handled above with the full action set (including
    # followups). Followups are intentionally omitted for older steps —
    # clicking a followup from mid-history would inject it into the current
    # conversation in a confusing order.
    for entry in historical_citation_steps:
        if entry["step_id"] == latest_assistant_step_id:
            continue
        if entry["has_actions"]:
            continue
        await _restore_actions_for_step(
            entry["step_id"],
            followup_questions=[],
            has_citations_panel=True,
            citation_panel_content=entry["panel_content"],
            citation_source_rows=entry["source_rows"],
        )

    # Defensive: if anything in the restore path opened the side panel,
    # close it. Chainlit maps set_sidebar_elements([]) to setSideView(undefined)
    # on the frontend, so this is a safe idempotent no-op when already closed.
    await cl.ElementSidebar.set_elements([], key="citations_panel")


@cl.set_chat_profiles
async def set_chat_profiles():
    """Chat profiles are now managed via settings for persistence.
    
    We return an empty list to disable the startup profile selector.
    The profile can be changed in the chat settings (sidebar).
    """
    return []


def _build_full_system_prompt(
    chat_profile_config: dict[str, Any] | None = None,
    user_profile: UserProfile | None = None,
) -> str | None:
    """Build the complete system prompt from base + role context + personalization.

    Uses user's custom_prompt if set, otherwise falls back to the default.
    """
    # Start with custom prompt or default
    base = None
    if user_profile and user_profile.custom_prompt:
        base = user_profile.custom_prompt
    else:
        base = SYSTEM_PROMPT

    if not base:
        return None

    system_prompt = base

    # Add chat profile / role context
    if chat_profile_config:
        profile_prompt = chat_profile_config.get("prompt_context", "")
        if profile_prompt:
            system_prompt = f"{system_prompt}\n\n## ROLLENKONTEXT\n{profile_prompt}"

    # Add personalization context (keywords for "Bezug zu Ihren Interessen")
    if user_profile:
        personalization_context = _build_personalization_prompt(user_profile)
        if personalization_context:
            system_prompt = f"{system_prompt}\n\n{personalization_context}"

    return system_prompt


def _rebuild_system_prompt_in_session() -> None:
    """Rebuild the system prompt from current session state and update messages."""
    chat_profile_config = cl.user_session.get("chat_profile_config")
    user_profile = cl.user_session.get("user_profile")
    system_prompt = _build_full_system_prompt(chat_profile_config, user_profile)

    # A copy, not the session's list: `messages` is mutated in place below and there
    # is no try/except around chat(), so an oversized request would otherwise leave
    # the oversized history in the session and every later question would fail the
    # same way until reload. The only path that skips the set() at the end of main()
    # is an exception, so success is byte-identical and only failure is forgotten.
    messages = list(cl.user_session.get("messages") or [])
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = system_prompt or ""
    elif system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    cl.user_session.set("messages", messages)


def _chat_model_options() -> list[str]:
    """Chat-model ids for the selector: configured list ∪ gateway ∪ active model.

    Uses the warmed cache (see ``cached_chat_models``) so no blocking network
    call happens inside the async settings handlers. Embedding models the gateway
    also advertises are filtered out (they are not valid chat models)."""
    cfg = get_config()
    embed_model = (cfg.models.embed_model or "").lower()
    options: list[str] = []
    candidates = (
        list(cfg.models.selectable_chat_models)
        + cached_chat_models()
        + [_session_chat_model(), CHAT_MODEL]
    )
    for model in candidates:
        if not model or model in options:
            continue
        lowered = model.lower()
        if lowered == embed_model or "embed" in lowered:
            continue
        options.append(model)
    return options


def _session_chat_model() -> str | None:
    """Per-session chat model override picked in the settings panel (or None)."""
    return cl.user_session.get("chat_model") or None


def _model_is_vision_capable(model: str | None, cfg) -> bool:
    """Whether ``model`` may receive figure pixels in attach mode. The gateway
    exposes no capability flag, so ``images.vision_capable_models`` is authoritative."""
    lowered = (model or "").lower()
    return any(v.lower() in lowered or lowered in v.lower() for v in cfg.images.vision_capable_models)


def _collect_attach_figures(last_results: list[Any], cfg, *, limit: int) -> list[tuple[str, Any]]:
    """Up to ``limit`` (data_uri, RagResult) pairs for figure chunks present in
    ``last_results`` that have a resolvable stored image."""
    from kb.figure_store import figure_dir, file_to_data_uri, resolve_figure_path

    base = figure_dir(cfg)
    out: list[tuple[str, Any]] = []
    for result in last_results:
        metadata = getattr(result, "metadata", None) or {}
        if not metadata.get("is_figure"):
            continue
        image_path = metadata.get("image_path")
        resolved = resolve_figure_path(image_path, base) if isinstance(image_path, str) else None
        if resolved is None:
            continue
        out.append((file_to_data_uri(resolved, max_px=cfg.images.attach_image_max_px), result))
        if len(out) >= limit:
            break
    return out


def _figure_marker_system_message(results: Any) -> dict[str, str] | None:
    """Ephemeral per-request instruction teaching the ``{{ABB:...}}`` protocol.

    Returns None when the feature is off or no figure was retrieved — that saves
    tokens and stops the model inventing markers. Never edit the (regenerable,
    user-overridable) system prompt file for this."""
    cfg = get_config()
    if cfg.images.mode == "none" or not cfg.images.inline_figures:
        return None
    if not any((getattr(r, "metadata", None) or {}).get("is_figure") for r in (results or [])):
        return None
    return {"role": "system", "content": cfg.images.figure_marker_prompt}


def _build_chat_settings(
    current_profile: str | None = None,
    user_profile: UserProfile | None = None,
    chat_profile_config: dict[str, Any] | None = None,
):
    """Build ChatSettings with model + system-prompt controls, plus profile,
    personalization, and keyword widgets. Always returns a panel (the model and
    system-prompt controls are shown even when no chat profiles are configured)."""
    app_cfg = get_config().app
    if not app_cfg.show_settings:
        return None
    personalization_feature = app_cfg.personalization_enabled

    profiles = CHAT_PROFILES_CONFIG.get("profiles", [])
    profile_names = [p.get("name", "") for p in profiles if p.get("name")]

    # Resolve profile config if not passed
    if chat_profile_config is None and current_profile:
        chat_profile_config = _get_profile_by_name(current_profile)

    # Determine personalization state and keywords from profile
    personalization_on = True
    active_kw_values: list[str] = []
    if user_profile:
        personalization_on = user_profile.personalization_enabled
        active_kw_values = user_profile.active_keyword_values()

    # Determine current prompt text for the editor
    current_prompt = ""
    if user_profile and user_profile.custom_prompt:
        current_prompt = user_profile.custom_prompt
    elif SYSTEM_PROMPT:
        current_prompt = SYSTEM_PROMPT

    # Model selector (options: configured list ∪ gateway list ∪ active model)
    model_options = _chat_model_options()
    current_model = _session_chat_model() or CHAT_MODEL
    model_index = model_options.index(current_model) if current_model in model_options else 0

    widgets: list = [
        Select(
            id="chat_model",
            label="Chat-Modell",
            description="Modell für Antworten (nutzt den API-Key aus der .env).",
            values=model_options,
            initial_index=model_index,
        ),
    ]

    # Role selector only when chat profiles are configured for this instance.
    if profile_names:
        initial_index = 0
        if current_profile and current_profile in profile_names:
            initial_index = profile_names.index(current_profile)
        widgets.append(
            Select(
                id="chat_profile",
                label="Ihre Rolle",
                description="Wählen Sie Ihre Rolle für angepasste Antworten",
                values=profile_names,
                initial_index=initial_index,
            )
        )

    # Personalization / keyword controls — only when the feature is enabled for
    # this instance (app.personalization_enabled in the YAML).
    if personalization_feature:
        widgets.extend([
            Switch(
                id="personalization_enabled",
                label="Personalisierung aktivieren",
                description="Schlüsselwörter aus dem Chatverlauf in Antworten berücksichtigen",
                initial=personalization_on,
            ),
            Tags(
                id="active_keywords",
                label="Schlüsselwörter",
                description="Themen aus Ihrem Chatverlauf. Entfernen oder hinzufügen.",
                initial=active_kw_values,
            ),
            Select(
                id="regenerate_keywords",
                label="Schlüsselwörter-Aktion",
                description="Schlüsselwörter aus dem Chatverlauf neu extrahieren",
                values=["- Keine Aktion -", "Jetzt neu generieren"],
                initial_index=0,
            ),
        ])

    widgets.append(
        TextInput(
            id="system_prompt",
            label="System-Prompt (bearbeitbar)",
            description="Bearbeiten Sie den Basis-Prompt. Leer lassen = Standard-Prompt verwenden.",
            initial=current_prompt,
            placeholder="System-Prompt hier eingeben …",
            multiline=True,
        ),
    )

    # Add read-only ROLLENKONTEXT so the user sees what gets appended
    role_context = ""
    if chat_profile_config:
        ctx = chat_profile_config.get("prompt_context", "")
        if ctx:
            role_context = f"## ROLLENKONTEXT\n{ctx}"
    if personalization_feature and user_profile and user_profile.personalization_enabled:
        active_kws = user_profile.active_keyword_values()
        if active_kws:
            kw_section = f"## PERSONALISIERTER KONTEXT\nThemen: {', '.join(active_kws)}"
            role_context = f"{role_context}\n\n{kw_section}" if role_context else kw_section
    if role_context:
        widgets.append(
            TextInput(
                id="_readonly_context",
                label="Automatisch ergänzt (nicht bearbeitbar)",
                description="Wird dem Prompt je nach Rolle und Personalisierung hinzugefügt.",
                initial=role_context,
                multiline=True,
                disabled=True,
            ),
        )

    return cl.ChatSettings(widgets)


@cl.on_settings_update
async def on_settings_update(settings: dict[str, Any]):
    """Handle changes in settings: profile, personalization toggle, keywords."""
    user_id = cl.user_session.get("current_user_id")
    user_profile: UserProfile | None = cl.user_session.get("user_profile")
    changed_parts: list[str] = []

    # --- Handle chat model change (applied silently, no confirmation message) ---
    new_model = (settings.get("chat_model") or "").strip()
    if new_model and new_model != (_session_chat_model() or CHAT_MODEL):
        cl.user_session.set("chat_model", new_model)
        if user_id:
            set_user_selected_chat_model(CHAT_DB_PATH, user_id, new_model)

    # --- Handle chat profile change ---
    new_profile_name = settings.get("chat_profile")
    if new_profile_name:
        old_profile = cl.user_session.get("chat_profile")
        if new_profile_name != old_profile:
            if user_id:
                set_user_selected_chat_profile(CHAT_DB_PATH, user_id, new_profile_name)
            chat_profile_config = _get_profile_by_name(new_profile_name)
            cl.user_session.set("chat_profile", new_profile_name)
            cl.user_session.set("chat_profile_config", chat_profile_config)
            changed_parts.append(f"Rolle → **{new_profile_name}**")

    # --- Handle personalization toggle ---
    if "personalization_enabled" in settings:
        new_enabled = bool(settings["personalization_enabled"])
        if user_profile:
            user_profile.personalization_enabled = new_enabled
        else:
            user_profile = UserProfile(
                user_id=user_id or "anonymous",
                personalization_enabled=new_enabled,
            )
        cl.user_session.set("user_profile", user_profile)
        if user_id:
            upsert_user_profile(CHAT_DB_PATH, user_id, personalization_enabled=new_enabled)
        label = "aktiviert" if new_enabled else "deaktiviert"
        changed_parts.append(f"Personalisierung → **{label}**")

    # --- Handle keyword tags changes ---
    # Skip the reconcile pass when the user also requested a full regenerate in
    # the same settings update: regenerate_keywords rebuilds the whole list, so
    # the tag-diff would otherwise mark unrelated entries as "deactivated".
    regen_requested = settings.get("regenerate_keywords") == "Jetzt neu generieren"
    if "active_keywords" in settings and not regen_requested:
        new_tag_values: list[str] = settings.get("active_keywords") or []
        if user_profile is None:
            user_profile = UserProfile(user_id=user_id or "anonymous")

        # Determine what changed
        old_active = set(user_profile.active_keyword_values())
        new_active = set(new_tag_values)

        # Index existing keywords (incl. inactive) by normalized value so a
        # re-typed value reactivates the existing entry instead of creating a
        # duplicate manual one. Folds Unicode hyphen variants too.
        existing_by_lc = {
            _kw_key(k["value"]): k
            for k in user_profile.keywords
            if k.get("value")
        }

        # Tags added → reactivate existing or create manual keyword
        added = new_active - old_active
        for value in added:
            existing = existing_by_lc.get(_kw_key(value))
            if existing is not None:
                existing["active"] = True
            else:
                user_profile.keywords.append({"value": value, "active": True, "source": "manual"})

        # Tags removed → deactivate (normalized match)
        removed = old_active - new_active
        removed_lc = {_kw_key(v) for v in removed}
        for kw in user_profile.keywords:
            if kw.get("value") and _kw_key(kw["value"]) in removed_lc:
                kw["active"] = False

        # Re-embed if changes occurred
        if added or removed:
            user_profile = await update_keyword_embeddings(user_profile)
            cl.user_session.set("user_profile", user_profile)
            if added:
                changed_parts.append(f"Schlüsselwörter hinzugefügt: {', '.join(added)}")
            if removed:
                changed_parts.append(f"Schlüsselwörter deaktiviert: {', '.join(removed)}")

    # --- Handle keyword regeneration ---
    if settings.get("regenerate_keywords") == "Jetzt neu generieren" and user_id:
        try:
            updated_profile = await regenerate_keywords(user_id)
            user_profile = updated_profile
            cl.user_session.set("user_profile", user_profile)
            kw_list = user_profile.active_keyword_values()
            if kw_list:
                changed_parts.append(f"Schlüsselwörter neu generiert: {', '.join(kw_list)}")
            else:
                changed_parts.append("Keine Schlüsselwörter gefunden")
        except Exception as exc:
            print(f"[ERROR] regenerate_keywords in settings: {exc}")
            changed_parts.append(f"Fehler beim Generieren: {exc}")

    # --- Handle system prompt editing ---
    if "system_prompt" in settings:
        new_prompt_text = (settings.get("system_prompt") or "").strip()
        if user_profile is None:
            user_profile = UserProfile(user_id=user_id or "anonymous")

        if not new_prompt_text or new_prompt_text == (SYSTEM_PROMPT or "").strip():
            # Empty or identical to default → reset to default. Capture the prior
            # state BEFORE clearing so the confirmation reflects an actual reset.
            had_custom = user_profile.custom_prompt is not None
            user_profile.custom_prompt = None
            if user_id:
                upsert_user_profile(CHAT_DB_PATH, user_id, custom_prompt=None)
            if had_custom or new_prompt_text == "":
                changed_parts.append("System-Prompt → **Standard**")
        else:
            old_custom = user_profile.custom_prompt
            if new_prompt_text != (old_custom or "").strip():
                user_profile.custom_prompt = new_prompt_text
                if user_id:
                    upsert_user_profile(CHAT_DB_PATH, user_id, custom_prompt=new_prompt_text)
                changed_parts.append("System-Prompt → **benutzerdefiniert**")
        cl.user_session.set("user_profile", user_profile)

    # Rebuild system prompt with all changes
    _rebuild_system_prompt_in_session()

    # Refresh settings panel so the read-only context reflects changes
    refreshed = _build_chat_settings(
        cl.user_session.get("chat_profile"),
        cl.user_session.get("user_profile"),
        cl.user_session.get("chat_profile_config"),
    )
    if refreshed:
        await refreshed.send()

    if changed_parts:
        summary = "\n".join(f"- {p}" for p in changed_parts)
        await cl.Message(
            content=f"Einstellungen aktualisiert:\n{summary}",
            author="System",
        ).send()


@cl.on_chat_start
async def on_chat_start():
    existing_session_id = cl.user_session.get("chat_history_session_id")
    resume_session_id = existing_session_id if isinstance(existing_session_id, str) and existing_session_id.strip() else None
    # Use Chainlit's own thread_id (set by the websocket session before on_chat_start
    # fires and reused by on_chat_resume as thread["id"]) so our SQLite session_id
    # stays aligned with Chainlit's Postgres thread_id. Otherwise a fresh uuid4()
    # here would orphan our source_catalog whenever the user leaves and returns
    # to the chat — resume would look up an empty row and restart numbering at 1.
    session_id = resume_session_id or cl.context.session.thread_id
    resumed_session = resume_session_id is not None

    # Get authenticated user ID if available
    # Chainlit stores user in session after auth callback
    user = cl.user_session.get("user")
    user_id = None
    if user:
        # Try different attribute names Chainlit might use
        user_id = getattr(user, "identifier", None) or getattr(user, "id", None)

    # Load persisted chat profile for authenticated users (persistent across sessions)
    chat_profile_name = None
    if user_id:
        chat_profile_name = get_user_selected_chat_profile(CHAT_DB_PATH, user_id)
    
    # Fall back to default profile if none persisted
    if not chat_profile_name:
        chat_profile_name = CHAT_PROFILES_CONFIG.get("default_profile")
        # Find the profile name for the default_profile id
        if chat_profile_name:
            for p in CHAT_PROFILES_CONFIG.get("profiles", []):
                if p.get("id") == chat_profile_name:
                    chat_profile_name = p.get("name")
                    break
    
    chat_profile_config = _get_profile_by_name(chat_profile_name) if chat_profile_name else None
    cl.user_session.set("chat_profile", chat_profile_name)
    cl.user_session.set("chat_profile_config", chat_profile_config)

    # Restore the user's persisted chat-model selection so it survives new chats.
    if user_id:
        saved_model = get_user_selected_chat_model(CHAT_DB_PATH, user_id)
        if saved_model:
            cl.user_session.set("chat_model", saved_model)

    print(
        f"[DEBUG] on_chat_start: user={user}, user_id={user_id}, chat_profile={chat_profile_name}, "
        f"resumed_session={resumed_session}, session_id={session_id}"
    )

    create_chat_session(
        CHAT_DB_PATH,
        session_id,
        user_id=user_id,
        metadata={
            "system_prompt_loaded": bool(SYSTEM_PROMPT),
            "chat_profile": chat_profile_name,
            "source_catalog": _empty_source_catalog(),
        },
    )
    cl.user_session.set("chat_history_session_id", session_id)
    cl.user_session.set("current_user_id", user_id)
    cl.user_session.set("source_catalog", _load_session_source_catalog(session_id))

    # Load or initialize user profile for personalization (only if the feature
    # is enabled for this instance).
    user_profile: UserProfile | None = None
    if user_id and get_config().app.personalization_enabled:
        user_profile = await load_user_profile(user_id)
        if user_profile and user_profile.has_sufficient_history():
            print(f"[DEBUG] on_chat_start: loaded profile for {user_id}, topics={user_profile.topics}")
        else:
            # Check if user has enough messages to generate profile
            msg_count = get_user_message_count(CHAT_DB_PATH, user_id)
            if msg_count >= PROFILE_MIN_MESSAGES:
                print(f"[DEBUG] on_chat_start: generating profile for {user_id}, msg_count={msg_count}")
                user_profile = await update_user_profile(user_id)
    cl.user_session.set("user_profile", user_profile)

    # Build system prompt with chat profile context and personalization
    system_prompt = _build_full_system_prompt(chat_profile_config, user_profile)

    existing_messages = cl.user_session.get("messages")
    messages: list[dict[str, Any]]
    if resumed_session and isinstance(existing_messages, list) and existing_messages:
        messages = existing_messages
        if system_prompt:
            if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                messages[0]["content"] = system_prompt
            else:
                messages.insert(0, {"role": "system", "content": system_prompt})
    else:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            add_chat_message(CHAT_DB_PATH, session_id, "system", system_prompt)
    cl.user_session.set("messages", messages)

    # Send chat settings with profile selector, personalization toggle, and keywords
    chat_settings = _build_chat_settings(chat_profile_name, user_profile, chat_profile_config)
    if chat_settings:
        await chat_settings.send()


@cl.action_callback("regenerate_keywords")
async def regenerate_keywords_action(action: cl.Action):
    """Regenerate keywords from the user's chat history."""
    user_id = cl.user_session.get("current_user_id")
    if not user_id:
        await cl.Message(content="Schlüsselwörter können nur für angemeldete Nutzer generiert werden.", author="System").send()
        return
    await cl.Message(content="Schlüsselwörter werden aus dem Chatverlauf neu generiert …", author="System").send()
    try:
        updated_profile = await regenerate_keywords(user_id)
        cl.user_session.set("user_profile", updated_profile)
        _rebuild_system_prompt_in_session()
        kw_list = updated_profile.active_keyword_values()
        if kw_list:
            kw_str = ", ".join(kw_list)
            await cl.Message(content=f"Schlüsselwörter aktualisiert: {kw_str}", author="System").send()
        else:
            await cl.Message(content="Keine Schlüsselwörter gefunden. Führen Sie zunächst einige Gespräche.", author="System").send()
        # Refresh settings panel
        chat_settings = _build_chat_settings(cl.user_session.get("chat_profile"), updated_profile, cl.user_session.get("chat_profile_config"))
        if chat_settings:
            await chat_settings.send()
    except Exception as exc:
        print(f"[ERROR] regenerate_keywords_action: {exc}")
        await cl.Message(content=f"Fehler beim Generieren: {exc}", author="System").send()


@cl.set_starters
async def set_starters(user=None, language: str | None = None) -> list[Starter]:
    """Welcome-screen suggestions, in the language the rest of the screen is in.

    Chainlit passes the resolved interface language here — the browser's, or
    ``[UI] language`` where an instance forces one. Both parameters are positional
    as far as Chainlit is concerned (it zips them onto the signature), so the names
    are ours; ``user`` is unused but has to be first.
    """
    starter_icons = [
        "/public/icons/shield.svg",
        "/public/icons/search.svg",
        "/public/icons/book.svg",
    ]
    starters: list[Starter] = []
    for i, q in enumerate(starter_questions(language)[:6]):
        starters.append(
            Starter(
                label=q if len(q) <= 70 else q[:67].rstrip() + "...",
                message=q,
                icon=starter_icons[i % len(starter_icons)],
            )
        )
    return starters


@cl.action_callback("open_source_pdf")
async def open_source_pdf(action: cl.Action):
    file_name = action.payload.get("file")
    page = action.payload.get("page")
    if not isinstance(file_name, str):
        return
    file_path = _resolve_source_pdf_path(file_name)
    if file_path is None:
        await cl.Message(content=f"Datei nicht gefunden: {file_name}").send()
        return

    pdf_name = f"{file_name} (S.{page})" if isinstance(page, int) else file_name
    element = cl.Pdf(name=pdf_name, url=_source_pdf_url(file_name), page=page if isinstance(page, int) else 1, display="side")
    await cl.Message(content=f"Quelle geöffnet: {pdf_name}", elements=[element]).send()


@cl.action_callback("open_all_citations")
async def open_all_citations(action: cl.Action):
    payload = action.payload if isinstance(action.payload, dict) else {}
    show_history = bool(payload.get("show_history"))
    payload_panel_content = payload.get("citation_panel_content")
    payload_source_rows = payload.get("citation_source_rows")

    latest_panel_content = (
        payload_panel_content
        if isinstance(payload_panel_content, str) and payload_panel_content.strip()
        else cl.user_session.get("citation_panel_content")
    )
    latest_source_rows = _sanitize_source_rows_payload(payload_source_rows)
    if not latest_source_rows:
        latest_source_rows = _sanitize_source_rows_payload(cl.user_session.get("citation_source_rows"))

    panel_content: str | None = latest_panel_content if isinstance(latest_panel_content, str) else None
    source_rows: list[dict[str, Any]] = latest_source_rows
    sidebar_title = CITATION_SIDEBAR_TITLE
    if show_history:
        history_panel_content, history_rows = _build_citation_history_view(
            _sanitize_citation_history(cl.user_session.get("citation_history"))
        )
        if isinstance(history_panel_content, str) and history_panel_content.strip():
            panel_content = history_panel_content
            source_rows = history_rows
            sidebar_title = CITATION_HISTORY_SIDEBAR_TITLE

    if not isinstance(panel_content, str) or not panel_content.strip():
        await cl.Message(content="Keine Zitierungen verfügbar.").send()
        return

    panel_content_with_links = panel_content
    if "/sources/pdf/" not in panel_content_with_links:
        panel_content_with_links = _append_source_links_to_panel(panel_content, source_rows)
    if sidebar_title == CITATION_SIDEBAR_TITLE:
        cl.user_session.set("citation_panel_content", panel_content)
        cl.user_session.set("citation_source_rows", source_rows)

    await _show_citation_sidebar(
        panel_content_with_links,
        source_rows,
        sidebar_title=sidebar_title,
    )


@cl.action_callback("ask_followup")
async def ask_followup(action: cl.Action):
    payload = action.payload if isinstance(action.payload, dict) else {}
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return
    user_msg = cl.Message(content=question, author="You", type="user_message")
    await user_msg.send()
    # Wrap main() in a Step(type='run', ...) so the resulting assistant
    # message is attached to a scorable run, matching what @cl.on_message
    # does internally. Without this, Chainlit's frontend gates copy +
    # thumbs on the absent `scorableRun` and hides them on the new answer.
    async with cl.Step(name="on_message", type="run", parent_id=user_msg.id) as run_step:
        run_step.input = question
        await main(cl.Message(content=question))


@cl.on_message
async def main(message: cl.Message):
    if await _handle_control_message(message):
        return

    messages = cl.user_session.get("messages") or []
    session_id = _current_chat_session_id()
    if not session_id:
        # Defensive fallback — should normally already be set by on_chat_start or
        # on_chat_resume. Use Chainlit's thread_id so we don't orphan the catalog.
        session_id = cl.context.session.thread_id
        create_chat_session(CHAT_DB_PATH, session_id)
        cl.user_session.set("chat_history_session_id", session_id)

    messages.append({"role": "user", "content": message.content})
    add_chat_message(CHAT_DB_PATH, session_id, "user", message.content)
    set_session_title_if_missing(CHAT_DB_PATH, session_id, _first_sentence(message.content, max_len=96))

    response = await chat(messages, tools=TOOLS, tool_choice="required", model=_session_chat_model())
    assistant_msg = response.choices[0].message
    print(
        "[DEBUG] first_call",
        "content_empty=",
        not bool(assistant_msg.content),
        "tool_calls=",
        bool(getattr(assistant_msg, "tool_calls", None)),
    )

    if not getattr(assistant_msg, "tool_calls", None):
        print("[WARN] first_call_without_tool_retrying")
        retry_messages = [
            *messages,
            {
                "role": "system",
                "content": get_config().ui_text.retry_tool.format(tool=TOOL_NAME),
            },
        ]
        retry_response = await chat(retry_messages, tools=TOOLS, tool_choice="required", model=_session_chat_model())
        retry_msg = retry_response.choices[0].message
        print(
            "[DEBUG] first_call_retry",
            "content_empty=",
            not bool(retry_msg.content),
            "tool_calls=",
            bool(getattr(retry_msg, "tool_calls", None)),
        )
        if getattr(retry_msg, "tool_calls", None):
            assistant_msg = retry_msg

    if getattr(assistant_msg, "tool_calls", None):
        citations_text: str | None = None
        last_results = []
        content = ""
        current_msg = assistant_msg
        aggregated_by_key: dict[tuple[str, int | None, str], Any] = {}
        cached_tool_payloads: dict[str, tuple[list[Any], dict[str, Any]]] = {}

        max_tool_rounds_raw = os.getenv("MAX_TOOL_CALL_ROUNDS", "12")
        try:
            max_tool_rounds = max(1, int(max_tool_rounds_raw))
        except ValueError:
            max_tool_rounds = 12
        tool_round = 0

        while getattr(current_msg, "tool_calls", None) and tool_round < max_tool_rounds:
            tool_round += 1
            messages.append(message_to_dict(current_msg))
            print(
                "[DEBUG] tool_round_start",
                "round=",
                tool_round,
                "tool_calls=",
                len(current_msg.tool_calls),
            )
            for tool_call in current_msg.tool_calls:
                function_name = getattr(getattr(tool_call, "function", None), "name", "")
                tool = TOOL_BY_FUNCTION_NAME.get(function_name)
                if tool is None:
                    tool_payload = {"error": f"Unsupported tool: {function_name}"}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(tool_payload, ensure_ascii=False),
                        }
                    )
                    continue

                args = json.loads(tool_call.function.arguments or "{}")
                signature = f"{function_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
                if signature in cached_tool_payloads:
                    results, tool_payload = cached_tool_payloads[signature]
                    with cl.Step(name=function_name, type="tool") as step:
                        step.input = {**args, "cached": True}
                        step.output = {"hits": len(results), "cached": True}
                else:
                    cfg = get_config()
                    ctx = ToolContext(
                        query_fallback=message.content or "",
                        filters=_active_retrieval_filters(),
                        default_top_k=TOP_K,
                        max_top_k=MAX_TOP_K,
                        collection=None,
                        language=cfg.language,
                        fetch_max_chunks=cfg.tools.fetch_max_chunks,
                        expand_window=cfg.tools.expand_window,
                    )
                    with cl.Step(name=function_name, type="tool") as step:
                        step.input = args
                        tool_result = await tool.handler(args, ctx)
                        results = tool_result.results
                        tool_payload = tool_result.payload
                        step.output = tool_result.step_output or {"hits": len(results)}
                    print(
                        "[DEBUG] tool_call",
                        "name=",
                        function_name,
                        "hits=",
                        len(results),
                    )
                    cached_tool_payloads[signature] = (results, tool_payload)

                for item in results:
                    # First writer wins. The score comparison this replaces preferred
                    # fetch_document's placeholder 1.0 over a real similarity, so it
                    # swapped in that tool's longer 4000-char copy of the same chunk.
                    aggregated_by_key.setdefault(_result_key(item), item)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_payload, ensure_ascii=False),
                    }
                )
                add_chat_message(
                    CHAT_DB_PATH,
                    session_id,
                    "tool",
                    json.dumps(tool_payload, ensure_ascii=False),
                    metadata={"tool_name": function_name},
                )

            # This in-loop call also produces the FINAL answer (the loop exits when
            # it returns no tool_calls), so the marker instruction must ride along.
            # Throwaway list — never mutate the session `messages`.
            _fig_hint = _figure_marker_system_message(aggregated_by_key.values())
            _loop_messages = [*messages, _fig_hint] if _fig_hint else messages
            followup = await chat(
                _loop_messages, tools=TOOLS, tool_choice="auto", model=_session_chat_model()
            )
            current_msg = followup.choices[0].message
            print(
                "[DEBUG] tool_round_followup",
                "round=",
                tool_round,
                "content_empty=",
                not bool(current_msg.content),
                "tool_calls=",
                bool(getattr(current_msg, "tool_calls", None)),
            )

        # Deliberately unsorted. `score` is not comparable across tools: search
        # returns a similarity (or a fused rank under retrieval.hybrid), while
        # fetch_document and expand_context did no relevance matching at all and
        # report a placeholder 1.0 — which used to sort a journal header above the
        # passage that answered the question, and past the max_source_links cut.
        # Insertion order is meaningful instead: dicts preserve it and retrieve()
        # appends in Qdrant's relevance order, so search hits stay ranked and land
        # ahead of a whole-document dump. Trade-off: two separate search calls group
        # by call rather than interleaving by similarity.
        last_results = list(aggregated_by_key.values())

        # attach mode: if figures are retrieved and the active chat model can see
        # images, produce the final answer with a multimodal vision pass (mirrors
        # the safety-stop block below, plus image_url parts).
        vision_answer: str | None = None
        _img_cfg = get_config()
        if _img_cfg.images.mode == "attach":
            active_model = _session_chat_model() or CHAT_MODEL
            attach_figures = _collect_attach_figures(
                last_results, _img_cfg, limit=_img_cfg.images.max_attach_images
            )
            if attach_figures and _model_is_vision_capable(active_model, _img_cfg):
                vision_context, _, _ = render_context(last_results[: max(TOP_K, 8)])
                user_parts: list[dict[str, Any]] = [
                    {
                        "type": "text",
                        "text": (
                            f"Frage: {message.content}\n\n"
                            f"Kontext:\n{vision_context}\n\n"
                            "Nutze auch die beigefügten Abbildungen zur Beantwortung. "
                            "Antworte auf Deutsch mit Quellenhinweisen [1], [2], ..."
                        ),
                    }
                ]
                for data_uri, _ in attach_figures:
                    user_parts.append({"type": "image_url", "image_url": {"url": data_uri}})
                _fig_hint = _figure_marker_system_message(last_results)
                vision_messages = [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "Erstelle jetzt die finale Antwort aus Kontext und Abbildungen. "
                            "Keine weiteren Tool-Aufrufe."
                        ),
                    },
                    *([_fig_hint] if _fig_hint else []),
                    {"role": "user", "content": user_parts},
                ]
                vision_final = await chat(vision_messages, model=active_model)
                vision_answer = vision_final.choices[0].message.content or ""
                print("[DEBUG] attach_vision_pass", "figures=", len(attach_figures), "model=", active_model)
            elif attach_figures:
                print(
                    f"[WARN] images.mode=attach but active model '{active_model}' is not "
                    "vision-capable; falling back to the text answer."
                )

        if vision_answer is not None and vision_answer.strip():
            content = vision_answer
        elif getattr(current_msg, "tool_calls", None):
            # Safety stop: avoid endless tool loops, force final answer from collected context.
            print(
                "[WARN] tool_round_limit_reached",
                "max_tool_rounds=",
                max_tool_rounds,
                "aggregated_hits=",
                len(last_results),
            )
            final_context, _, _ = render_context(last_results[: max(TOP_K, 8)])
            _fig_hint = _figure_marker_system_message(last_results)
            forced_messages = [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "Erstelle jetzt die finale Antwort ausschließlich aus dem Kontext. "
                        "Keine weiteren Tool-Aufrufe."
                    ),
                },
                *([_fig_hint] if _fig_hint else []),
                {
                    "role": "user",
                    "content": (
                        f"Frage: {message.content}\n\n"
                        f"Kontext:\n{final_context}\n\n"
                        "Antworte auf Deutsch mit Quellenhinweisen [1], [2], ..."
                    ),
                },
            ]
            forced_final = await chat(forced_messages, model=_session_chat_model())
            forced_final_msg = forced_final.choices[0].message
            content = forced_final_msg.content or ""
        else:
            content = current_msg.content or ""

        if not content.strip():
            if last_results:
                content = _extractive_answer_from_results(message.content, last_results)
            else:
                content = "Im bereitgestellten Kontext nicht enthalten"

        content = _strip_model_source_blocks(content)
        # Canonicalize figure markers BEFORE _inject_named_source_refs below, which
        # would otherwise swallow a bracket-form marker as a named source reference.
        content = normalize_figure_markers(content)

        # Attach source PDFs as endpoint URLs to avoid session-scoped file copies.
        session_source_catalog = _sanitize_source_catalog(cl.user_session.get("source_catalog"))
        if not session_source_catalog.get("entries"):
            session_source_catalog = _load_session_source_catalog(session_id)
        source_catalog_changed = False
        # Keep the catalog compact: drop IDs not referenced by persisted citation history.
        if _prune_source_catalog(
            session_source_catalog,
            _source_ids_from_citation_history(cl.user_session.get("citation_history")),
        ):
            source_catalog_changed = True
        cl.user_session.set("source_catalog", session_source_catalog)
        seen_links: set[tuple[str, int | None]] = set()
        source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]] = []
        alias_by_index: dict[int, str] = {}
        url_by_index: dict[int, str] = {}
        source_rows_for_session: list[dict[str, Any]] = []
        alias_to_url: dict[str, str] = {}
        desired_sources = _desired_source_count(content, len(last_results))
        if MAX_SOURCE_LINKS > 0:
            desired_sources = min(desired_sources, MAX_SOURCE_LINKS)
        # Warn once per file, not once per chunk: a missing document usually supplies
        # several of the retrieved chunks.
        unlinkable_files: set[str] = set()
        # The alias number IS the retrieval index, not a separate running counter.
        # The model cites by the number it was shown, which is the position in the
        # tool payload's `citations` list — i.e. the position in last_results. A
        # second counter that only advanced for *linkable* sources drifted from it
        # the moment a chunk had no resolvable PDF: the answer said "Quelle 5" while
        # the alias said "Quelle 4", so _normalize_source_alias_mentions looked up a
        # number no alias carried and left the citation as plain text.
        #
        # Consequence, accepted deliberately: the panel can show gaps (Quelle 2, 3,
        # 5) when a retrieved chunk cannot be linked. A number that matches what the
        # model read is worth more than a consecutive one — and a gap is a true
        # statement about the context.
        for idx, result in enumerate(last_results, start=1):
            file_name = extract_source_file(result.metadata)
            if not file_name:
                continue
            page = extract_page(result.metadata)
            key = (file_name, page)
            if key in seen_links:
                existing_alias = next((alias for _, alias, fname, pstart, _, _, _ in source_rows if fname == file_name and pstart == page), None)
                if existing_alias:
                    alias_by_index[idx] = existing_alias
                    existing_url = alias_to_url.get(existing_alias)
                    if isinstance(existing_url, str) and existing_url:
                        url_by_index[idx] = existing_url
                continue
            file_path = _resolve_source_pdf_path(file_name)
            if file_path is None and file_name not in unlinkable_files:
                unlinkable_files.add(file_name)
                # A retrieved chunk whose file is not on disk gets no alias, so any
                # citation the model writes for it stays plain text — silently. That
                # is worth saying out loud: it means the index and the document
                # folder disagree, which no check reports today.
                print(
                    f"[WARN] citation_unlinkable source_file={file_name!r} "
                    f"cited_as=Quelle {idx} — file not found in sources.data_dir or any "
                    f"data_sources[].path, "
                    f"so this citation cannot be made clickable"
                )
            if file_path is not None:
                page_end = result.metadata.get("page_end") if isinstance(result.metadata.get("page_end"), int) else None
                section_title = _resolve_section_title(result.metadata)
                page_start = extract_page(result.metadata)
                alias = _source_alias(idx, section_title, page_start, page_end)
                pdf_url = _source_pdf_url(file_name)
                if isinstance(page, int):
                    pdf_url = f"{pdf_url}#page={page}"
                evidence_snippet = _first_sentence(result.text)
                alias_by_index[idx] = alias
                url_by_index[idx] = pdf_url
                alias_to_url[alias] = pdf_url
                source_rows.append(
                    (
                        idx,
                        alias,
                        file_name,
                        page_start,
                        page_end,
                        section_title,
                        evidence_snippet,
                    )
                )
                source_rows_for_session.append(
                    {
                        "alias": alias,
                        "file": file_name,
                        "page": page,
                        "page_start": page_start if isinstance(page_start, int) else None,
                        "page_end": page_end if isinstance(page_end, int) else None,
                        "section": section_title if isinstance(section_title, str) else None,
                        "evidence": evidence_snippet if isinstance(evidence_snippet, str) else None,
                    }
                )
                seen_links.add(key)
            if desired_sources and len(seen_links) >= desired_sources:
                break

        alias_by_number = _alias_number_map(source_rows)
        url_by_number: dict[int, str] = {}
        for src_idx, alias, *_ in source_rows:
            alias_url = alias_to_url.get(alias)
            if isinstance(alias_url, str) and alias_url:
                if isinstance(src_idx, int):
                    url_by_number[src_idx] = alias_url
                number_match = re.match(r"^\s*Quelle\s+(\d+)\s*:", alias, flags=re.IGNORECASE)
                if number_match:
                    url_by_number[int(number_match.group(1))] = alias_url

        alias_by_ref = {**alias_by_number, **alias_by_index}
        url_by_ref = {**url_by_number, **url_by_index}

        # Make in-text citations clickable (supports [1], [1†...], 【1†...】).
        content = _inject_clickable_refs(
            content,
            alias_by_index,
            alias_by_ref,
            url_by_index,
            url_by_ref,
        )
        # Also map named refs like [standard_200_2.pdf, S. 2] to known source aliases.
        content = _inject_named_source_refs(content, source_rows)
        # Link explicit alias mentions like "Quelle 3: ... (S.312-313)" early,
        # before normalization potentially removes the numeric anchor.
        content = _inject_source_alias_links(content, alias_by_ref, url_by_ref)
        # Normalize model-written "Quelle n: ..." strings to exact alias values.
        content = _normalize_source_alias_mentions(content, alias_by_index, alias_by_ref)
        # Fallback: if model index does not match retrieved order, map by title/page similarity.
        content = _normalize_source_mentions_by_content(content, source_rows)
        # Repair model outputs like: "Quelle 1: ... (S.30)(/sources/pdf/...)" to markdown links.
        content = _inject_naked_source_links(content)

        cited_aliases = set()
        for _, alias, *_ in source_rows:
            if not isinstance(alias, str) or not alias:
                continue
            escaped_alias = alias.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            if alias in content or escaped_alias in content:
                cited_aliases.add(alias)
        if cited_aliases:
            source_rows = [row for row in source_rows if row[1] in cited_aliases]
            source_rows_for_session = [
                row
                for row in source_rows_for_session
                if isinstance(row.get("alias"), str) and row["alias"] in cited_aliases
            ]

        # Assign persistent IDs only for sources that remain in the final assistant message.
        resolved_source_rows: list[tuple[int, str, str, int | None, int | None, str | None, str]] = []
        resolved_source_rows_for_session: list[dict[str, Any]] = []
        for row, session_row in zip(source_rows, source_rows_for_session):
            _, alias, file_name, page_start, page_end, section_title, evidence = row
            source_id, _, catalog_changed = _register_source_in_catalog(
                session_source_catalog,
                file_name=file_name,
                page_start=page_start if isinstance(page_start, int) else None,
                page_end=page_end if isinstance(page_end, int) else None,
                section_title=section_title if isinstance(section_title, str) else None,
            )
            if catalog_changed:
                source_catalog_changed = True
            resolved_source_rows.append(
                (
                    source_id,
                    alias,
                    file_name,
                    page_start,
                    page_end,
                    section_title,
                    evidence,
                )
            )
            updated_session_row = dict(session_row)
            updated_session_row["source_id"] = source_id
            resolved_source_rows_for_session.append(updated_session_row)

        source_rows = resolved_source_rows
        source_rows_for_session = resolved_source_rows_for_session
        content, source_rows, source_rows_for_session = _align_aliases_to_source_ids(
            content,
            source_rows,
            source_rows_for_session,
        )

        # Final canonicalization: every "Quelle ...(S.X)" span in the content is
        # rewritten to an exact element-name alias, and any adjacent stray **/__
        # decorators are stripped. Handles LLM deviations the upstream chain
        # cannot repair: orphan bold wrappers (e.g. "**Quelle 1: ... (S.X)" with
        # no closing "**") and the numberless form ("Quelle <Abschnitt> ... (S.X)").
        # Chainlit's frontend uses strict substring equality against element
        # names, so any residual divergence silently kills the click handler.
        # This pass is idempotent when the pipeline already agrees.
        content = _canonicalize_citations(content, source_rows)

        if source_catalog_changed:
            sanitized_catalog = _sanitize_source_catalog(session_source_catalog)
            cl.user_session.set("source_catalog", sanitized_catalog)
            _persist_session_source_catalog(session_id, sanitized_catalog)

        used_source_ids = sorted(
            {
                source_id
                for source_id, *_ in source_rows
                if isinstance(source_id, int) and source_id > 0
            }
        )

        # Final safety pass: ensure all plain "Quelle X: ..." aliases in answer text are clickable.
        content = _inject_alias_links_by_rows(content, source_rows_for_session)

        # Build a detailed source block for the citation panel.
        detail_block = ""
        if source_rows:
            box_lines = ["## Quellen & Belegstellen", ""]
            for visible_idx, (src_idx, alias, file_name, page_start, page_end, section_title, evidence) in enumerate(source_rows, start=1):
                page_label = _page_label(page_start, page_end)
                section_label = section_title or "Abschnitt unbekannt"
                pdf_url = _source_pdf_url(file_name)
                page_for_link = page_start if isinstance(page_start, int) else None
                if isinstance(page_for_link, int):
                    pdf_url = f"{pdf_url}#page={page_for_link}"
                box_lines.append(f"### {alias}")
                box_lines.append(f"- Datei: `{file_name}`")
                box_lines.append(f"- PDF: [Öffnen]({pdf_url})")
                if isinstance(src_idx, int) and src_idx > 0:
                    box_lines.append(f"- Quellen-ID: {src_idx}")
                else:
                    box_lines.append(f"- Quellen-ID: {visible_idx}")
                box_lines.append(f"- Seiten: {page_label}")
                box_lines.append(f"- Abschnitt: {section_label}")
                if evidence:
                    box_lines.append(f"- Belegsnippet: \"{evidence}\"")
                box_lines.append("")
            detail_block = "\n".join(box_lines)

        # Put only the detailed evidence list into a separate side panel.
        citation_panel_content = detail_block
        if citation_panel_content:
            cl.user_session.set("citation_panel_content", citation_panel_content)
            cl.user_session.set("citation_source_rows", source_rows_for_session)
            citation_history = _sanitize_citation_history(cl.user_session.get("citation_history"))
            citation_history = _append_citation_history(
                citation_history,
                citation_panel_content,
                source_rows_for_session,
            )
            cl.user_session.set("citation_history", citation_history)
        else:
            cl.user_session.set("citation_panel_content", None)
            cl.user_session.set("citation_source_rows", [])

        content, followups = _extract_followups(content)
        followup_questions = _sanitize_followup_questions(followups)
        cl.user_session.set("followup_questions", followup_questions)

        # --- inline figures: marked figures become images above their paragraph ---
        # render_content -> screen + Chainlit's data layer (so images survive a
        # reload, which cl.Image elements do not). `content` -> LLM history + export
        # DB, kept marker- and image-free so the model never imitates ![](...).
        _img_inline_cfg = get_config().images
        inlined_figures: list[Any] = []
        render_content = content
        if _img_inline_cfg.inline_figures and _img_inline_cfg.mode != "none":
            try:
                render_content, inlined_figures = render_figure_markers(
                    render_content,
                    build_figure_candidates(last_results),
                    with_caption=_img_inline_cfg.inline_figure_caption,
                )
            except Exception as exc:  # noqa: BLE001 — never lose an answer over a figure
                print(f"[WARN] inline_figures_failed: {exc.__class__.__name__}: {exc}")
                render_content, inlined_figures = content, []
        # Unresolved markers must never reach the user, the DB, or the model.
        render_content = strip_figure_markers(render_content)
        content = strip_figure_markers(content)
        inlined_image_paths = {c.image_path for c in inlined_figures}
        inlined_figure_names = {c.display_name for c in inlined_figures}
        if inlined_figures:
            print("[DEBUG] inline_figures=", len(inlined_figures))

        message_metadata: dict[str, Any] = {
            "has_citations_panel": bool(citation_panel_content),
            "followup_count": len(followup_questions),
            "followup_questions": followup_questions,
            "used_source_ids": used_source_ids,
        }
        if citation_panel_content:
            message_metadata["citation_panel_content"] = citation_panel_content
            message_metadata["citation_source_rows"] = _sanitize_source_rows_payload(source_rows_for_session)

        assistant_reply = cl.Message(
            content=render_content,
            metadata=message_metadata,
        )
        actions = _build_chat_actions(
            followup_questions=followup_questions,
            has_citations_panel=bool(citation_panel_content),
            source_step_id=assistant_reply.id,
            citation_panel_content=citation_panel_content,
            citation_source_rows=source_rows_for_session,
        )
        assistant_reply.actions = actions
        print("[DEBUG] followup_actions=", len(followup_questions), "total_actions=", len(actions))
        if citation_panel_content:
            _cache_citation_panel_content(assistant_reply.id, citation_panel_content)
            panel_elements = _build_citation_elements(
                citation_panel_content,
                source_rows_for_session,
                citation_step_id=assistant_reply.id,
            )
            assistant_reply.elements = panel_elements

        # Attach one inline cl.Pdf element per cited source so clicking the alias text
        # in the message body opens the PDF in the right side panel instead of a new tab.
        inline_pdf_elements = _build_inline_pdf_elements(source_rows_for_session)
        if inline_pdf_elements:
            assistant_reply.elements = (assistant_reply.elements or []) + inline_pdf_elements

        # Thumbnails below the message for retrieved figures that were NOT inlined
        # into the answer text (opt out via images.show_unmarked_figures: false).
        if _img_inline_cfg.show_unmarked_figures:
            figure_elements = _build_inline_figure_elements(
                last_results,
                exclude_image_paths=inlined_image_paths,
                exclude_names=inlined_figure_names,
            )
            if figure_elements:
                assistant_reply.elements = (assistant_reply.elements or []) + figure_elements

        await assistant_reply.send()
        if citation_panel_content:
            history_panel_content, history_rows = _build_citation_history_view(
                _sanitize_citation_history(cl.user_session.get("citation_history"))
            )
            use_history_sidebar = isinstance(history_panel_content, str) and history_panel_content.strip()
            sidebar_content = (
                history_panel_content
                if use_history_sidebar
                else citation_panel_content
            )
            sidebar_rows = history_rows if use_history_sidebar else source_rows_for_session
            if "/sources/pdf/" not in sidebar_content:
                sidebar_content = _append_source_links_to_panel(sidebar_content, sidebar_rows)
            await _show_citation_sidebar(
                sidebar_content,
                sidebar_rows,
                sidebar_title=(
                    CITATION_HISTORY_SIDEBAR_TITLE if use_history_sidebar else CITATION_SIDEBAR_TITLE
                ),
            )
        messages.append({"role": "assistant", "content": content})
        add_chat_message(
            CHAT_DB_PATH,
            session_id,
            "assistant",
            content,
            metadata=message_metadata,
        )

        # Answer-quality scoring (off unless evaluation.enabled). Detached on purpose:
        # a judge takes tens of seconds, and awaiting it here would leave the session
        # busy and unable to take the next question for the whole time. Nothing in the
        # UI is waiting on the result either — the badge above the chatbox polls
        # /eval-status, so a score that lands after the socket closed still counts and
        # still shows up on the next page load.
        #
        # This deliberately does NOT touch the sent message. Appending to it from here
        # was the original design and it never worked: Message.update() emits over the
        # session websocket, and by the time a ~16s judge returns the handler is gone
        # and the emit silently goes nowhere.
        #
        # The sibling branch below retrieves nothing, so it has no chunks to check an
        # answer against and is deliberately left alone.
        if get_config().evaluation.enabled:
            # Read here, not inside the task: cl.user_session is context-local, and by
            # the time a detached judge runs there is no session to ask. Without it the
            # score is filed under the *configured* model rather than the one the user
            # switched to in the settings panel — a Gemma answer landing in the
            # gpt-oss-120b row, which is the dashboard's own grouping key.
            answered_by = _session_chat_model()
            # The reference must be held until the task finishes: asyncio keeps only a
            # weak one, so a bare create_task() can be collected mid-flight — the same
            # trap the document watcher documents at its own create_task above. Dropped
            # it here first, and the symptom was silence: no scores, no request reaching
            # the service, and nothing in the log.
            async def _score_and_forget() -> None:
                try:
                    await post_score(
                        question=message.content,
                        answer=content,
                        # Labelled with the aliases the answer actually cites, not
                        # just the source line: `content` here is post-processed, so
                        # it says "Quelle 1: Methods (S.7-8)" where the context says
                        # "Source: Methods — file.pdf — p. 7". Same fact, different
                        # vocabulary, and the judge marked the citation unsupported.
                        contexts=_judge_contexts(last_results, source_rows),
                        thread_id=session_id,
                        message_id=assistant_reply.id,
                        chat_model=answered_by,
                    )
                finally:
                    _SCORING_THREADS.discard(session_id)

            _SCORING_THREADS.add(session_id)
            task = asyncio.create_task(_score_and_forget())
            _SCORING_TASKS.add(task)
            task.add_done_callback(_forget_scoring_task)
    else:
        # No retrieval happened, so any marker here would be imitation (e.g. copied
        # from a resumed transcript) — strip defensively.
        content = strip_figure_markers(assistant_msg.content or "")
        content, followups = _extract_followups(content)
        followup_questions = _sanitize_followup_questions(followups)
        cl.user_session.set("followup_questions", followup_questions)
        assistant_reply = cl.Message(content=content)
        actions = _build_chat_actions(
            followup_questions=followup_questions,
            has_citations_panel=False,
            source_step_id=assistant_reply.id,
        )
        assistant_reply.actions = actions
        await assistant_reply.send()
        messages.append({"role": "assistant", "content": content})
        add_chat_message(
            CHAT_DB_PATH,
            session_id,
            "assistant",
            content,
            metadata={
                "followup_count": len(followup_questions),
                "followup_questions": followup_questions,
            },
        )

    cl.user_session.set("messages", messages)

    # Trigger background profile update if enough messages accumulated
    user_id = cl.user_session.get("current_user_id")
    if user_id:
        try:
            current_profile = cl.user_session.get("user_profile")
            current_count = get_user_message_count(CHAT_DB_PATH, user_id)
            profile_count = current_profile.message_count if current_profile else 0

            # Update profile if 10+ new messages since last update
            if current_count >= PROFILE_MIN_MESSAGES and current_count - profile_count >= 10:
                print(f"[DEBUG] triggering profile update for {user_id}, new_messages={current_count - profile_count}")
                updated_profile = await update_user_profile(user_id)
                cl.user_session.set("user_profile", updated_profile)
                _rebuild_system_prompt_in_session()
        except Exception as e:
            print(f"[WARN] profile_update_failed for user_id={user_id}: {e.__class__.__name__}: {e}")
