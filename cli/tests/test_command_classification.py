"""Pins the strictcli effect classification of every orxtra command.

strictcli 0.36.0 makes ``effect`` mandatory on every command registration:
``read_only`` or ``mutating``, with no default. ``read_only`` means the command
performs no user-visible or consequential mutation. ``mutating`` governs dry-run
recording (``--dry-run`` is framework-owned and arrives as ``ctx.dry_run``).

Separately, a command may declare ``consequential=True``. That is *not* a
restatement of ``mutating``: it says the act is worth interrupting an operator
for, and the framework prompts only for those commands. Two thirds of the fleet
classify ``mutating``; only a handful are consequential, and inflating that set
is what trains the reflex to pass ``--approve-consequential`` blindly.

The table below is the whole CLI. The ``orxtra`` binary is a single strictcli
App whose commands are registered from five modules (``cli/_cli.py``,
``cli/_db.py``, ``cli/_dispatch.py``, ``api/_cli.py``, ``worker/_cli.py``), so
one table covers all of them.

Reasoning, group by group:

* ``run start/abort/pause/resume`` write run rows and control signals to the
  database -- mutating. ``run list/show`` only query.
* ``inbox respond/skip/reject`` resolve a pending human-in-the-loop item (a
  write plus a ``resolved_by`` attribution) -- mutating. ``inbox list/show``
  only query.
* Every ``trace`` command is a query over the append-only event store.
* ``event fire`` inserts an event row and wakes wait-for tasks -- mutating.
* ``validate agent/workflow/categories`` parse a TOML file off disk and report
  errors. Nothing is written anywhere.
* ``config show`` reads a stored run snapshot; ``config pricing`` prints an
  in-process table. Both read-only.
* ``serve``, ``dispatch run``, ``worker connect`` and ``worker docker`` start
  long-running processes that go on to write to the database or execute tool
  calls. A process whose whole purpose is to mutate is mutating even though the
  command itself only starts it.
* ``db init`` creates schema objects and seeds the system principal --
  mutating, but idempotent and additive, so not consequential.
* ``db verify``, ``db migrate plan`` and ``db migrate status`` inspect the live
  schema without changing it.
* ``db migrate apply`` is the one consequential command in orxtra: it executes
  DDL against whatever database ``--db`` names, a rerun cannot un-apply it, and
  a mistake there is not recoverable from the CLI. It meets the fleet bar
  ("destructive on a remote / cannot be un-done by a rerun"); nothing else in
  orxtra does.

``serve`` was considered for ``consequential`` -- it binds 0.0.0.0 by default,
which arguably "makes something live that was not before" -- and rejected: it
is the ordinary way to run the system in development, and prompting on every
server start is exactly the 1:10 signal-to-noise ratio the consequence round
exists to avoid.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Same mock dance as test_cli.py: importing the CLI module pulls in heavy
# database-backed packages that this structural test does not need.
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

from orxtra.cli._cli import app  # noqa: E402

for _mod in _installed_mocks:
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]

import importlib as _importlib  # noqa: E402

for _parent in ("orxtra.worker",):
    if _parent in sys.modules:
        _importlib.reload(sys.modules[_parent])


# dotted command path -> effect
EXPECTED_EFFECTS: dict[str, str] = {
    "serve": "mutating",
    "run.start": "mutating",
    "run.list": "read_only",
    "run.show": "read_only",
    "run.abort": "mutating",
    "run.pause": "mutating",
    "run.resume": "mutating",
    "inbox.list": "read_only",
    "inbox.show": "read_only",
    "inbox.respond": "mutating",
    "inbox.skip": "mutating",
    "inbox.reject": "mutating",
    "trace.events": "read_only",
    "trace.transcript": "read_only",
    "trace.search": "read_only",
    "trace.tasks": "read_only",
    "trace.notepad": "read_only",
    "event.fire": "mutating",
    "validate.agent": "read_only",
    "validate.workflow": "read_only",
    "validate.categories": "read_only",
    "config.show": "read_only",
    "config.pricing": "read_only",
    "db.init": "mutating",
    "db.verify": "read_only",
    "db.migrate.plan": "read_only",
    "db.migrate.apply": "mutating",
    "db.migrate.status": "read_only",
    "dispatch.run": "mutating",
    "worker.connect": "mutating",
    "worker.docker": "mutating",
}

# The complete consequential set. Adding to it is a deliberate decision, not a
# reflex: see the module docstring for the bar.
EXPECTED_CONSEQUENTIAL: frozenset[str] = frozenset({"db.migrate.apply"})


def _registered() -> dict[str, object]:
    return dict(app._collect_all_commands())


def test_every_command_is_in_the_table() -> None:
    assert set(_registered()) == set(EXPECTED_EFFECTS)


def test_command_count() -> None:
    assert len(EXPECTED_EFFECTS) == 31


def test_effects_match_the_table() -> None:
    actual = {path: cmd.effect for path, cmd in _registered().items()}
    assert actual == EXPECTED_EFFECTS


def test_consequential_set_is_exactly_the_table() -> None:
    actual = {
        path for path, cmd in _registered().items() if cmd.consequential
    }
    assert actual == EXPECTED_CONSEQUENTIAL


def test_consequential_commands_are_mutating() -> None:
    """strictcli hard-errors on a consequential read_only command.

    Asserting it here keeps the table itself honest even if the registration
    ever moves behind a helper.
    """
    for path in EXPECTED_CONSEQUENTIAL:
        assert EXPECTED_EFFECTS[path] == "mutating"


def test_read_only_commands_outnumber_mutating_ones_nowhere_near_the_prompt() -> None:
    """Sanity counts, pinned so a drift shows up as a diff rather than silently."""
    effects = list(EXPECTED_EFFECTS.values())
    assert effects.count("read_only") == 17
    assert effects.count("mutating") == 14


def test_no_command_declares_a_reserved_flag_name() -> None:
    """The quartet is banned at every level; strictcli enforces it, we pin it."""
    reserved = {"dry-run", "approve-consequential", "quiet", "verbose", "yes"}
    for path, cmd in _registered().items():
        for flag in cmd.flags:
            assert flag.name not in reserved, f"{path}: --{flag.name}"
    for flag in app.flags:
        assert flag.name not in reserved, f"app global: --{flag.name}"
