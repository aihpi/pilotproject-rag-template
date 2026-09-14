"""A tool file dropped into extra_tools/ registers itself, without Docker.

Runs the import in a subprocess so the probe does not disturb the registry the
other tests use.
"""

import os
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
PROBE = APP / "extra_tools" / "zz_probe_tool.py"

PROBE_SOURCE = '''
from tools import register_tool
from tools.base import ToolResult

@register_tool("zz_probe", build_schema=lambda cfg: {"type": "function", "function": {"name": "zz_probe"}})
async def _probe(args, ctx):
    return ToolResult(payload={})
'''


def _registered_ids() -> list[str]:
    env = {**os.environ, "RAG_CONFIG": "config/default.yaml"}
    out = subprocess.run(
        [sys.executable, "-c", "import tools; print(','.join(sorted(tools.TOOL_REGISTRY)))"],
        cwd=APP, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip().splitlines()[-1].split(",")


def test_file_in_extra_tools_registers():
    PROBE.write_text(PROBE_SOURCE)
    try:
        ids = _registered_ids()
    finally:
        PROBE.unlink()
    assert "zz_probe" in ids and "search" in ids


def test_without_the_file_it_is_gone():
    assert not PROBE.exists()
    assert "zz_probe" not in _registered_ids()
