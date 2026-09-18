"""Check that everything this app needs is reachable, and say what to fix.

Run this when something does not work. It answers the question that otherwise costs
the most time: is the problem my settings, my connection, or the AI service? Every
failure comes with numbered steps rather than a stack trace.

    make check                     # or: python -m check_setup

Each model is tried several times on purpose. The failure that misleads people is not
"it broke" but "it broke sometimes": a connection that loses one request in three lets
a single chat message through while making a document import fail dozens of times. One
attempt cannot tell those apart.
"""

from __future__ import annotations

import argparse
import base64
import errno
import io
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Enough attempts to tell "unstable" from "broken". Every probe is a few tokens.
ATTEMPTS = 5

ENV_FILE = "apps/chainlit/.env"

# The values .env.example ships with. Someone who copied that file and never edited it
# is the most common cause of "nothing works", and it is worth naming plainly instead
# of letting them read a connection error.
PLACEHOLDER_KEYS = {"your-key", "sk-your-key", "changeme", "change-me", "todo", "xxx"}
PLACEHOLDER_URL = "http://localhost:4000"


@dataclass
class Result:
    name: str
    ok: bool = False
    attempts: int = 0
    passed: int = 0
    seconds: list[float] = field(default_factory=list)
    error: str = ""
    cause: str = ""
    steps: list[str] = field(default_factory=list)
    skipped: str = ""

    @property
    def median(self) -> float:
        return statistics.median(self.seconds) if self.seconds else 0.0

    def line(self) -> str:
        if self.skipped:
            return f"  [skip] {self.name}: {self.skipped}"
        if self.ok and self.passed == self.attempts:
            return f"  [ ok ] {self.name}: {self.passed}/{self.attempts}, {self.median:.1f}s"
        if self.passed:
            return (
                f"  [WARN] {self.name}: only {self.passed} of {self.attempts} attempts "
                f"worked, {self.median:.1f}s"
            )
        return f"  [FAIL] {self.name}"


def _short(exc: Exception | str, limit: int = 110) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _mask(secret: str) -> str:
    """Show enough of the key to recognise it, never enough to use it."""
    if not secret:
        return "(not set)"
    if len(secret) <= 8:
        return "*" * len(secret) + f" ({len(secret)} characters)"
    return f"{secret[:4]}...{secret[-2:]} ({len(secret)} characters)"


# --------------------------------------------------------------------------- #
# Turning an error into steps
# --------------------------------------------------------------------------- #
_KEY_STEPS = [
    f"Open {ENV_FILE}",
    "Find the line starting LITELLM_API_KEY= and make sure a real key follows it",
    "Remove any quotes or spaces around the key",
    "Ask whoever gave you the key whether it is still valid",
    "Run 'make check' again",
]

_URL_STEPS = [
    f"Open {ENV_FILE}",
    "Find the line starting LITELLM_BASE_URL= and check the address for typos",
    "It has to start with https:// or http://, and include the port if there is one",
    "If the service runs on your own computer, write host.docker.internal instead of "
    "localhost, because localhost inside the app means the app itself",
    "Run 'make check' again",
]

_CONNECTION_STEPS = [
    "Turn off any VPN and try again. This is the most common cause",
    "Try a different network. A busy shared network does this",
    "Wait a few minutes: the service itself may be overloaded",
    "Before reading documents in, set images.mode: none. That removes most of the "
    "calls, so you can confirm the rest works without paying for pictures",
]


def _classify(exc: Exception, *, host_reachable: bool = True) -> tuple[str, list[str]]:
    """Return (what it means, what to do) for an error from a model call."""
    text = str(exc).lower()

    # Not a model call, but ``kb.ingest._report_failure`` sends every failed run
    # through here, and a folder the run cannot write to is the one non-network way
    # an ingest ends early. Without this branch it comes out as "the service
    # returned an error this check does not recognise", which sends the reader to
    # the AI service for a mount problem. It goes first because the string matches
    # below would otherwise claim it: a path holding "token" reads as a rejected key.
    if isinstance(exc, OSError) and exc.errno in (
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    ):
        target = getattr(exc, "filename", None) or "a folder it needs"
        return (
            f"The run is not allowed to write to {target}.",
            [
                "Check whether that path is on a read-only mount, such as a network "
                "share mounted with 'ro'",
                "Your documents may stay read-only, but the folders the run writes to "
                "may not: pdf_options.docling_json_dir, and sources.data_dir with the "
                "figures and descriptions folders under it",
                "Point those at a writable folder, or mount the share read-write",
            ],
        )

    if "disconnected" in text or "connection error" in text or "timed out" in text:
        # litellm reports a misspelled address and a mid-request disconnect with the
        # same "Connection error", so the message alone cannot tell them apart. The
        # separate host check can. Getting this wrong sends people hunting for a VPN
        # problem when what they have is a typo.
        if not host_reachable:
            return "The AI service could not be reached at all.", _URL_STEPS
        return (
            "The service was found, but it stopped answering part way through.",
            _CONNECTION_STEPS,
        )

    if (
        "401" in text
        or "unauthorized" in text
        or "invalid api key" in text
        or "authentication" in text
        or "token" in text
    ):
        return "The access key was rejected.", _KEY_STEPS

    if "403" in text or "forbidden" in text:
        return (
            "The service refused access to this model.",
            [
                "Check that your key is allowed to use this particular model",
                "Ask the service which models your key may use",
                "Some services want the model name written differently, for example "
                "with or without a prefix such as 'openai/'",
                "Put a name it accepts into your settings file, then run 'make check' again",
            ],
        )

    if "404" in text or "not found" in text or "does not exist" in text:
        return (
            "The service does not know this model name.",
            [
                "Ask the service which models it offers",
                "Open your settings file, the one named by RAG_CONFIG",
                "Put one of those names into models.chat_model, models.embed_model or "
                "images.vision_model, whichever failed above",
                "Run 'make check' again",
            ],
        )

    if "429" in text or "rate limit" in text:
        return (
            "The service is rate limiting you: too many requests too quickly.",
            [
                "Wait a few minutes and try again",
                "While reading documents this usually still finishes, because failed "
                "calls are tried again automatically",
            ],
        )

    if "413" in text or "too large" in text:
        return (
            "The service rejected the request for being too large.",
            [
                "For pictures, lower images.describe_image_max_px in your settings file",
                "Report this if it keeps happening: the app is supposed to shrink "
                "pictures before sending them",
            ],
        )

    return (
        "The service returned an error this check does not recognise.",
        [
            "Read the error line above: it usually names the problem",
            "See docs/troubleshooting.md",
        ],
    )


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #
def _probe(name: str, call, attempts: int = ATTEMPTS, *, host_reachable: bool = True) -> Result:
    result = Result(name=name)
    for _ in range(attempts):
        result.attempts += 1
        start = time.time()
        try:
            call()
        except Exception as exc:  # noqa: BLE001 — this tool reports, it does not raise
            if not result.error:
                result.error = _short(exc)
                result.cause, result.steps = _classify(exc, host_reachable=host_reachable)
            continue
        result.passed += 1
        result.seconds.append(time.time() - start)
    result.ok = result.passed == result.attempts and result.attempts > 0
    if 0 < result.passed < result.attempts:
        # Some attempts worked, so the settings must be right: only the connection is
        # left to blame. Say that, rather than repeating whatever the first error was.
        result.cause = (
            f"Only {result.passed} of {result.attempts} attempts worked. Wrong settings "
            "fail every time, not sometimes, so this is the connection."
        )
        result.steps = _CONNECTION_STEPS
    return result


def check_settings(base_url: str, api_key: str) -> list[Result]:
    """Catch the obvious before spending anything over the network."""
    results: list[Result] = []

    url = Result(name="An address for the AI service is set", attempts=1)
    if not base_url:
        url.error = "LITELLM_BASE_URL is empty"
        url.cause = "Without an address the app cannot reach any AI service."
        url.steps = _URL_STEPS
    elif base_url.strip().rstrip("/") == PLACEHOLDER_URL:
        url.error = f"LITELLM_BASE_URL is still the example value {PLACEHOLDER_URL}"
        url.cause = (
            "That is the value the example file ships with, so it was probably never "
            "changed to your own service."
        )
        url.steps = [
            f"Open {ENV_FILE}",
            "Replace LITELLM_BASE_URL with the address of the AI service you were given",
            "If you genuinely do run a service at that address yourself, ignore this",
            "Run 'make check' again",
        ]
    else:
        url.ok = True
        url.passed = 1
    results.append(url)

    key = Result(name="An access key is set", attempts=1)
    if not api_key:
        key.error = "LITELLM_API_KEY is empty"
        key.cause = "Without a key the service will refuse every request."
        key.steps = _KEY_STEPS
    elif api_key.strip().lower() in PLACEHOLDER_KEYS:
        key.error = f"LITELLM_API_KEY is still the example value '{api_key}'"
        key.cause = (
            "That is the placeholder from the example file, not a real key, so the "
            "service will refuse every request."
        )
        key.steps = _KEY_STEPS
    else:
        key.ok = True
        key.passed = 1
    results.append(key)
    return results


def check_service_host(base_url: str) -> Result:
    """Can the service be found, and does it accept a connection?

    Deliberately separate from the model probes: it tells "the address is wrong" apart
    from "the connection is unstable", which the library's own message cannot, because
    both arrive as "Connection error".
    """
    import socket
    from urllib.parse import urlparse

    result = Result(name="Reaching the AI service")
    if not base_url:
        result.error = "no address set"
        result.cause = "Without an address the app cannot reach any AI service."
        result.steps = _URL_STEPS
        return result

    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        result.error = f"cannot read a host out of {base_url!r}"
        result.cause = "The address is not in a usable form."
        result.steps = _URL_STEPS
        return result

    result.attempts = 1
    start = time.time()
    try:
        socket.getaddrinfo(host, port)
    except OSError as exc:
        result.error = f"the name {host} could not be looked up ({_short(exc, 50)})"
        result.cause = f"The name {host} does not exist, or you are not online."
        result.steps = [
            "Check that you have a working internet connection",
            f"Open {ENV_FILE} and check LITELLM_BASE_URL for typos",
            "Run 'make check' again",
        ]
        return result
    try:
        with socket.create_connection((host, port), timeout=8):
            pass
    except OSError as exc:
        result.error = f"{host}:{port} did not accept a connection ({_short(exc, 50)})"
        result.cause = f"The name {host} exists, but nothing answered on port {port}."
        result.steps = [
            "Turn off any VPN and try again",
            "A firewall may be blocking it: ask whoever runs your network",
            f"Check that the port in LITELLM_BASE_URL is right (this tried {port})",
            "The service itself may be down: ask whoever runs it",
        ]
        return result

    result.passed = 1
    result.ok = True
    result.seconds.append(time.time() - start)
    result.name = f"Reaching the AI service ({host}:{port})"
    return result


def check_qdrant(url: str) -> Result:
    def call() -> None:
        with urllib.request.urlopen(f"{url.rstrip('/')}/collections", timeout=10) as response:
            json.loads(response.read().decode("utf-8"))

    result = _probe(f"Search index at {url}", call, attempts=2)
    if not result.passed:
        result.cause = "The search index is not reachable, so nothing can be looked up."
        result.steps = [
            "Start everything with 'docker compose up -d'",
            "Check what is running with 'docker compose ps'",
            "Inside the app, localhost means the app itself, so this address has to be "
            "the service name http://qdrant:6333",
        ]
    return result


def check_hybrid_collection(config) -> Result:
    """Can the configured collection actually serve hybrid retrieval?

    `check_qdrant` only proves the index answers; it never opens the collection.
    A collection built before hybrid existed, or by a different lexical format,
    matches nothing on the lexical half — so hybrid looks enabled and does not
    work. The app refuses to start in that state, which is unhelpful to discover
    after a deploy; this is the preflight that says so first.
    """
    collection = config.vector_store.collection
    result = Result(name=f"Hybrid search for collection '{collection}'")
    if not config.retrieval.hybrid:
        result.skipped = "retrieval.hybrid is off"
        return result

    from kb.ingestion_pipeline import get_client, verify_hybrid_compatible

    try:
        verify_hybrid_compatible(get_client(), collection, hybrid=True)
    except RuntimeError as exc:
        result.error = _short(str(exc))
        result.cause = "Hybrid search is switched on, but this collection cannot serve it."
        result.steps = [
            "Run 'docker compose run --rm ingest python -m kb.ingest' — it rebuilds "
            "a collection in this state by itself",
            "Or set retrieval.hybrid: false in your config to stay with semantic search",
            "Run 'make check' again",
        ]
        return result
    except Exception as exc:  # noqa: BLE001 — reachability is check_qdrant's job
        result.skipped = f"could not be checked ({_short(str(exc))})"
        return result

    result.ok = True
    result.attempts = result.passed = 1
    return result


def _tiny_png_data_uri() -> str:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (96, 96), "white")
    ImageDraw.Draw(image).rectangle([16, 16, 80, 80], fill="red")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


# --------------------------------------------------------------------------- #
# Running everything
# --------------------------------------------------------------------------- #
def run(*, skip_vision: bool = False) -> list[Result]:
    from config import get_config
    from settings import LITELLM_API_KEY, LITELLM_BASE_URL, QDRANT_URL

    config = get_config()
    results: list[Result] = []

    print("What this app is set up to use")
    print(f"  settings file : {os.environ.get('RAG_CONFIG', '(default)')}")
    print(f"  AI service    : {LITELLM_BASE_URL or '(not set)'}")
    print(f"  access key    : {_mask(LITELLM_API_KEY or '')}")
    print(f"  chat model    : {config.models.chat_model}")
    print(f"  search model  : {config.models.embed_model}")
    print(f"  image model   : {config.images.vision_model} (images.mode: {config.images.mode})")
    print(f"  search index  : {QDRANT_URL}")
    print()

    print("Checking your settings")
    settings_results = check_settings(LITELLM_BASE_URL or "", LITELLM_API_KEY or "")
    results.extend(settings_results)
    for result in settings_results:
        print(result.line())

    if any(not r.ok for r in settings_results):
        # No point spending calls, or confusing anyone with a second error, when the
        # settings themselves are plainly not filled in.
        print()
        print("Stopping here. Those have to be right before anything else can work.")
        print()
        return results

    print()
    print(f"Trying each one {ATTEMPTS} times, to see whether it works every time")
    host = check_service_host(LITELLM_BASE_URL or "")
    print(host.line())

    import llm

    probes: list[Result] = [host]
    probes.append(
        _probe(
            f"Search model ({config.models.embed_model})",
            lambda: llm.embed_sync(["probe"]),
            host_reachable=host.ok,
        )
    )

    def chat_call() -> None:
        import litellm

        litellm.completion(
            model=config.models.chat_model,
            messages=[{"role": "user", "content": "Reply with: OK"}],
            max_tokens=5,
            temperature=0,
            **llm._client_args(config.models.chat_model),
        )

    probes.append(
        _probe(f"Chat model ({config.models.chat_model})", chat_call, host_reachable=host.ok)
    )

    vision = Result(name=f"Image model ({config.images.vision_model})")
    if skip_vision:
        vision.skipped = "not checked (--skip-vision)"
    elif config.images.mode == "none":
        vision.skipped = "not needed, images.mode is 'none'"
    else:
        uri = _tiny_png_data_uri()
        vision = _probe(
            vision.name,
            lambda: llm.describe_image_sync(
                uri, "Name the shape in one word.", config.images.vision_model
            ),
            host_reachable=host.ok,
        )
    probes.append(vision)
    probes.append(check_qdrant(QDRANT_URL))
    probes.append(check_hybrid_collection(config))

    for result in probes[1:]:
        print(result.line())
    results.extend(probes)
    print()
    return results


def summarise(results: list[Result]) -> int:
    """Print what to do about each problem. Returns the exit code."""
    checked = [r for r in results if not r.skipped]
    problems = [r for r in checked if r.passed < r.attempts or not r.attempts]

    if not problems:
        print("All good. Everything the app needs is reachable and answered every time.")
        print("If something still does not work, see docs/troubleshooting.md.")
        return 0

    noun = "thing needs" if len(problems) == 1 else "things need"
    print(f"{len(problems)} {noun} attention.")
    for result in problems:
        print()
        print(f"  {result.name}")
        if result.error:
            print(f"    the error : {result.error}")
        if result.cause:
            print(f"    meaning   : {result.cause}")
        for index, step in enumerate(result.steps, start=1):
            label = "    do this  : " if index == 1 else " " * len("    do this  : ")
            print(f"{label}{index}. {step}")
    print()
    print("More explanations: docs/troubleshooting.md")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the AI service and search index are reachable, and say what to fix."
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Do not test the image model, even when images are switched on.",
    )
    args = parser.parse_args()
    raise SystemExit(summarise(run(skip_vision=args.skip_vision)))


if __name__ == "__main__":
    main()
