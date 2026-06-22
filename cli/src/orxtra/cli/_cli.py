from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

import asyncpg
import strictcli
from orxtra.cli._formatters import format_output
from orxtra.services import (
    abort_run,
    dump_config,
    fire_event,
    get_inbox_item,
    get_notepad,
    get_run,
    get_transcript,
    list_inbox,
    list_runs,
    list_tasks,
    pause_run,
    query_events,
    reject_inbox_item,
    respond_to_inbox,
    resume_run,
    search_transcript,
    show_pricing,
    skip_inbox_item,
    start_run_from_file,
    validate_agent,
    validate_categories,
    validate_workflow,
)
from orxtra.trace import TraceWriter

# -- Helpers --

def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


def _parse_uuid(raw: str, label: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        _die(f"invalid {label}: {raw!r}")


def _require_db(db: str) -> str:
    if not db:
        _die("--db is required for this command")
    return db


def _print(data: Any, fmt: str) -> None:  # noqa: ANN401
    print(format_output(data, fmt))


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
            run_id = await start_run_from_file(pool, intent, Path(config))
            print(run_id)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="list", help="List all runs, newest first.")
def cmd_run_list(*, db: str, format: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await list_runs(pool)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="show", help="Show a run's full report.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_show(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await get_run(pool, rid)
            if result is None:
                _die(f"run {rid} not found")
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="abort", help="Signal a running run to abort.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_abort(*, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await abort_run(pool, rid)
            if not quiet:
                print(f"run {rid} aborted")
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="pause", help="Pause a running run.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_pause(*, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await pause_run(pool, rid)
            if not quiet:
                print(f"run {rid} paused")
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="resume", help="Resume a paused run.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_run_resume(*, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await resume_run(pool, rid)
            if not quiet:
                print(f"run {rid} resumed")
        finally:
            await pool.close()

    asyncio.run(_run())


# -- Inbox group --

inbox_group = app.group("inbox", help="Human inbox commands.")


@inbox_group.command(name="list", help="List inbox items.")
@strictcli.flag(name="run", type=str, help="Run ID to filter by.")
@strictcli.flag(name="status", type=str, help="Status filter.", default="")
def cmd_inbox_list(
    *, db: str, format: str, run: str, status: str, **_kwargs: object,  # noqa: A002
) -> None:
    db_url = _require_db(db)
    rid = _parse_uuid(run, "run_id")
    status_filter = status or None

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await list_inbox(pool, rid, status_filter)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@inbox_group.command(name="show", help="Show a single inbox item.")
@strictcli.arg(name="item_id", help="Inbox item ID.")
def cmd_inbox_show(*, db: str, format: str, item_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)
    iid = _parse_uuid(item_id, "item_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await get_inbox_item(pool, iid)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@inbox_group.command(name="respond", help="Answer an inbox item.")
@strictcli.arg(name="item_id", help="Inbox item ID.")
@strictcli.arg(name="answer", help="The answer text.")
def cmd_inbox_respond(
    *, db: str, format: str, item_id: str, answer: str, **_kwargs: object,  # noqa: A002
) -> None:
    db_url = _require_db(db)
    iid = _parse_uuid(item_id, "item_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await respond_to_inbox(pool, iid, answer)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@inbox_group.command(name="skip", help="Skip an inbox item.")
@strictcli.arg(name="item_id", help="Inbox item ID.")
def cmd_inbox_skip(*, db: str, format: str, item_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)
    iid = _parse_uuid(item_id, "item_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await skip_inbox_item(pool, iid)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@inbox_group.command(name="reject", help="Reject an inbox item (options insufficient).")
@strictcli.arg(name="item_id", help="Inbox item ID.")
@strictcli.arg(name="reason", help="Reason for rejection.")
def cmd_inbox_reject(
    *, db: str, format: str, item_id: str, reason: str, **_kwargs: object,  # noqa: A002
) -> None:
    db_url = _require_db(db)
    iid = _parse_uuid(item_id, "item_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await reject_inbox_item(pool, iid, reason)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


# -- Trace group --

trace_group = app.group("trace", help="Trace and event query commands.")


@trace_group.command(name="events", help="Query events for a run.")
@strictcli.arg(name="run_id", help="Run ID.")
@strictcli.flag(name="type", type=str, help="Filter by event type.", default="")
@strictcli.flag(name="limit", type=int, help="Maximum events to return.", default=100)
def cmd_trace_events(
    *, db: str, format: str, run_id: str, type: str, limit: int, **_kwargs: object,  # noqa: A002
) -> None:
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")
    event_type = type or None

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await query_events(pool, rid, event_type=event_type, limit=limit)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@trace_group.command(name="transcript", help="Show a session's full transcript.")
@strictcli.arg(name="session_id", help="Session ID.")
def cmd_trace_transcript(
    *, db: str, format: str, session_id: str, **_kwargs: object,  # noqa: A002
) -> None:
    db_url = _require_db(db)
    sid = _parse_uuid(session_id, "session_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await get_transcript(pool, sid)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@trace_group.command(
    name="search",
    help="Search a transcript (case-insensitive substring).",
)
@strictcli.arg(name="session_id", help="Session ID.")
@strictcli.arg(name="query", help="Search query.")
def cmd_trace_search(
    *, db: str, format: str, session_id: str, query: str, **_kwargs: object,  # noqa: A002
) -> None:
    db_url = _require_db(db)
    sid = _parse_uuid(session_id, "session_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await search_transcript(pool, sid, query)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@trace_group.command(name="tasks", help="Show task statuses and attempt counts.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_trace_tasks(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await list_tasks(pool, rid)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@trace_group.command(name="notepad", help="Show notepad entries.")
@strictcli.arg(name="run_id", help="Run ID.")
def cmd_trace_notepad(*, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await get_notepad(pool, rid)
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


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
    rid = _parse_uuid(run_id, "run_id")
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
            writer = TraceWriter(pool)
            event_id = await fire_event(writer, rid, event_name, parsed_payload)
            if not quiet:
                print(f"event {event_name!r} fired for run {rid} (id={event_id})")
        finally:
            await pool.close()

    asyncio.run(_run())


# -- Validate group --

validate_group = app.group("validate", help="Validate configuration files.")


@validate_group.command(name="agent", help="Validate an agent TOML file.")
@strictcli.arg(name="path", help="Path to agent TOML file.")
def cmd_validate_agent(*, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        errors = await validate_agent(Path(path))
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
        errors = await validate_workflow(Path(path))
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
        errors = await validate_categories(Path(path))
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
    rid = _parse_uuid(run_id, "run_id")

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            result = await dump_config(pool, rid)
            if result is None:
                _die(f"run {rid} not found")
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@config_group.command(name="pricing", help="Show the current internal pricing table.")
def cmd_config_pricing(*, format: str, **_kwargs: object) -> None:  # noqa: A002
    async def _run() -> None:
        result = await show_pricing()
        _print(result, format)

    asyncio.run(_run())


# -- Entry point --

def main() -> None:
    app.run()
