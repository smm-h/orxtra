from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, NoReturn

import asyncpg
import strictcli
from orxtra.cli._formatters import format_output
from orxtra.services import DispatchContext, dispatch, verify_schema

# -- Helpers --

def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


def _require_db(db: str) -> str:
    if not db:
        _die("--db is required for this command")
    return db


def _print(data: Any, fmt: str) -> None:  # noqa: ANN401
    print(format_output(data, fmt))


def _dispatch_and_print(
    db_url: str,
    capability: str,
    args: dict[str, Any],
    fmt: str,
) -> None:
    """Run a capability through the dispatcher and print the result."""
    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(pool=pool)
            result = await dispatch(ctx, capability, args)
            _print(result, fmt)
        finally:
            await pool.close()

    asyncio.run(_run())


def _dispatch_quiet(
    db_url: str,
    capability: str,
    args: dict[str, Any],
    quiet: bool,
    success_msg: str,
) -> None:
    """Run a mutating capability and print a confirmation unless quiet."""
    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(pool=pool)
            await dispatch(ctx, capability, args)
            if not quiet:
                print(success_msg)
        finally:
            await pool.close()

    asyncio.run(_run())


def _dispatch_no_pool(
    capability: str,
    args: dict[str, Any],
    fmt: str,
) -> None:
    """Run a capability that does not require a database pool."""
    async def _run() -> None:
        ctx = DispatchContext()
        result = await dispatch(ctx, capability, args)
        _print(result, fmt)

    asyncio.run(_run())


# -- App --

app = strictcli.App(
    name="orxtra",
    help="Autonomous multi-agent AI workflows.",
    flags=[
        strictcli.Flag(
            name="db",
            type=str,
            help="PostgreSQL connection URL.",
            default="",
        ),
        strictcli.Flag(
            name="format",
            type=str,
            help="Output format.",
            default="table",
            choices=["table", "json"],
        ),
        strictcli.Flag(
            name="quiet",
            type=bool,
            default=False,
            help="Suppress non-essential output.",
        ),
    ],
)

# -- Run group --

run_group = app.group("run", help="Run lifecycle commands.")


@run_group.command(name="start", help="Start a run from a config file.")
@strictcli.flag(name="config", type=str, help="Path to run config file.")
@strictcli.flag(name="intent", type=str, help="Intent description for the run.")
def cmd_run_start(*, db: str, config: str, intent: str, **_kwargs: object) -> None:
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(pool=pool)
            run_id = await dispatch(ctx, "start_run", {
                "config_path": config,
                "intent": intent,
            })
            print(run_id)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="list", help="List all runs, newest first.")
def cmd_run_list(*, db: str, format: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "list_runs", {}, format)


@run_group.command(name="show", help="Show a run's full report.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_show(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(pool=pool)
            result = await dispatch(ctx, "get_run", {"run_id": run_id})
            if result is None:
                _die(f"run {run_id} not found")
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="abort", help="Signal a running run to abort.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_abort(*, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    _dispatch_quiet(
        _require_db(db), "abort_run", {"run_id": run_id},
        quiet, f"run {run_id} aborted",
    )


@run_group.command(name="pause", help="Pause a running run.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_pause(*, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    _dispatch_quiet(
        _require_db(db), "pause_run", {"run_id": run_id},
        quiet, f"run {run_id} paused",
    )


@run_group.command(name="resume", help="Resume a paused run.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_resume(*, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    _dispatch_quiet(
        _require_db(db), "resume_run", {"run_id": run_id},
        quiet, f"run {run_id} resumed",
    )


# -- Inbox group --

inbox_group = app.group("inbox", help="Human inbox commands.")


@inbox_group.command(name="list", help="List inbox items.")
@strictcli.flag(name="run", type=str, help="Run ID to filter by.")
@strictcli.flag(name="status", type=str, help="Status filter.", default="")
def cmd_inbox_list(
    *, db: str, format: str, run: str, status: str, **_kwargs: object,  # noqa: A002
) -> None:
    args: dict[str, Any] = {"run_id": run}
    if status:
        args["status"] = status
    _dispatch_and_print(_require_db(db), "list_inbox", args, format)


@inbox_group.command(name="show", help="Show a single inbox item.")
@strictcli.arg(name="item_id", help="Inbox item ID.")
def cmd_inbox_show(*, db: str, format: str, item_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "get_inbox_item", {"item_id": item_id}, format)


@inbox_group.command(name="respond", help="Answer an inbox item.")
@strictcli.arg(name="item_id", help="Inbox item ID.")
@strictcli.arg(name="answer", help="The answer text.")
def cmd_inbox_respond(
    *, db: str, format: str, item_id: str, answer: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "respond_to_inbox",
        {"item_id": item_id, "answer": answer}, format,
    )


@inbox_group.command(name="skip", help="Skip an inbox item.")
@strictcli.arg(name="item_id", help="Inbox item ID.")
def cmd_inbox_skip(*, db: str, format: str, item_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "skip_inbox_item", {"item_id": item_id}, format)


@inbox_group.command(name="reject", help="Reject an inbox item (options insufficient).")
@strictcli.arg(name="item_id", help="Inbox item ID.")
@strictcli.arg(name="reason", help="Reason for rejection.")
def cmd_inbox_reject(
    *, db: str, format: str, item_id: str, reason: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "reject_inbox_item",
        {"item_id": item_id, "reason": reason}, format,
    )


# -- Trace group --

trace_group = app.group("trace", help="Trace and event query commands.")


@trace_group.command(name="events", help="Query events for a run.")
@strictcli.arg(name="run_id", help="Run ID.")
@strictcli.flag(name="type", type=str, help="Filter by event type.", default="")
@strictcli.flag(name="limit", type=int, help="Maximum events to return.", default=100)
def cmd_trace_events(
    *, db: str, format: str, run_id: str, type: str, limit: int, **_kwargs: object,  # noqa: A002
) -> None:
    args: dict[str, Any] = {"run_id": run_id, "limit": limit}
    if type:
        args["event_type"] = type
    _dispatch_and_print(_require_db(db), "query_events", args, format)


@trace_group.command(name="transcript", help="Show a session's full transcript.")
@strictcli.arg(name="session_id", help="Session ID.")
def cmd_trace_transcript(
    *, db: str, format: str, session_id: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "get_transcript", {"session_id": session_id}, format,
    )


@trace_group.command(
    name="search",
    help="Search a transcript (case-insensitive substring).",
)
@strictcli.arg(name="session_id", help="Session ID.")
@strictcli.arg(name="query", help="Search query.")
def cmd_trace_search(
    *, db: str, format: str, session_id: str, query: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "search_transcript",
        {"session_id": session_id, "query": query}, format,
    )


@trace_group.command(name="tasks", help="Show task statuses and attempt counts.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_trace_tasks(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "list_tasks", {"run_id": run_id}, format)


@trace_group.command(name="notepad", help="Show notepad entries.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_trace_notepad(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "get_notepad", {"run_id": run_id}, format)


# -- Event group --

event_group = app.group("event", help="Event firing commands.")


@event_group.command(name="fire", help="Fire a named event for wait-for tasks.")
@strictcli.arg(name="run_id", help="Run ID.")
@strictcli.arg(name="event_name", help="Event name.")
@strictcli.flag(name="payload", type=str, help="JSON payload.", default="")
def cmd_event_fire(
    *, db: str, quiet: bool, run_id: str,
    event_name: str, payload: str, **_kwargs: object,
) -> None:
    db_url = _require_db(db)
    parsed_payload: dict[str, Any] | None = None
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            _die(f"invalid JSON payload: {exc}")
        if not isinstance(parsed, dict):
            _die("payload must be a JSON object")
        parsed_payload = parsed

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(pool=pool)
            event_id, _inserted = await dispatch(ctx, "fire_event", {
                "run_id": run_id,
                "event_name": event_name,
                "payload": parsed_payload,
            })
            if not quiet:
                print(f"event {event_name!r} fired for run {run_id} (id={event_id})")
        finally:
            await pool.close()

    asyncio.run(_run())


# -- Validate group --

validate_group = app.group("validate", help="Validate configuration files.")


@validate_group.command(name="agent", help="Validate an agent TOML file.")
@strictcli.arg(name="path", help="Path to agent TOML file.")
def cmd_validate_agent(*, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        ctx = DispatchContext()
        errors = await dispatch(ctx, "validate_agent", {"path": path})
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print("valid")

    asyncio.run(_run())


@validate_group.command(name="workflow", help="Validate a workflow TOML file.")
@strictcli.arg(name="path", help="Path to workflow TOML file.")
def cmd_validate_workflow(*, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        ctx = DispatchContext()
        errors = await dispatch(ctx, "validate_workflow", {"path": path})
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print("valid")

    asyncio.run(_run())


@validate_group.command(name="categories", help="Validate a categories TOML file.")
@strictcli.arg(name="path", help="Path to categories TOML file.")
def cmd_validate_categories(*, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        ctx = DispatchContext()
        errors = await dispatch(ctx, "validate_categories", {"path": path})
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print("valid")

    asyncio.run(_run())


# -- Config group --

config_group = app.group("config", help="Configuration commands.")


@config_group.command(name="show", help="Show the config snapshot for a run.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_config_show(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(pool=pool)
            result = await dispatch(ctx, "show_config", {"run_id": run_id})
            if result is None:
                _die(f"run {run_id} not found")
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@config_group.command(name="pricing", help="Show the current internal pricing table.")
def cmd_config_pricing(*, format: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_no_pool("show_pricing", {}, format)


# -- Serve command (from orxtra.api) --

from orxtra.api._cli import register_serve_command

register_serve_command(app)


# -- DB commands (from orxtra.cli._db) --

from orxtra.cli._db import register_db_commands

register_db_commands(app)


# -- Dispatch commands (from orxtra.dispatch) --

from orxtra.dispatch._cli import register_dispatch_commands

register_dispatch_commands(app)


# -- Worker commands (from orxtra.worker) --

from orxtra.worker._cli import register_worker_commands

register_worker_commands(app)


# -- Entry point --

def main() -> None:
    app.run()
