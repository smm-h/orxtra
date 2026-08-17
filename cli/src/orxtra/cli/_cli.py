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
from orxtra.cli import _payload_schemas as schemas
from orxtra.cli._formatters import format_table, to_payload
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


def _require_db(db: str | None) -> str:
    if db is None:
        _die("--db is required for this command")
    return db


def _emit(cli_ctx: strictcli.Context, data: Any) -> None:
    """Supply the machine payload, and render the table outside machine mode.

    The payload call is mode-independent: the framework decides what to do
    with the value. The table is the human rendering, and it is the one thing
    machine mode must not print -- stdout carries the envelope alone.
    """
    cli_ctx.payload(to_payload(data))
    if not cli_ctx.json:
        print(format_table(data))


def _dispatch_and_emit(
    cli_ctx: strictcli.Context,
    db_url: str,
    capability: str,
    args: dict[str, Any],
) -> None:
    """Run a capability through the dispatcher and emit the result."""
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
            _emit(cli_ctx, result)
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
    cli_ctx: strictcli.Context,
    capability: str,
    args: dict[str, Any],
) -> None:
    """Run a capability that does not require a database pool."""
    async def _run() -> None:
        ctx = DispatchContext(auth_context=_operator_auth_context())
        result = await dispatch(ctx, capability, args)
        _emit(cli_ctx, result)

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
            presence="optional",
        ),
    ],
)

# Every command handler absorbs the app-level global flag value it does not
# itself name (--db), so each registration declares forwarding.
_ABSORBS_GLOBALS = strictcli.Forwarding(
    reason="absorbs app-level global flag values the handler does not name",
)

# -- Run group --

run_group = app.group(
    "run",
    help="Manage autonomous workflow run lifecycle "
    "(start, list, show, abort, pause, resume).",
)


@run_group.command(
    name="start",
    help="Start a new autonomous workflow run from a TOML configuration file and a "
    "natural-language intent. Opens a pool against the trace database, verifies the "
    "schema, dispatches start_run under an operator identity, and prints the new "
    "run's UUID on stdout so later run, trace and inbox commands can address it.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.flag(
    name="config",
    type=str,
    help="Filesystem path to the run configuration TOML file.",
    presence="required",
)
@strictcli.flag(
    name="intent",
    type=str,
    help="Natural-language description of what the run should accomplish.",
    presence="required",
)
def cmd_run_start(
    _ctx: strictcli.Context,
    *,
    db: str | None,
    config: str,
    intent: str,
    **_kwargs: object,
) -> None:
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


@run_group.command(
    name="list",
    help="List every run recorded in the trace database, newest first, with the "
    "identifying and status fields the storage layer keeps for each one. Output "
    "renders as a human-readable table, or, under --json, as the machine "
    "document a script or agent consumes directly.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
def cmd_run_list(
    ctx: strictcli.Context,
    *,
    db: str | None,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(ctx, _require_db(db), "list_runs", {})


@run_group.command(
    name="show",
    help="Display the full status report the trace store holds for one run: its "
    "current state, the intent it was started with, and the accounting the storage "
    "layer keeps alongside it. Exits non-zero with a clear message when no run carries "
    "the given identifier. Renders as a table, or as the machine document under "
    "--json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROW,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to display (UUID format).",
    presence="required",
)
def cmd_run_show(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            dispatch_ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
            result = await dispatch(dispatch_ctx, "get_run", {"run_id": run_id})
            if result is None:
                _die(f"run {run_id} not found")
            _emit(ctx, result)
        finally:
            await pool.close()

    asyncio.run(_run())


@run_group.command(
    name="abort",
    help="Send an abort signal to a currently executing workflow run, telling the "
    "scheduler to stop dispatching further tasks for it. The signal travels through "
    "the trace store's run-control channel rather than through dispatch, so a "
    "scheduler running in another process picks it up. Confirms on stdout unless "
    "--quiet.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to abort (UUID format).",
    presence="required",
)
def cmd_run_abort(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_quiet(
        _require_db(db), "abort_run", {"run_id": run_id},
        ctx.quiet, f"run {run_id} aborted",
    )


@run_group.command(
    name="pause",
    help="Suspend a running workflow, telling the scheduler to stop starting new tasks "
    "for it while leaving the run and its task tree intact so it can be resumed later. "
    "The signal travels through the trace store's run-control channel, so a scheduler "
    "in another process observes it. Confirms on stdout unless --quiet.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to pause (UUID format).",
    presence="required",
)
def cmd_run_pause(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_quiet(
        _require_db(db), "pause_run", {"run_id": run_id},
        ctx.quiet, f"run {run_id} paused",
    )


@run_group.command(
    name="resume",
    help="Restart task execution for a workflow that was previously paused, telling "
    "the scheduler to begin dispatching its ready tasks again from the state the pause "
    "left behind. The signal travels through the trace store's run-control channel, so "
    "a scheduler in another process observes it. Confirms on stdout unless --quiet.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to resume (UUID format).",
    presence="required",
)
def cmd_run_resume(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_quiet(
        _require_db(db), "resume_run", {"run_id": run_id},
        ctx.quiet, f"run {run_id} resumed",
    )


# -- Inbox group --

inbox_group = app.group(
    "inbox",
    help="Manage human-in-the-loop inbox items (list, show, respond, skip, reject).",
)


@inbox_group.command(
    name="list",
    help="List the human-in-the-loop inbox items agents have raised for one workflow "
    "run, named by --run. Narrow the listing further with --status to one lifecycle "
    "state such as pending, answered or skipped; omit it to see every item of the run. "
    "Renders as a table, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
@strictcli.flag(
    name="run",
    type=str,
    help="Unique identifier of the run whose inbox items to list (UUID format).",
    presence="required",
)
@strictcli.flag(
    name="status",
    type=str,
    help="Filter inbox items by status (e.g. pending, answered, skipped).",
    presence="optional",
)
def cmd_inbox_list(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run: str,
    status: str | None,
    **_kwargs: object,
) -> None:
    args: dict[str, Any] = {"run_id": run}
    if status is not None:
        args["status"] = status
    _dispatch_and_emit(ctx, _require_db(db), "list_inbox", args)


@inbox_group.command(
    name="show",
    help="Display everything the store holds for one inbox item: the question an agent "
    "asked, the options it offered, the run and task it came from, and its current "
    "resolution state. Takes the item's UUID. Renders as a readable table, or as the "
    "machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROW,
)
@strictcli.arg(
    name="item_id",
    help="Unique identifier of the inbox item to display (UUID format).",
    presence="required",
)
def cmd_inbox_show(
    ctx: strictcli.Context,
    *,
    db: str | None,
    item_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "get_inbox_item", {"item_id": item_id},
    )


@inbox_group.command(
    name="respond",
    help="Submit an answer to a pending inbox item, unblocking the agent that raised "
    "the question and recording your identity as the principal that resolved it. Takes "
    "the item's UUID and the answer text. Prints the updated item, honouring the "
    "global machine document under --json, so you can confirm the response was "
    "recorded.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROW,
)
@strictcli.arg(
    name="item_id",
    help="Unique identifier of the inbox item to respond to (UUID format).",
    presence="required",
)
@strictcli.arg(
    name="answer",
    help="The answer text to submit as a response to this item.",
    presence="required",
)
def cmd_inbox_respond(
    ctx: strictcli.Context,
    *,
    db: str | None,
    item_id: str,
    answer: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "respond_to_inbox",
        {"item_id": item_id, "answer": answer},
    )


@inbox_group.command(
    name="skip",
    help="Resolve a pending inbox item without answering it, telling the agent that "
    "raised the question to proceed without your input. Records your identity as the "
    "principal that resolved the item. Takes the item's UUID and prints the updated "
    "item as a table, or as the machine document under --json, so you can confirm the "
    "outcome.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROW,
)
@strictcli.arg(
    name="item_id",
    help="Unique identifier of the inbox item to skip (UUID format).",
    presence="required",
)
def cmd_inbox_skip(
    ctx: strictcli.Context,
    *,
    db: str | None,
    item_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "skip_inbox_item", {"item_id": item_id},
    )


@inbox_group.command(
    name="reject",
    help="Reject a pending inbox item when none of the options an agent offered is "
    "usable, sending back a reason instead of an answer so the agent can reformulate. "
    "Takes the item's UUID and an explanation. Prints the updated item, honouring the "
    "item as a table, or as the machine document under --json, and records you as the "
    "principal that resolved it.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROW,
)
@strictcli.arg(
    name="item_id",
    help="Unique identifier of the inbox item to reject (UUID format).",
    presence="required",
)
@strictcli.arg(
    name="reason",
    help="Explanation of why the inbox item is being rejected.",
    presence="required",
)
def cmd_inbox_reject(
    ctx: strictcli.Context,
    *,
    db: str | None,
    item_id: str,
    reason: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "reject_inbox_item",
        {"item_id": item_id, "reason": reason},
    )


# -- Trace group --

trace_group = app.group(
    "trace",
    help="Query trace data: events, transcripts, tasks, and notepad entries for runs.",
)


@trace_group.command(
    name="events",
    help="Query the append-only event log the trace store keeps for one workflow run. "
    "Narrow the result with --type to a single event type such as task_started or "
    "tool_call, and cap its size with --limit, which defaults to a hundred. Events "
    "render as a table, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to query events for (UUID format).",
    presence="required",
)
@strictcli.flag(
    name="type",
    type=str,
    help="Filter events by type (e.g. task_started, tool_call).",
    presence="optional",
)
@strictcli.flag(
    name="limit",
    type=int,
    help="Maximum number of events to return from the query.",
    default=100,
)
def cmd_trace_events(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    type: str | None,  # noqa: A002
    limit: int,
    **_kwargs: object,
) -> None:
    args: dict[str, Any] = {"run_id": run_id, "limit": limit}
    if type is not None:
        args["event_type"] = type
    _dispatch_and_emit(ctx, _require_db(db), "query_events", args)


@trace_group.command(
    name="transcript",
    help="Display the complete stored message transcript for one agent session: every "
    "message exchanged with the model, in order, as the session module persisted it. "
    "Takes the session's identifier rather than a run's. The transcript renders as "
    "readable text, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
@strictcli.arg(
    name="session_id",
    help="Unique identifier of the session to show the transcript for.",
    presence="required",
)
def cmd_trace_transcript(
    ctx: strictcli.Context,
    *,
    db: str | None,
    session_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "get_transcript", {"session_id": session_id},
    )


@trace_group.command(
    name="search",
    help="Search one agent session's stored transcript for a substring, case-"
    "insensitively, and return the matching messages rather than the whole "
    "conversation. Takes the session identifier and the text to look for. Matches "
    "render as a table, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
@strictcli.arg(
    name="session_id",
    help="Unique identifier of the session whose transcript to search.",
    presence="required",
)
@strictcli.arg(
    name="query",
    help="Case-insensitive substring to search for in the transcript.",
    presence="required",
)
def cmd_trace_search(
    ctx: strictcli.Context,
    *,
    db: str | None,
    session_id: str,
    query: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "search_transcript",
        {"session_id": session_id, "query": query},
    )


@trace_group.command(
    name="tasks",
    help="List every task recorded for one workflow run with its current status, its "
    "attempt count and its place in the recursive task hierarchy, so you can see which "
    "branch of the tree stalled or retried. Takes the run's UUID. Tasks render as a "
    "table, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to list tasks for (UUID format).",
    presence="required",
)
def cmd_trace_tasks(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "list_tasks", {"run_id": run_id},
    )


@trace_group.command(
    name="notepad",
    help="Show the append-only cross-agent notepad entries written during one workflow "
    "run -- the messages agents left for each other as the run progressed, in the "
    "order they were appended. Takes the run's UUID. Entries render as a readable "
    "table, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROWS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to show notepad entries for (UUID format).",
    presence="required",
)
def cmd_trace_notepad(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    _dispatch_and_emit(
        ctx, _require_db(db), "get_notepad", {"run_id": run_id},
    )


# -- Event group --

event_group = app.group(
    "event",
    help="Fire named events to wake wait-for tasks in running workflows.",
)


@event_group.command(
    name="fire",
    help="Fire a named event into a running workflow to wake any task waiting on it. "
    "Takes the run's UUID and an event name that must match a wait-for task's declared "
    "name; attach structured data with --payload, which must parse as a JSON object. "
    "Prints the stored event's identifier unless --quiet is passed.",
    effect="mutating",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run to fire the event into (UUID format).",
    presence="required",
)
@strictcli.arg(
    name="event_name",
    help="Name of the event to fire (must match a wait-for task's event name).",
    presence="required",
)
@strictcli.flag(
    name="payload",
    type=str,
    help="JSON object payload to attach to the event, given as a literal string.",
    presence="optional",
)
def cmd_event_fire(
    ctx: strictcli.Context, *, db: str | None, run_id: str,
    event_name: str, payload: str | None, **_kwargs: object,
) -> None:
    db_url = _require_db(db)
    # Read the framework value before the closure: `ctx` is rebound inside
    # _run() to a DispatchContext, which has no quiet.
    quiet = ctx.quiet
    parsed_payload: dict[str, Any] | None = None
    if payload is not None:
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

validate_group = app.group(
    "validate",
    help="Validate agent, workflow, and category configuration TOML files.",
)


@validate_group.command(
    name="agent",
    help="Validate an agent definition TOML file against the agent module's strict "
    "schema, without touching the database. Prints every error it finds to stderr and "
    "exits non-zero, or prints 'valid' and exits zero when the file is clean. Suitable "
    "as a pre-commit or CI check over a directory of agent definitions.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="path",
    help="Filesystem path to the agent TOML file to validate.",
    presence="required",
)
def cmd_validate_agent(
    ctx: strictcli.Context,
    *,
    path: str,
    **_kwargs: object,
) -> None:
    # Read the framework value before the closure: `ctx` is rebound inside
    # _run() to a DispatchContext, which has no quiet.
    quiet = ctx.quiet

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


@validate_group.command(
    name="workflow",
    help="Validate a workflow definition TOML file against the strict workflow schema "
    "without touching the database, checking the task declarations it makes and the "
    "dependency structure between them. Prints every error it finds to stderr and "
    "exits non-zero, or prints 'valid' and exits zero when the file is clean.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="path",
    help="Filesystem path to the workflow TOML file to validate.",
    presence="required",
)
def cmd_validate_workflow(
    ctx: strictcli.Context,
    *,
    path: str,
    **_kwargs: object,
) -> None:
    # Read the framework value before the closure: `ctx` is rebound inside
    # _run() to a DispatchContext, which has no quiet.
    quiet = ctx.quiet

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


@validate_group.command(
    name="categories",
    help="Validate a model-categories TOML file against the strict categories schema "
    "without touching the database, checking the multi-provider model routing it "
    "declares. Prints every error it finds to stderr and exits non-zero, or prints "
    "'valid' and exits zero when the file is clean.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
)
@strictcli.arg(
    name="path",
    help="Filesystem path to the categories TOML file to validate.",
    presence="required",
)
def cmd_validate_categories(
    ctx: strictcli.Context,
    *,
    path: str,
    **_kwargs: object,
) -> None:
    # Read the framework value before the closure: `ctx` is rebound inside
    # _run() to a DispatchContext, which has no quiet.
    quiet = ctx.quiet

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

config_group = app.group(
    "config",
    help="Inspect run configuration snapshots and internal pricing tables.",
)


@config_group.command(
    name="show",
    help="Display the frozen configuration snapshot the trace store captured when a "
    "run was started -- the settings that run actually executed under, rather than "
    "whatever the configuration file happens to say today. Takes the run's UUID and "
    "exits non-zero when no such run exists. Renders as a table, or as the machine "
    "document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.ROW,
)
@strictcli.arg(
    name="run_id",
    help="Unique identifier of the run whose config to display (UUID format).",
    presence="required",
)
def cmd_config_show(
    ctx: strictcli.Context,
    *,
    db: str | None,
    run_id: str,
    **_kwargs: object,
) -> None:
    db_url = _require_db(db)

    async def _run() -> None:
        pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
        try:
            await verify_schema(pool)
            dispatch_ctx = DispatchContext(
                pool=pool,
                principal_storage=PgPrincipalStorage(pool),
                kind_registry=KindRegistry(()),
                auth_context=_operator_auth_context(),
            )
            result = await dispatch(dispatch_ctx, "show_config", {"run_id": run_id})
            if result is None:
                _die(f"run {run_id} not found")
            _emit(ctx, result)
        finally:
            await pool.close()

    asyncio.run(_run())


@config_group.command(
    name="pricing",
    help="Display orxtra's internal pricing table for every model it knows about -- "
    "the per-token costs used to denominate run budgets in USD. Needs no database "
    "connection, since the table ships inside the installed package. Renders for a "
    "human, or as the machine document under --json.",
    effect="read_only",
    forwarding=_ABSORBS_GLOBALS,
    payload_schema=schemas.PRICING,
)
def cmd_config_pricing(
    ctx: strictcli.Context,
    **_kwargs: object,
) -> None:
    _dispatch_no_pool(ctx, "show_pricing", {})


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
