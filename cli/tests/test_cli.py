# ruff: noqa: SLF001
from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing _cli.
_MOCK_MODS = [
    "asyncpg",
    "orxt.services",
    "orxt.services._run",
    "orxt.services._inbox",
    "orxt.services._trace",
    "orxt.services._events",
    "orxt.services._validate",
    "orxt.services._config",
    "orxt.trace",
    "orxt.trace._writer",
]
for _mod in _MOCK_MODS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from orxt.cli._cli import app  # noqa: E402


def _test(*args: str) -> tuple[str, str, int]:
    """Run app.test() and return (stdout, stderr, exit_code)."""
    result = app.test(list(args))
    return result.stdout, result.stderr, result.exit_code


# -- Global flags ----------------------------------------------------------------


def test_db_default_is_empty() -> None:
    db_flag = next(f for f in app.flags if f.name == "db")
    assert db_flag.default == ""


def test_format_default_is_table() -> None:
    fmt_flag = next(f for f in app.flags if f.name == "format")
    assert fmt_flag.default == "table"


def test_format_choices() -> None:
    fmt_flag = next(f for f in app.flags if f.name == "format")
    assert fmt_flag.choices == ["table", "json"]


def test_format_invalid_choice_fails() -> None:
    _, stderr, code = _test("--format", "xml", "config", "pricing")
    assert code == 1
    assert "xml" in stderr


def test_quiet_flag_exists() -> None:
    quiet_flag = next(f for f in app.flags if f.name == "quiet")
    assert quiet_flag.type is bool


def test_no_command_shows_help() -> None:
    stdout, _, code = _test()
    assert code == 0
    assert "orxt" in stdout.lower()


# -- Structure: all groups exist --------------------------------------------------


def test_all_groups_exist() -> None:
    expected = {"run", "inbox", "trace", "event", "validate", "config"}
    assert set(app._groups.keys()) == expected


# -- Structure: run commands ------------------------------------------------------


def test_run_commands() -> None:
    cmds = set(app._groups["run"].commands.keys())
    assert cmds == {"start", "list", "show", "abort", "pause", "resume"}


def test_run_start_flags() -> None:
    cmd = app._groups["run"].commands["start"]
    flag_names = {f.name for f in cmd.flags}
    assert "config" in flag_names
    assert "intent" in flag_names


def test_run_start_config_required() -> None:
    cmd = app._groups["run"].commands["start"]
    config_flag = next(f for f in cmd.flags if f.name == "config")
    assert config_flag.default is None


def test_run_start_intent_required() -> None:
    cmd = app._groups["run"].commands["start"]
    intent_flag = next(f for f in cmd.flags if f.name == "intent")
    assert intent_flag.default is None


def test_run_start_missing_config_fails() -> None:
    _, stderr, code = _test("run", "start", "--intent", "foo")
    assert code == 1
    assert "config" in stderr.lower()


def test_run_start_missing_intent_fails() -> None:
    _, stderr, code = _test("run", "start", "--config", "conf/run.toml")
    assert code == 1
    assert "intent" in stderr.lower()


def test_run_show_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["show"]
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"
    assert cmd.args[0].required is True


def test_run_abort_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["abort"]
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"


def test_run_pause_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["pause"]
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"


def test_run_resume_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["resume"]
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"


def test_run_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("run")
    assert code == 0
    assert "run" in stdout.lower()


# -- Structure: inbox commands ----------------------------------------------------


def test_inbox_commands() -> None:
    cmds = set(app._groups["inbox"].commands.keys())
    assert cmds == {"list", "show", "respond", "skip", "reject"}


def test_inbox_list_has_run_flag() -> None:
    cmd = app._groups["inbox"].commands["list"]
    flag_names = {f.name for f in cmd.flags}
    assert "run" in flag_names


def test_inbox_list_run_required() -> None:
    cmd = app._groups["inbox"].commands["list"]
    run_flag = next(f for f in cmd.flags if f.name == "run")
    assert run_flag.default is None


def test_inbox_list_has_optional_status() -> None:
    cmd = app._groups["inbox"].commands["list"]
    status_flag = next(f for f in cmd.flags if f.name == "status")
    assert status_flag.default == ""


def test_inbox_list_missing_run_fails() -> None:
    _, stderr, code = _test("inbox", "list")
    assert code == 1
    assert "run" in stderr.lower()


def test_inbox_show_has_item_id_arg() -> None:
    cmd = app._groups["inbox"].commands["show"]
    assert cmd.args[0].name == "item_id"


def test_inbox_respond_has_two_args() -> None:
    cmd = app._groups["inbox"].commands["respond"]
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "item_id" in arg_names
    assert "answer" in arg_names


def test_inbox_skip_has_item_id_arg() -> None:
    cmd = app._groups["inbox"].commands["skip"]
    assert cmd.args[0].name == "item_id"


def test_inbox_reject_has_two_args() -> None:
    cmd = app._groups["inbox"].commands["reject"]
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "item_id" in arg_names
    assert "reason" in arg_names


def test_inbox_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("inbox")
    assert code == 0
    assert "inbox" in stdout.lower()


# -- Structure: trace commands ----------------------------------------------------


def test_trace_commands() -> None:
    cmds = set(app._groups["trace"].commands.keys())
    assert cmds == {"events", "transcript", "search", "tasks", "notepad"}


def test_trace_events_has_run_id_arg() -> None:
    cmd = app._groups["trace"].commands["events"]
    assert cmd.args[0].name == "run_id"


def test_trace_events_has_type_flag() -> None:
    cmd = app._groups["trace"].commands["events"]
    type_flag = next(f for f in cmd.flags if f.name == "type")
    assert type_flag.default == ""


def test_trace_events_has_limit_flag() -> None:
    cmd = app._groups["trace"].commands["events"]
    limit_flag = next(f for f in cmd.flags if f.name == "limit")
    assert limit_flag.type is int
    assert limit_flag.default == 100


def test_trace_transcript_has_session_id_arg() -> None:
    cmd = app._groups["trace"].commands["transcript"]
    assert cmd.args[0].name == "session_id"


def test_trace_search_has_two_args() -> None:
    cmd = app._groups["trace"].commands["search"]
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "session_id" in arg_names
    assert "query" in arg_names


def test_trace_tasks_has_run_id_arg() -> None:
    cmd = app._groups["trace"].commands["tasks"]
    assert cmd.args[0].name == "run_id"


def test_trace_notepad_has_run_id_arg() -> None:
    cmd = app._groups["trace"].commands["notepad"]
    assert cmd.args[0].name == "run_id"


def test_trace_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("trace")
    assert code == 0
    assert "trace" in stdout.lower()


# -- Structure: event commands ----------------------------------------------------


def test_event_commands() -> None:
    cmds = set(app._groups["event"].commands.keys())
    assert cmds == {"fire"}


def test_event_fire_has_two_args() -> None:
    cmd = app._groups["event"].commands["fire"]
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "run_id" in arg_names
    assert "event_name" in arg_names


def test_event_fire_has_optional_payload() -> None:
    cmd = app._groups["event"].commands["fire"]
    payload_flag = next(f for f in cmd.flags if f.name == "payload")
    assert payload_flag.default == ""


def test_event_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("event")
    assert code == 0
    assert "event" in stdout.lower()


# -- Structure: validate commands -------------------------------------------------


def test_validate_commands() -> None:
    cmds = set(app._groups["validate"].commands.keys())
    assert cmds == {"agent", "workflow", "categories"}


def test_validate_agent_has_path_arg() -> None:
    cmd = app._groups["validate"].commands["agent"]
    assert cmd.args[0].name == "path"


def test_validate_workflow_has_path_arg() -> None:
    cmd = app._groups["validate"].commands["workflow"]
    assert cmd.args[0].name == "path"


def test_validate_categories_has_path_arg() -> None:
    cmd = app._groups["validate"].commands["categories"]
    assert cmd.args[0].name == "path"


def test_validate_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("validate")
    assert code == 0
    assert "validate" in stdout.lower()


# -- Structure: config commands ---------------------------------------------------


def test_config_commands() -> None:
    cmds = set(app._groups["config"].commands.keys())
    assert cmds == {"show", "pricing"}


def test_config_show_has_run_id_arg() -> None:
    cmd = app._groups["config"].commands["show"]
    assert cmd.args[0].name == "run_id"


def test_config_pricing_no_args() -> None:
    cmd = app._groups["config"].commands["pricing"]
    assert len(cmd.args) == 0


def test_config_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("config")
    assert code == 0
    assert "config" in stdout.lower()


# -- Total command count -----------------------------------------------------------


def test_total_command_count_is_22() -> None:
    total = sum(len(g.commands) for g in app._groups.values())
    assert total == 22
