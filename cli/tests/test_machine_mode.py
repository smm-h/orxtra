"""Machine mode: the envelope is the only document stdout carries.

The CLI used to declare an app-global ``--format table|json`` and print the
chosen rendering. Machine output is the framework's now: ``--json`` enters
machine mode, the dispatch result is the envelope's ``payload`` member, and
the table is the human rendering that machine mode must not print.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

# Same mock window test_cli.py opens: _cli pulls in the whole services and
# storage stack at import time, and none of it is needed to exercise the
# output seam.
_MOCK_MODS = [
    "asyncpg",
    "orxtra.identity",
    "orxtra.services",
    "orxtra.services._run",
    "orxtra.services._inbox",
    "orxtra.services._trace",
    "orxtra.services._events",
    "orxtra.services._validate",
    "orxtra.services._config",
    "orxtra.trace",
    "orxtra.trace._writer",
    "orxtra.tool",
    "orxtra.tool._scrub",
    "orxtra.tool._pipeline",
    "orxtra.tool._consult_tool",
    "orxtra.worker._brain",
    "orxtra.worker._native",
    "orxtra.worker._docker",
    "orxtra.worker._pipeline_split",
    "orxtra.worker._protocol",
    "orxtra.worker._registry",
]
_installed_mocks = []
for _mod in _MOCK_MODS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
        _installed_mocks.append(_mod)

from orxtra.cli import _cli as _cli_module
from orxtra.cli._cli import app

for _mod in _installed_mocks:
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]

import importlib as _importlib

for _parent in ("orxtra.worker",):
    if _parent in sys.modules:
        _importlib.reload(sys.modules[_parent])


_PRICING = {
    "claude-opus-5": {
        "input_per_million": "15",
        "output_per_million": "75",
        "cache_read_per_million": "1.5",
        "cache_write_per_million": "18.75",
    },
}


class _Pool:
    """Stand-in for the asyncpg pool: opened, then closed, never queried."""

    async def close(self) -> None:
        return None


@pytest.fixture
def stub_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the database pool the run/inbox/trace commands open."""
    async def _create_pool(_url: str) -> _Pool:
        return _Pool()

    async def _verify_schema(_pool: _Pool) -> None:
        return None

    monkeypatch.setattr(_cli_module.asyncpg, "create_pool", _create_pool)
    monkeypatch.setattr(_cli_module, "verify_schema", _verify_schema)


@pytest.fixture
def stub_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Any], list[tuple[str, dict[str, Any]]]]:
    """Answer every capability call from a canned result."""
    def _install(result: Any) -> list[tuple[str, dict[str, Any]]]:
        calls: list[tuple[str, dict[str, Any]]] = []

        async def _dispatch(
            _ctx: object, capability: str, args: dict[str, Any],
        ) -> Any:
            calls.append((capability, args))
            return result

        monkeypatch.setattr(_cli_module, "dispatch", _dispatch)
        return calls

    return _install


def _envelope(stdout: str) -> dict[str, Any]:
    return json.loads(stdout)


def test_machine_mode_stdout_is_the_envelope(
    stub_dispatch: Callable[[Any], object],
) -> None:
    stub_dispatch(_PRICING)
    result = app.test(["config", "pricing", "--json"])

    env = _envelope(result.stdout)
    assert env["app"] == "orxtra"
    assert env["command"] == "config.pricing"
    assert env["exit_code"] == 0
    assert env["payload"] == _PRICING
    # The table never reaches stdout in machine mode.
    assert "input_per_million:" not in result.stdout


def test_human_mode_prints_the_table_and_no_envelope(
    stub_dispatch: Callable[[Any], object],
) -> None:
    stub_dispatch(_PRICING)
    result = app.test(["config", "pricing"])

    assert "claude-opus-5" in result.stdout
    assert "interface_version" not in result.stdout


def test_domain_types_reach_the_payload_as_plain_json(
    stub_dispatch: Callable[[Any], object], stub_pool: None,
) -> None:
    """A row carrying a UUID and a timestamp is a payload of strings.

    The framework serializes the payload itself, so the conversion has to
    happen before it sees the value -- and the declared schema would refuse
    anything the envelope cannot carry.
    """
    uid = UUID("12345678-1234-5678-1234-567812345678")
    started = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    stub_dispatch([{"id": uid, "started_at": started, "status": "running"}])

    result = app.test(["--db", "postgres://stub", "run", "list", "--json"])

    payload = _envelope(result.stdout)["payload"]
    assert payload == [{
        "id": str(uid),
        "started_at": started.isoformat(),
        "status": "running",
    }]


def test_a_payload_the_declaration_forbids_is_refused(
    stub_dispatch: Callable[[Any], object], stub_pool: None,
) -> None:
    """The declared schema is enforced: a listing must be a list of rows.

    Enforcement happens at emission, where the framework writes the envelope,
    and a deviation fails the run instead of shipping a wrong shape.
    """
    stub_dispatch("not a list of rows")

    with pytest.raises(RuntimeError, match="payload does not satisfy"):
        app.test(["--db", "postgres://stub", "run", "list", "--json"])
