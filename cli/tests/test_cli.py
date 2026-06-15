from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

# Mock heavy dependencies before importing _cli -- asyncpg and orxt.services
# are not available in the test environment.
_MOCK_MODS = [
    "asyncpg",
    "orxt.services",
    "orxt.services._run",
    "orxt.services._inbox",
    "orxt.services._trace",
    "orxt.services._events",
    "orxt.services._validate",
    "orxt.services._config",
]
for _mod in _MOCK_MODS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest  # noqa: E402
from orxt.cli._cli import _build_parser  # noqa: E402

if TYPE_CHECKING:
    import argparse


def _parse(*args: str) -> argparse.Namespace:
    parser = _build_parser()
    return parser.parse_args(list(args))


# -- Global flags ----------------------------------------------------------------


def test_db_default_is_none() -> None:
    ns = _parse("config", "pricing")
    assert ns.db is None


def test_db_flag() -> None:
    ns = _parse("--db", "postgres://localhost/orxt", "config", "pricing")
    assert ns.db == "postgres://localhost/orxt"


def test_format_default_is_table() -> None:
    ns = _parse("config", "pricing")
    assert ns.format == "table"


def test_format_json() -> None:
    ns = _parse("--format", "json", "config", "pricing")
    assert ns.format == "json"


def test_format_invalid_choice_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("--format", "xml", "config", "pricing")


def test_quiet_flag_default_false() -> None:
    ns = _parse("config", "pricing")
    assert ns.quiet is False


def test_quiet_flag() -> None:
    ns = _parse("--quiet", "config", "pricing")
    assert ns.quiet is True


def test_no_command_exits() -> None:
    with pytest.raises(SystemExit):
        _parse()


# -- run -------------------------------------------------------------------------


def test_run_list() -> None:
    ns = _parse("run", "list")
    assert ns.command == "run"
    assert ns.subcommand == "list"


def test_run_show() -> None:
    ns = _parse("run", "show", "abc123")
    assert ns.subcommand == "show"
    assert ns.run_id == "abc123"


def test_run_start() -> None:
    ns = _parse("run", "start", "--config", "conf/run.toml", "--intent", "do stuff")
    assert ns.subcommand == "start"
    assert ns.config == "conf/run.toml"
    assert ns.intent == "do stuff"


def test_run_start_missing_config_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("run", "start", "--intent", "foo")


def test_run_start_missing_intent_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("run", "start", "--config", "conf/run.toml")


def test_run_abort() -> None:
    ns = _parse("run", "abort", "some-id")
    assert ns.subcommand == "abort"
    assert ns.run_id == "some-id"


def test_run_pause() -> None:
    ns = _parse("run", "pause", "some-id")
    assert ns.subcommand == "pause"
    assert ns.run_id == "some-id"


def test_run_resume() -> None:
    ns = _parse("run", "resume", "some-id")
    assert ns.subcommand == "resume"
    assert ns.run_id == "some-id"


def test_run_missing_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("run")


# -- inbox -----------------------------------------------------------------------


def test_inbox_list() -> None:
    ns = _parse("inbox", "list", "--run", "abc123")
    assert ns.command == "inbox"
    assert ns.subcommand == "list"
    assert ns.run_id == "abc123"
    assert ns.status is None


def test_inbox_list_with_status() -> None:
    ns = _parse("inbox", "list", "--run", "abc123", "--status", "pending")
    assert ns.status == "pending"


def test_inbox_list_missing_run_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("inbox", "list")


def test_inbox_show() -> None:
    ns = _parse("inbox", "show", "item1")
    assert ns.subcommand == "show"
    assert ns.item_id == "item1"


def test_inbox_respond() -> None:
    ns = _parse("inbox", "respond", "item1", "yes")
    assert ns.item_id == "item1"
    assert ns.answer == "yes"


def test_inbox_skip() -> None:
    ns = _parse("inbox", "skip", "item1")
    assert ns.subcommand == "skip"
    assert ns.item_id == "item1"


def test_inbox_reject() -> None:
    ns = _parse("inbox", "reject", "item1", "bad options")
    assert ns.item_id == "item1"
    assert ns.reason == "bad options"


def test_inbox_missing_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("inbox")


# -- trace -----------------------------------------------------------------------


def test_trace_events() -> None:
    ns = _parse("trace", "events", "rid")
    assert ns.command == "trace"
    assert ns.subcommand == "events"
    assert ns.run_id == "rid"
    assert ns.event_type is None
    assert ns.limit == 100


def test_trace_events_with_flags() -> None:
    ns = _parse("trace", "events", "rid", "--type", "run.started", "--limit", "50")
    assert ns.event_type == "run.started"
    assert ns.limit == 50


def test_trace_transcript() -> None:
    ns = _parse("trace", "transcript", "sid1")
    assert ns.subcommand == "transcript"
    assert ns.session_id == "sid1"


def test_trace_search() -> None:
    ns = _parse("trace", "search", "sid1", "error")
    assert ns.session_id == "sid1"
    assert ns.query == "error"


def test_trace_tasks() -> None:
    ns = _parse("trace", "tasks", "rid")
    assert ns.subcommand == "tasks"
    assert ns.run_id == "rid"


def test_trace_notepad() -> None:
    ns = _parse("trace", "notepad", "rid")
    assert ns.subcommand == "notepad"
    assert ns.run_id == "rid"


def test_trace_missing_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("trace")


# -- event -----------------------------------------------------------------------


def test_event_fire() -> None:
    ns = _parse("event", "fire", "rid", "deploy")
    assert ns.command == "event"
    assert ns.subcommand == "fire"
    assert ns.run_id == "rid"
    assert ns.event_name == "deploy"
    assert ns.payload is None


def test_event_fire_with_payload() -> None:
    ns = _parse("event", "fire", "rid", "deploy", "--payload", '{"key": "val"}')
    assert ns.payload == '{"key": "val"}'


def test_event_missing_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("event")


# -- validate --------------------------------------------------------------------


def test_validate_agent() -> None:
    ns = _parse("validate", "agent", "agents/planner.toml")
    assert ns.command == "validate"
    assert ns.subcommand == "agent"
    assert ns.path == "agents/planner.toml"


def test_validate_workflow() -> None:
    ns = _parse("validate", "workflow", "workflows/main.toml")
    assert ns.subcommand == "workflow"
    assert ns.path == "workflows/main.toml"


def test_validate_categories() -> None:
    ns = _parse("validate", "categories", "conf/categories.toml")
    assert ns.subcommand == "categories"
    assert ns.path == "conf/categories.toml"


def test_validate_missing_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("validate")


# -- config ----------------------------------------------------------------------


def test_config_show() -> None:
    ns = _parse("config", "show", "rid")
    assert ns.command == "config"
    assert ns.subcommand == "show"
    assert ns.run_id == "rid"


def test_config_pricing() -> None:
    ns = _parse("config", "pricing")
    assert ns.command == "config"
    assert ns.subcommand == "pricing"


def test_config_missing_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _parse("config")


# -- Global flags combine with any command ----------------------------------------


def test_all_global_flags_with_run() -> None:
    ns = _parse("--db", "pg://x", "--format", "json", "--quiet", "run", "list")
    assert ns.db == "pg://x"
    assert ns.format == "json"
    assert ns.quiet is True
    assert ns.command == "run"
    assert ns.subcommand == "list"
