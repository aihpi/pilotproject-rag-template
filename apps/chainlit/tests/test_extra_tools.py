"""A tool file dropped into extra_tools/ registers itself, without Docker.

The probe is written into the real ``extra_tools/`` folder rather than a
``tmp_path``: what is under test is that *that* path is scanned by a local
``chainlit run``, so a temporary directory would test nothing.

Each probe import runs in a subprocess so it cannot disturb the registry the
rest of the suite shares.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
PROBE = APP / "extra_tools" / "zz_probe_tool.py"

PROBE_SOURCE = '''
from tools import register_tool
from tools.base import ToolResult

@register_tool("zz_probe", build_schema=lambda cfg: {"type": "function", "function": {"name": "zz_probe"}})
async def _probe(args, ctx):
    return ToolResult(payload={})
'''

BROKEN_SOURCE = '''
DEFINED_BEFORE_THE_FAILURE = True
raise RuntimeError("boom during import")
'''

# The folder exists to hold arbitrary deployment code, and importing it is how
# these tests read the registry. A tool that blocks at import (a database or HTTP
# connection to an unreachable host) would otherwise hang the whole suite with no
# output, so bound it. Import takes ~0.1s; this is headroom for a slow runner,
# not a real expectation.
IMPORT_TIMEOUT_S = 60


@pytest.fixture(autouse=True, scope="module")
def _clear_stale_probes():
    """Remove probe files a previously killed run left behind.

    `finally` below covers ordinary failures but not SIGKILL or a cancelled CI
    job, and .gitignore hides `extra_tools/*` from `git status`, so a leftover
    file would sit there invisibly and load into every later run.
    """
    for stale in (PROBE, PROBE.with_name("zz_broken_tool.py")):
        stale.unlink(missing_ok=True)
    yield
    for stale in (PROBE, PROBE.with_name("zz_broken_tool.py")):
        stale.unlink(missing_ok=True)


def _run(snippet: str) -> str:
    env = {**os.environ, "RAG_CONFIG": "config/default.yaml"}
    out = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=APP, env=env, capture_output=True, text=True,
        check=True, timeout=IMPORT_TIMEOUT_S,
    )
    return out.stdout.strip().splitlines()[-1]


def _registered_ids() -> list[str]:
    return _run(
        "import tools; print(','.join(sorted(tools.TOOL_REGISTRY)))"
    ).split(",")


def test_file_in_extra_tools_registers():
    PROBE.write_text(PROBE_SOURCE)
    try:
        ids = _registered_ids()
    finally:
        PROBE.unlink(missing_ok=True)

    assert "zz_probe" in ids
    # Positive control: without it the assertion above passes just as well when
    # tool loading is broken and the registry is empty.
    assert "search" in ids


def test_without_the_file_it_is_gone():
    assert not PROBE.exists(), f"stale probe left behind at {PROBE}"

    ids = _registered_ids()

    # Same positive control, and it matters more here: an empty registry prints
    # nothing, `''.split(',')` is `['']`, and "zz_probe" not in [''] is True — so
    # this test passed even with tool loading completely broken.
    assert "search" in ids
    assert "zz_probe" not in ids


def test_a_broken_tool_is_skipped_without_poisoning_sys_modules():
    """A failed import must not leave a half-built module behind.

    It was registered in sys.modules before exec_module and never removed, so a
    later `import zz_broken_tool` anywhere in the process returned the partial
    module instead of raising.
    """
    broken = PROBE.with_name("zz_broken_tool.py")
    broken.write_text(BROKEN_SOURCE)
    try:
        result = _run(
            "import warnings, sys; warnings.simplefilter('ignore'); import tools; "
            "print('zz_broken_tool' in sys.modules)"
        )
    finally:
        broken.unlink(missing_ok=True)

    assert result == "False"


def test_a_broken_tool_does_not_stop_the_others_loading():
    broken = PROBE.with_name("zz_broken_tool.py")
    broken.write_text(BROKEN_SOURCE)
    try:
        ids = _registered_ids()
    finally:
        broken.unlink(missing_ok=True)

    assert "search" in ids
