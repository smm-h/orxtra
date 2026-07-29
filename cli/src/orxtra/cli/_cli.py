from __future__ import annotations

import asyncio
import importlib.metadata
import json
import sys
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import uuid4

import asyncpg
import strictcli
from orxtra.cli._formatters import format_output
from orxtra.identity import KindRegistry, PgPrincipalStorage
from orxtra.protocols import ALL_SCOPES, AuthContext, TrustTier
from orxtra.services import DispatchContext, dispatch, verify_schema

# -- Helpers --

def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


def _operator_auth_context() -> AuthContext:
    """Build the local operator's auth context.

    The CLI talks straight to the database with no HTTP layer in front of
    it -- it is the local-trust path. Under the single-operator model the
    local operator simply acts as the system: SYSTEM trust tier, every
    scope, and no backing consumer record (``consumer_id`` is None, which
    the resolver maps to the seeded system principal). The context lives
    for exactly one command, so it never expires.
    """
    return AuthContext(
        id=uuid4(),
        consumer_id=None,
        scopes=ALL_SCOPES,
        trust_tier=TrustTier.SYSTEM,
        authenticated_via="cli-local",
        issued_at=datetime.now(tz=UTC),
        expires_at=None,
    )


def _require_db(db: str) -> str:
    if not db:
        _die("--db is required for this command")
    return db


def _print(data: Any, fmt: str) -> None:
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
            ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
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
            ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
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
        ctx = DispatchContext(auth_context=_operator_auth_context())
        result = await dispatch(ctx, capability, args)
        _print(result, fmt)

    asyncio.run(_run())


# -- App --

app = strictcli.App(
    name="orxtra",
    help="Autonomous multi-agent AI workflows.",
    version=importlib.metadata.version("orxtra"),
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

run_group = app.group("run", help="Manage autonomous workflow run lifecycle (start, list, show, abort, pause, resume).")


@run_group.command(name="start", help="Start a new autonomous workflow run from a configuration file.")
@strictcli.flag(name="config", type=str, help="Filesystem path to the run configuration TOML file.")
@strictcli.flag(name="intent", type=str, help="Natural-language description of what the run should accomplish.")
def cmd_run_start(ctx, *, db: str, config: str, intent: str, **_kwargs: object) -> None:
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
            run_id = await dispatch(ctx, "start_run", {
                "config_path": config,
                "intent": intent,
            })
            print(run_id)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="list", help="List all runs in the database, ordered newest first.")
def cmd_run_list(ctx, *, db: str, format: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "list_runs", {}, format)


@run_group.command(name="show", help="Display the full status report for a specific run.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to display (UUID format).")
def cmd_run_show(ctx, *, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
            result = await dispatch(ctx, "get_run", {"run_id": run_id})
            if result is None:
                _die(f"run {run_id} not found")
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(name="abort", help="Send an abort signal to stop a currently running workflow.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to abort (UUID format).")
def cmd_run_abort(ctx, *, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    _dispatch_quiet(
        _require_db(db), "abort_run", {"run_id": run_id},
        quiet, f"run {run_id} aborted",
    )


@run_group.command(name="pause", help="Pause a running workflow run, suspending task execution.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to pause (UUID format).")
def cmd_run_pause(ctx, *, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    _dispatch_quiet(
        _require_db(db), "pause_run", {"run_id": run_id},
        quiet, f"run {run_id} paused",
    )


@run_group.command(name="resume", help="Resume a previously paused workflow run, restarting task execution.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to resume (UUID format).")
def cmd_run_resume(ctx, *, db: str, quiet: bool, run_id: str, **_kwargs: object) -> None:
    _dispatch_quiet(
        _require_db(db), "resume_run", {"run_id": run_id},
        quiet, f"run {run_id} resumed",
    )


# -- Inbox group --

inbox_group = app.group("inbox", help="Manage human-in-the-loop inbox items (list, show, respond, skip, reject).")


@inbox_group.command(name="list", help="List pending human-in-the-loop inbox items, optionally filtered.")
@strictcli.flag(name="run", type=str, help="Filter inbox items by run ID (only show items for this run).")
@strictcli.flag(name="status", type=str, help="Filter inbox items by status (e.g. pending, answered, skipped).", default="")
def cmd_inbox_list(
    ctx, *, db: str, format: str, run: str, status: str, **_kwargs: object,  # noqa: A002
) -> None:
    args: dict[str, Any] = {"run_id": run}
    if status:
        args["status"] = status
    _dispatch_and_print(_require_db(db), "list_inbox", args, format)


@inbox_group.command(name="show", help="Display the full details of a single inbox item by ID.")
@strictcli.arg(name="item_id", help="Unique identifier of the inbox item to display (UUID format).")
def cmd_inbox_show(ctx, *, db: str, format: str, item_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "get_inbox_item", {"item_id": item_id}, format)


@inbox_group.command(name="respond", help="Submit an answer to a pending inbox item from a workflow run.")
@strictcli.arg(name="item_id", help="Unique identifier of the inbox item to respond to (UUID format).")
@strictcli.arg(name="answer", help="The answer text to submit as a response to this item.")
def cmd_inbox_respond(
    ctx, *, db: str, format: str, item_id: str, answer: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "respond_to_inbox",
        {"item_id": item_id, "answer": answer}, format,
    )


@inbox_group.command(name="skip", help="Skip a pending inbox item without providing an answer.")
@strictcli.arg(name="item_id", help="Unique identifier of the inbox item to skip (UUID format).")
def cmd_inbox_skip(ctx, *, db: str, format: str, item_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(
        _require_db(db), "skip_inbox_item", {"item_id": item_id}, format,
    )


@inbox_group.command(name="reject", help="Reject a pending inbox item, indicating the provided options are insufficient.")
@strictcli.arg(name="item_id", help="Unique identifier of the inbox item to reject (UUID format).")
@strictcli.arg(name="reason", help="Explanation of why the inbox item is being rejected.")
def cmd_inbox_reject(
    ctx, *, db: str, format: str, item_id: str, reason: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "reject_inbox_item",
        {"item_id": item_id, "reason": reason}, format,
    )


# -- Trace group --

trace_group = app.group("trace", help="Query trace data: events, transcripts, tasks, and notepad entries for runs.")


@trace_group.command(name="events", help="Query the stored event log for a specific workflow run.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to query events for (UUID format).")
@strictcli.flag(name="type", type=str, help="Filter events by type (e.g. task_started, tool_call).", default="")
@strictcli.flag(name="limit", type=int, help="Maximum number of events to return from the query.", default=100)
def cmd_trace_events(
    ctx, *, db: str, format: str, run_id: str, type: str, limit: int, **_kwargs: object,  # noqa: A002
) -> None:
    args: dict[str, Any] = {"run_id": run_id, "limit": limit}
    if type:
        args["event_type"] = type
    _dispatch_and_print(_require_db(db), "query_events", args, format)


@trace_group.command(name="transcript", help="Display the full message transcript for an agent session.")
@strictcli.arg(name="session_id", help="Unique identifier of the session to show the transcript for.")
def cmd_trace_transcript(
    ctx, *, db: str, format: str, session_id: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "get_transcript", {"session_id": session_id}, format,
    )


@trace_group.command(
    name="search",
    help="Search a session transcript for matching text (case-insensitive).",
)
@strictcli.arg(name="session_id", help="Unique identifier of the session whose transcript to search.")
@strictcli.arg(name="query", help="Case-insensitive substring to search for in the transcript.")
def cmd_trace_search(
    ctx, *, db: str, format: str, session_id: str, query: str, **_kwargs: object,  # noqa: A002
) -> None:
    _dispatch_and_print(
        _require_db(db), "search_transcript",
        {"session_id": session_id, "query": query}, format,
    )


@trace_group.command(name="tasks", help="Show task statuses, attempt counts, and hierarchy for a run.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to list tasks for (UUID format).")
def cmd_trace_tasks(ctx, *, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "list_tasks", {"run_id": run_id}, format)


@trace_group.command(name="notepad", help="Show cross-agent notepad entries for a workflow run.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to show notepad entries for (UUID format).")
def cmd_trace_notepad(ctx, *, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_and_print(_require_db(db), "get_notepad", {"run_id": run_id}, format)


# -- Event group --

event_group = app.group("event", help="Fire named events to wake wait-for tasks in running workflows.")


@event_group.command(name="fire", help="Fire a named event to wake wait-for tasks in a running workflow.")
@strictcli.arg(name="run_id", help="Unique identifier of the run to fire the event into (UUID format).")
@strictcli.arg(name="event_name", help="Name of the event to fire (must match a wait-for task's event name).")
@strictcli.flag(name="payload", type=str, help="Optional JSON object payload to attach to the event.", default="")
def cmd_event_fire(
    ctx, *, db: str, quiet: bool, run_id: str,
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
            ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
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

validate_group = app.group("validate", help="Validate agent, workflow, and category configuration TOML files.")


@validate_group.command(name="agent", help="Validate an agent definition TOML file for schema errors.")
@strictcli.arg(name="path", help="Filesystem path to the agent TOML file to validate.")
def cmd_validate_agent(ctx, *, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        ctx = DispatchContext(auth_context=_operator_auth_context())
        errors = await dispatch(ctx, "validate_agent", {"path": path})
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print("valid")

    asyncio.run(_run())


@validate_group.command(name="workflow", help="Validate a workflow definition TOML file for schema errors.")
@strictcli.arg(name="path", help="Filesystem path to the workflow TOML file to validate.")
def cmd_validate_workflow(ctx, *, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        ctx = DispatchContext(auth_context=_operator_auth_context())
        errors = await dispatch(ctx, "validate_workflow", {"path": path})
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print("valid")

    asyncio.run(_run())


@validate_group.command(name="categories", help="Validate a model categories TOML file for schema errors.")
@strictcli.arg(name="path", help="Filesystem path to the categories TOML file to validate.")
def cmd_validate_categories(ctx, *, quiet: bool, path: str, **_kwargs: object) -> None:
    async def _run() -> None:
        ctx = DispatchContext(auth_context=_operator_auth_context())
        errors = await dispatch(ctx, "validate_categories", {"path": path})
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print("valid")

    asyncio.run(_run())


# -- Config group --

config_group = app.group("config", help="Inspect run configuration snapshots and internal pricing tables.")


@config_group.command(name="show", help="Display the frozen configuration snapshot stored for a run.")
@strictcli.arg(name="run_id", help="Unique identifier of the run whose config to display (UUID format).")
def cmd_config_show(ctx, *, db: str, format: str, run_id: str, **_kwargs: object) -> None:  # noqa: A002
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
            result = await dispatch(ctx, "show_config", {"run_id": run_id})
            if result is None:
                _die(f"run {run_id} not found")
            _print(result, format)
        finally:
            await pool.close()

    asyncio.run(_run())


@config_group.command(name="pricing", help="Display the current internal pricing table for all supported models.")
def cmd_config_pricing(ctx, *, format: str, **_kwargs: object) -> None:  # noqa: A002
    _dispatch_no_pool("show_pricing", {}, format)


# Registration imports below run after app construction on purpose
# (each module registers its commands against the constructed app).
# -- Serve command (from orxtra.api) --

from orxtra.api import register_serve_command  # noqa: E402

register_serve_command(app)


# -- DB commands (from orxtra.cli._db) --

from orxtra.cli._db import register_db_commands  # noqa: E402

register_db_commands(app)


# -- Dispatch commands --

from orxtra.cli._dispatch import register_dispatch_commands  # noqa: E402

register_dispatch_commands(app)


# -- Worker commands (from orxtra.worker) --

from orxtra.worker import register_worker_commands  # noqa: E402

register_worker_commands(app)


# -- Entry point --

def main() -> None:
    app.run()
