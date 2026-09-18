"""The setup check has to point at the right culprit, or it is worse than nothing.

Written after the first version got two cases wrong against a live gateway: an
address that does not resolve was blamed on "a VPN in between", and one of two
identical authentication failures fell through to "see the message above". Both are
pinned here.
"""

from __future__ import annotations

import check_setup
from check_setup import (
    Result,
    _classify,
    _mask,
    check_service_host,
    check_settings,
    summarise,
)


def _exc(message: str) -> Exception:
    return RuntimeError(message)


# --------------------------------------------------------------------------- #
# Reading an error
# --------------------------------------------------------------------------- #
def test_a_dropped_connection_blames_the_network_only_when_the_host_answers():
    """The regression: litellm reports a DNS failure and a mid-request disconnect
    identically as "Connection error", so the message alone must not decide."""
    dropped = _exc("litellm.InternalServerError: OpenAIException - Connection error.")

    reachable_cause, reachable_steps = _classify(dropped, host_reachable=True)
    unreachable_cause, unreachable_steps = _classify(dropped, host_reachable=False)

    assert "stopped answering" in reachable_cause
    assert any("VPN" in s for s in reachable_steps)
    assert "could not be reached" in unreachable_cause
    assert not any("VPN" in s for s in unreachable_steps), (
        "a wrong address must not send people hunting a VPN"
    )
    assert any("LITELLM_BASE_URL" in s for s in unreachable_steps)


def test_every_shape_of_auth_failure_blames_the_key():
    """The second regression: this gateway words 401 several ways."""
    for message in (
        "Error code: 401 - {'error': {'message': 'Authentication Error, Invalid proxy...",
        "AuthenticationError: Invalid proxy server token passed.",
        "openai.AuthenticationError: Unauthorized",
        "Invalid API key provided",
    ):
        cause, steps = _classify(_exc(message))
        assert "key was rejected" in cause, message
        assert any("LITELLM_API_KEY" in s for s in steps), message


def test_an_unknown_model_is_not_confused_with_a_bad_key():
    cause, steps = _classify(_exc("Error code: 404 - model 'gemma-9' does not exist"))
    assert "does not know this model name" in cause
    assert any("which models it offers" in s for s in steps)


def test_rate_limiting_says_to_wait():
    cause, steps = _classify(_exc("Error code: 429 - rate limit exceeded"))
    assert "rate limiting" in cause
    assert any("Wait" in s for s in steps)


def test_a_forbidden_response_mentions_the_model_name_spelling():
    """403 here usually means the model name needs a different form for this gateway."""
    cause, steps = _classify(_exc("Error code: 403 - Forbidden"))
    assert "refused access" in cause
    assert any("written differently" in s for s in steps)


def test_an_unrecognised_error_does_not_invent_a_cause():
    cause, steps = _classify(_exc("something entirely unexpected"))
    assert "does not recognise" in cause
    assert any("troubleshooting" in s for s in steps)


def test_a_folder_the_run_cannot_write_to_is_not_blamed_on_the_service():
    """What a PANDA-style data folder on a read-only share produces: the ingest
    dies in mkdir, and without this the reader is sent to the AI service."""
    exc = PermissionError(13, "Permission denied", "/panda-data/docling/beads")
    cause, steps = _classify(exc)
    assert "/panda-data/docling/beads" in cause
    assert "service" not in cause.lower()
    assert any("read-only" in s for s in steps)


def test_a_path_that_reads_like_a_key_error_is_still_a_permission_error():
    """'token' in a path would otherwise match the rejected-key branch below it."""
    exc = PermissionError(13, "Permission denied", "/mnt/share/tokenizers/cache")
    cause, _ = _classify(exc)
    assert "not allowed to write" in cause


# --------------------------------------------------------------------------- #
# Settings, checked before anything is spent
# --------------------------------------------------------------------------- #
def test_the_unedited_example_key_is_named_as_the_problem():
    """The commonest mistake: copy .env.example, never edit it."""
    url, key = check_settings("https://api.example.org/", "your-key")

    assert url.ok
    assert not key.ok
    assert "example value" in key.error
    assert any("LITELLM_API_KEY" in s for s in key.steps)


def test_the_unedited_example_address_is_named_too():
    url, key = check_settings("http://localhost:4000", "a-real-looking-key")

    assert not url.ok
    assert "example value" in url.error
    assert any("genuinely do run a service" in s for s in url.steps), (
        "someone genuinely running a local proxy must be told they can ignore it"
    )
    assert key.ok


def test_empty_settings_are_reported_separately():
    url, key = check_settings("", "")

    assert not url.ok and "empty" in url.error
    assert not key.ok and "empty" in key.error


def test_real_looking_settings_pass():
    url, key = check_settings("https://api.example.org/", "sk-abcdef123456")
    assert url.ok and key.ok


def test_the_key_is_never_printed_in_full():
    masked = _mask("sk-super-secret-value-1234")
    assert "super-secret" not in masked
    assert masked.startswith("sk-s")
    assert "26 characters" in masked
    assert _mask("") == "(not set)"


# --------------------------------------------------------------------------- #
# Reaching the host
# --------------------------------------------------------------------------- #
def test_a_name_that_does_not_resolve_is_reported_as_such():
    result = check_service_host("https://this-host-does-not-exist.invalid/")

    assert not result.ok
    assert "could not be looked up" in result.error
    assert any("LITELLM_BASE_URL" in s for s in result.steps)


def test_a_missing_address_is_reported_before_anything_is_tried():
    result = check_service_host("")
    assert not result.ok
    assert "no address set" in result.error
    assert result.steps, "a failure with no steps is not actionable"


def test_a_url_without_a_host_is_rejected_clearly():
    result = check_service_host("not-a-url")
    assert not result.ok
    assert "cannot read a host" in result.error


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #
def _ok(name: str) -> Result:
    return Result(name=name, ok=True, attempts=5, passed=5, seconds=[0.1] * 5)


def _flaky(name: str) -> Result:
    return Result(name=name, attempts=5, passed=3, seconds=[0.1] * 3, error="dropped")


def _broken(name: str) -> Result:
    return Result(name=name, attempts=5, passed=0, error="nope", cause="because",
                  steps=["do this first", "then this"])


def test_all_green_exits_zero(capsys):
    assert summarise([_ok("a"), _ok("b")]) == 0
    assert "All good" in capsys.readouterr().out


def test_partial_success_is_called_out_as_a_connection_problem(capsys):
    """The case from the reported log: most calls work, many do not."""
    from check_setup import _probe

    calls = {"n": 0}

    def flaky_call():
        calls["n"] += 1
        if calls["n"] % 2:
            raise RuntimeError("Connection error.")

    result = _probe("search", flaky_call, attempts=4)
    code = summarise([result])

    out = capsys.readouterr().out
    assert code == 1
    assert "fail every time, not sometimes" in result.cause
    assert any("VPN" in s for s in result.steps)
    assert "do this" in out


def test_a_hard_failure_prints_numbered_steps(capsys):
    code = summarise([_broken("search")])

    out = capsys.readouterr().out
    assert code == 1
    assert "1. do this first" in out
    assert "2. then this" in out, "steps must be numbered, not one blob of prose"
    assert "meaning" in out


def test_one_problem_is_not_reported_as_thing_s(capsys):
    summarise([_broken("search")])
    out = capsys.readouterr().out
    assert "1 thing needs attention" in out
    assert "(s)" not in out


def test_skipped_checks_do_not_count_against_the_verdict(capsys):
    skipped = Result(name="Image model", skipped="not needed, images.mode is 'none'")

    assert summarise([_ok("chat"), skipped]) == 0


def test_the_line_for_each_state_is_readable():
    assert "[ ok ]" in _ok("x").line()
    assert "[WARN]" in _flaky("x").line() and "only 3 of 5" in _flaky("x").line()
    assert "[FAIL]" in _broken("x").line()
    assert "[skip]" in Result(name="x", skipped="because").line()


def test_attempts_are_repeated_enough_to_reveal_flakiness():
    """One attempt cannot distinguish "broken" from "unreliable", which is the whole
    point of the tool."""
    assert check_setup.ATTEMPTS >= 3
