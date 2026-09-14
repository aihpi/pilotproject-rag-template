"""Tool registry for the agentic-RAG toolset.

Mirrors the ``kb/chunkers`` / ``kb/parsers`` convention: a module-level registry
dict, a ``register_tool`` decorator, a ``get_tool`` lookup that raises a helpful
``KeyError``, and lazy submodule imports at the bottom so each tool self-registers
at package-import time. Which tools are exposed to the model is driven by
``config.tools.enabled`` (see :func:`build_openai_tools`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.base import (
    SchemaBuilder,
    Tool,
    ToolContext,
    ToolHandler,
    ToolResult,
    clamp_top_k,
)

if TYPE_CHECKING:
    from config.schema import RagConfig

TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(tool_id: str, *, build_schema: SchemaBuilder):
    def deco(handler: ToolHandler) -> ToolHandler:
        TOOL_REGISTRY[tool_id] = Tool(id=tool_id, build_schema=build_schema, handler=handler)
        return handler

    return deco


def get_tool(tool_id: str) -> Tool:
    tool = TOOL_REGISTRY.get(tool_id)
    if tool is None:
        raise KeyError(
            f"no tool registered for '{tool_id}'. Registered: {sorted(TOOL_REGISTRY)}"
        )
    return tool


def enabled_tool_ids(cfg: "RagConfig") -> list[str]:
    return list(cfg.tools.enabled) or ["search"]


def build_openai_tools(cfg: "RagConfig") -> tuple[list[dict[str, Any]], dict[str, Tool]]:
    """Return ``(schemas, by_function_name)`` for the enabled tools:
    the OpenAI schema list to pass to ``chat(tools=...)`` and a router map from
    each schema's function name to its :class:`Tool`."""
    schemas: list[dict[str, Any]] = []
    by_function_name: dict[str, Tool] = {}
    for tool_id in enabled_tool_ids(cfg):
        tool = get_tool(tool_id)
        schema = tool.build_schema(cfg)
        schemas.append(schema)
        by_function_name[schema["function"]["name"]] = tool
    return schemas, by_function_name


# Lazy imports populate the registry. Each tool self-registers via its
# @register_tool decorator at import time.
#
# Two scan locations:
#   tools/        -- built-in tools (imported as package members)
#   extra_tools/  -- next to tools/, empty in this repo; drop or bind-mount any .py
#                    here to add tools without touching this repo. In Docker the
#                    app lives at /app, so this is /app/extra_tools.
import importlib  # noqa: E402
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

for _f in sorted(Path(__file__).parent.glob("*.py")):
    if _f.stem not in ("__init__", "base"):
        importlib.import_module(f"tools.{_f.stem}")

import sys  # noqa: E402

_extra = Path(__file__).resolve().parent.parent / "extra_tools"
if _extra.exists():
    for _f in sorted(_extra.glob("*.py")):
        _mod = None
        try:
            _spec = importlib.util.spec_from_file_location(_f.stem, _f)
            if _spec and _spec.loader:
                _mod = importlib.util.module_from_spec(_spec)
                # Registered before exec so the module can import itself by name;
                # the except below undoes this when exec does not finish.
                sys.modules[_f.stem] = _mod
                _spec.loader.exec_module(_mod)
        except Exception as _e:
            # A tool that raises halfway through import leaves a half-built module
            # behind, and a later `import <stem>` gets that instead of an
            # ImportError -- a broken tool degrading into wrong behaviour rather
            # than a clear failure. Only drop the object we put there: a file whose
            # stem shadows a real module (json.py, types.py) must not unload it.
            if _mod is not None and sys.modules.get(_f.stem) is _mod:
                del sys.modules[_f.stem]
            import warnings
            warnings.warn(f"extra_tools/{_f.name} failed to load: {_e}")

__all__ = [
    "TOOL_REGISTRY",
    "register_tool",
    "get_tool",
    "enabled_tool_ids",
    "build_openai_tools",
    "clamp_top_k",
    "Tool",
    "ToolContext",
    "ToolResult",
]
