from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing _cli.
_MOCK_MODS = [
    "asyncpg",
    "orxtra.services",
    "orxtra.services._run",
    "orxtra.services._inbox",
    "orxtra.services._trace",
    "orxtra.services._events",
    "orxtra.services._validate",
    "orxtra.services._config",
    "orxtra.trace",
    "orxtra.trace._writer",
]
for _mod in _MOCK_MODS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from orxtra.cli._cli import app  # noqa: E402


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
    assert "orxtra" in stdout.lower()


# -- Structure: all groups exist --------------------------------------------------


def test_all_groups_exist() -> None:
    expected = {"run", "inbox", "trace", "event", "validate", "config"}
    assert set(app._groups.keys()) == expected  # noqa: SLF001


# -- Structure: run commands ------------------------------------------------------


def test_run_commands() -> None:
    cmds = set(app._groups["run"].commands.keys())  # noqa: SLF001
    assert cmds == {"start", "list", "show", "abort", "pause", "resume"}


def test_run_start_flags() -> None:
    cmd = app._groups["run"].commands["start"]  # noqa: SLF001
    flag_names = {f.name for f in cmd.flags}
    assert "config" in flag_names
    assert "intent" in flag_names


def test_run_start_config_required() -> None:
    cmd = app._groups["run"].commands["start"]  # noqa: SLF001
    config_flag = next(f for f in cmd.flags if f.name == "config")
    assert config_flag.default is None


def test_run_start_intent_required() -> None:
    cmd = app._groups["run"].commands["start"]  # noqa: SLF001
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
    cmd = app._groups["run"].commands["show"]  # noqa: SLF001
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"
    assert cmd.args[0].required is True


def test_run_abort_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["abort"]  # noqa: SLF001
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"


def test_run_pause_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["pause"]  # noqa: SLF001
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"


def test_run_resume_has_run_id_arg() -> None:
    cmd = app._groups["run"].commands["resume"]  # noqa: SLF001
    assert len(cmd.args) == 1
    assert cmd.args[0].name == "run_id"


def test_run_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("run")
    assert code == 0
    assert "run" in stdout.lower()


# -- Structure: inbox commands ----------------------------------------------------


def test_inbox_commands() -> None:
    cmds = set(app._groups["inbox"].commands.keys())  # noqa: SLF001
    assert cmds == {"list", "show", "respond", "skip", "reject"}


def test_inbox_list_has_run_flag() -> None:
    cmd = app._groups["inbox"].commands["list"]  # noqa: SLF001
    flag_names = {f.name for f in cmd.flags}
    assert "run" in flag_names


def test_inbox_list_run_required() -> None:
    cmd = app._groups["inbox"].commands["list"]  # noqa: SLF001
    run_flag = next(f for f in cmd.flags if f.name == "run")
    assert run_flag.default is None


def test_inbox_list_has_optional_status() -> None:
    cmd = app._groups["inbox"].commands["list"]  # noqa: SLF001
    status_flag = next(f for f in cmd.flags if f.name == "status")
    assert status_flag.default == ""


def test_inbox_list_missing_run_fails() -> None:
    _, stderr, code = _test("inbox", "list")
    assert code == 1
    assert "run" in stderr.lower()


def test_inbox_show_has_item_id_arg() -> None:
    cmd = app._groups["inbox"].commands["show"]  # noqa: SLF001
    assert cmd.args[0].name == "item_id"


def test_inbox_respond_has_two_args() -> None:
    cmd = app._groups["inbox"].commands["respond"]  # noqa: SLF001
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "item_id" in arg_names
    assert "answer" in arg_names


def test_inbox_skip_has_item_id_arg() -> None:
    cmd = app._groups["inbox"].commands["skip"]  # noqa: SLF001
    assert cmd.args[0].name == "item_id"


def test_inbox_reject_has_two_args() -> None:
    cmd = app._groups["inbox"].commands["reject"]  # noqa: SLF001
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
    cmds = set(app._groups["trace"].commands.keys())  # noqa: SLF001
    assert cmds == {"events", "transcript", "search", "tasks", "notepad"}


def test_trace_events_has_run_id_arg() -> None:
    cmd = app._groups["trace"].commands["events"]  # noqa: SLF001
    assert cmd.args[0].name == "run_id"


def test_trace_events_has_type_flag() -> None:
    cmd = app._groups["trace"].commands["events"]  # noqa: SLF001
    type_flag = next(f for f in cmd.flags if f.name == "type")
    assert type_flag.default == ""


def test_trace_events_has_limit_flag() -> None:
    cmd = app._groups["trace"].commands["events"]  # noqa: SLF001
    limit_flag = next(f for f in cmd.flags if f.name == "limit")
    assert limit_flag.type is int
    assert limit_flag.default == 100


def test_trace_transcript_has_session_id_arg() -> None:
    cmd = app._groups["trace"].commands["transcript"]  # noqa: SLF001
    assert cmd.args[0].name == "session_id"


def test_trace_search_has_two_args() -> None:
    cmd = app._groups["trace"].commands["search"]  # noqa: SLF001
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "session_id" in arg_names
    assert "query" in arg_names


def test_trace_tasks_has_run_id_arg() -> None:
    cmd = app._groups["trace"].commands["tasks"]  # noqa: SLF001
    assert cmd.args[0].name == "run_id"


def test_trace_notepad_has_run_id_arg() -> None:
    cmd = app._groups["trace"].commands["notepad"]  # noqa: SLF001
    assert cmd.args[0].name == "run_id"


def test_trace_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("trace")
    assert code == 0
    assert "trace" in stdout.lower()


# -- Structure: event commands ----------------------------------------------------


def test_event_commands() -> None:
    cmds = set(app._groups["event"].commands.keys())  # noqa: SLF001
    assert cmds == {"fire"}


def test_event_fire_has_two_args() -> None:
    cmd = app._groups["event"].commands["fire"]  # noqa: SLF001
    assert len(cmd.args) == 2
    arg_names = {a.name for a in cmd.args}
    assert "run_id" in arg_names
    assert "event_name" in arg_names


def test_event_fire_has_optional_payload() -> None:
    cmd = app._groups["event"].commands["fire"]  # noqa: SLF001
    payload_flag = next(f for f in cmd.flags if f.name == "payload")
    assert payload_flag.default == ""


def test_event_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("event")
    assert code == 0
    assert "event" in stdout.lower()


# -- Structure: validate commands -------------------------------------------------


def test_validate_commands() -> None:
    cmds = set(app._groups["validate"].commands.keys())  # noqa: SLF001
    assert cmds == {"agent", "workflow", "categories"}


def test_validate_agent_has_path_arg() -> None:
    cmd = app._groups["validate"].commands["agent"]  # noqa: SLF001
    assert cmd.args[0].name == "path"


def test_validate_workflow_has_path_arg() -> None:
    cmd = app._groups["validate"].commands["workflow"]  # noqa: SLF001
    assert cmd.args[0].name == "path"


def test_validate_categories_has_path_arg() -> None:
    cmd = app._groups["validate"].commands["categories"]  # noqa: SLF001
    assert cmd.args[0].name == "path"


def test_validate_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("validate")
    assert code == 0
    assert "validate" in stdout.lower()


# -- Structure: config commands ---------------------------------------------------


def test_config_commands() -> None:
    cmds = set(app._groups["config"].commands.keys())  # noqa: SLF001
    assert cmds == {"show", "pricing"}


def test_config_show_has_run_id_arg() -> None:
    cmd = app._groups["config"].commands["show"]  # noqa: SLF001
    assert cmd.args[0].name == "run_id"


def test_config_pricing_no_args() -> None:
    cmd = app._groups["config"].commands["pricing"]  # noqa: SLF001
    assert len(cmd.args) == 0


def test_config_missing_subcommand_shows_help() -> None:
    stdout, _, code = _test("config")
    assert code == 0
    assert "config" in stdout.lower()


# -- Total command count -----------------------------------------------------------


def test_total_command_count_is_22() -> None:
    total = sum(len(g.commands) for g in app._groups.values())  # noqa: SLF001
    assert total == 22
