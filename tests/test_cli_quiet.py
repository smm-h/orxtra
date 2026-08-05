"""End-to-end checks that ``--quiet`` reaches the handlers that honour it.

``quiet`` is a strictcli-reserved name since 0.36.0: it can no longer be
declared as an app flag, and its value arrives on the handler's ``Context``
rather than as a keyword argument. Handlers that print a confirmation line now
read ``ctx.quiet``.

Several of those handlers do their work inside a nested ``async def _run()``
that rebinds the name ``ctx`` to an ``orxtra.services.DispatchContext`` -- a
completely different object with no ``quiet`` member. Reading ``ctx.quiet``
inside such a closure raises AttributeError at runtime and is invisible to any
test that only inspects the registration, which is why these run the real CLI
end to end instead.

The validate commands are the right vehicle: they need no database, so the
whole path from argv to the printed line executes for real.
"""

from __future__ import annotations

from pathlib import Path

from orxtra.cli._cli import app

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _run(*argv: str) -> tuple[str, str, int]:
    result = app.test(list(argv))
    return result.stdout, result.stderr, result.exit_code


def test_validate_agent_prints_valid_by_default() -> None:
    stdout, _, code = _run("validate", "agent", str(_EXAMPLES / "basic_agent.toml"))
    assert code == 0
    assert stdout.strip() == "valid"


def test_validate_agent_is_silent_under_quiet() -> None:
    stdout, _, code = _run(
        "--quiet", "validate", "agent", str(_EXAMPLES / "basic_agent.toml"),
    )
    assert code == 0
    assert stdout.strip() == ""


def test_validate_workflow_is_silent_under_quiet() -> None:
    stdout, _, code = _run(
        "--quiet", "validate", "workflow", str(_EXAMPLES / "simple_workflow.toml"),
    )
    assert code == 0
    assert stdout.strip() == ""


def test_validate_categories_is_silent_under_quiet() -> None:
    stdout, _, code = _run(
        "--quiet", "validate", "categories", str(_EXAMPLES / "categories.toml"),
    )
    assert code == 0
    assert stdout.strip() == ""


def test_quiet_is_accepted_after_the_command_token() -> None:
    """The reserved quartet is recognized anywhere in argv, not just up front."""
    stdout, _, code = _run(
        "validate", "agent", str(_EXAMPLES / "basic_agent.toml"), "--quiet",
    )
    assert code == 0
    assert stdout.strip() == ""
