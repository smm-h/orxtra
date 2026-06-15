# Temporary argparse implementation -- will be replaced by strictcli.
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from orxt.cli._formatters import format_output
from orxt.services import (
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

if TYPE_CHECKING:
    _SubAction = argparse._SubParsersAction[argparse.ArgumentParser]  # noqa: SLF001


def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


def _parse_uuid(raw: str, label: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        _die(f"invalid {label}: {raw!r}")


def _add_run_subparsers(sub: _SubAction) -> None:
    run_parser = sub.add_parser("run")
    run_sub = run_parser.add_subparsers(dest="subcommand")
    run_sub.required = True

    start = run_sub.add_parser("start")
    start.add_argument("--config", required=True)
    start.add_argument("--intent", required=True)

    run_sub.add_parser("list")

    show = run_sub.add_parser("show")
    show.add_argument("run_id")

    abort = run_sub.add_parser("abort")
    abort.add_argument("run_id")

    pause = run_sub.add_parser("pause")
    pause.add_argument("run_id")

    resume = run_sub.add_parser("resume")
    resume.add_argument("run_id")


def _add_inbox_subparsers(sub: _SubAction) -> None:
    inbox_parser = sub.add_parser("inbox")
    inbox_sub = inbox_parser.add_subparsers(dest="subcommand")
    inbox_sub.required = True

    ls = inbox_sub.add_parser("list")
    ls.add_argument("--run", required=True, dest="run_id")
    ls.add_argument("--status", default=None)

    show = inbox_sub.add_parser("show")
    show.add_argument("item_id")

    respond = inbox_sub.add_parser("respond")
    respond.add_argument("item_id")
    respond.add_argument("answer")

    skip = inbox_sub.add_parser("skip")
    skip.add_argument("item_id")

    reject = inbox_sub.add_parser("reject")
    reject.add_argument("item_id")
    reject.add_argument("reason")


def _add_trace_subparsers(sub: _SubAction) -> None:
    trace_parser = sub.add_parser("trace")
    trace_sub = trace_parser.add_subparsers(dest="subcommand")
    trace_sub.required = True

    events = trace_sub.add_parser("events")
    events.add_argument("run_id")
    events.add_argument("--type", default=None, dest="event_type")
    events.add_argument("--limit", type=int, default=100)

    transcript = trace_sub.add_parser("transcript")
    transcript.add_argument("session_id")

    search = trace_sub.add_parser("search")
    search.add_argument("session_id")
    search.add_argument("query")

    tasks = trace_sub.add_parser("tasks")
    tasks.add_argument("run_id")

    notepad = trace_sub.add_parser("notepad")
    notepad.add_argument("run_id")


def _add_event_subparsers(sub: _SubAction) -> None:
    event_parser = sub.add_parser("event")
    event_sub = event_parser.add_subparsers(dest="subcommand")
    event_sub.required = True

    fire = event_sub.add_parser("fire")
    fire.add_argument("run_id")
    fire.add_argument("event_name")
    fire.add_argument("--payload", default=None)


def _add_validate_subparsers(sub: _SubAction) -> None:
    validate_parser = sub.add_parser("validate")
    validate_sub = validate_parser.add_subparsers(dest="subcommand")
    validate_sub.required = True

    agent = validate_sub.add_parser("agent")
    agent.add_argument("path")

    workflow = validate_sub.add_parser("workflow")
    workflow.add_argument("path")

    categories = validate_sub.add_parser("categories")
    categories.add_argument("path")


def _add_config_subparsers(sub: _SubAction) -> None:
    config_parser = sub.add_parser("config")
    config_sub = config_parser.add_subparsers(dest="subcommand")
    config_sub.required = True

    show = config_sub.add_parser("show")
    show.add_argument("run_id")

    config_sub.add_parser("pricing")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orxt")
    parser.add_argument("--db", default=None)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--quiet", action="store_true")

    sub = parser.add_subparsers(dest="command")
    sub.required = True

    _add_run_subparsers(sub)
    _add_inbox_subparsers(sub)
    _add_trace_subparsers(sub)
    _add_event_subparsers(sub)
    _add_validate_subparsers(sub)
    _add_config_subparsers(sub)

    return parser


def _require_db(args: argparse.Namespace) -> str:
    db_url: str | None = args.db
    if db_url is None:
        _die("--db is required for this command")
    return db_url


def _print(data: Any, args: argparse.Namespace) -> None:  # noqa: ANN401
    print(format_output(data, args.format))


# -- Run handlers --


async def _run_start(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        run_id = await start_run_from_file(pool, args.intent, Path(args.config))
        print(run_id)
    finally:
        await pool.close()


async def _run_list(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await list_runs(pool)
        _print(result, args)
    finally:
        await pool.close()


async def _run_show(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await get_run(pool, run_id)
        if result is None:
            _die(f"run {run_id} not found")
        _print(result, args)
    finally:
        await pool.close()


async def _run_abort(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        await abort_run(pool, run_id)
        if not args.quiet:
            print(f"run {run_id} aborted")
    finally:
        await pool.close()


async def _run_pause(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        await pause_run(pool, run_id)
        if not args.quiet:
            print(f"run {run_id} paused")
    finally:
        await pool.close()


async def _run_resume(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        await resume_run(pool, run_id)
        if not args.quiet:
            print(f"run {run_id} resumed")
    finally:
        await pool.close()


# -- Inbox handlers --


async def _inbox_list(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await list_inbox(pool, run_id, args.status)
        _print(result, args)
    finally:
        await pool.close()


async def _inbox_show(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    item_id = _parse_uuid(args.item_id, "item_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await get_inbox_item(pool, item_id)
        _print(result, args)
    finally:
        await pool.close()


async def _inbox_respond(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    item_id = _parse_uuid(args.item_id, "item_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await respond_to_inbox(pool, item_id, args.answer)
        _print(result, args)
    finally:
        await pool.close()


async def _inbox_skip(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    item_id = _parse_uuid(args.item_id, "item_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await skip_inbox_item(pool, item_id)
        _print(result, args)
    finally:
        await pool.close()


async def _inbox_reject(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    item_id = _parse_uuid(args.item_id, "item_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await reject_inbox_item(pool, item_id, args.reason)
        _print(result, args)
    finally:
        await pool.close()


# -- Trace handlers --


async def _trace_events(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await query_events(
            pool, run_id, event_type=args.event_type, limit=args.limit
        )
        _print(result, args)
    finally:
        await pool.close()


async def _trace_transcript(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    session_id = _parse_uuid(args.session_id, "session_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await get_transcript(pool, session_id)
        _print(result, args)
    finally:
        await pool.close()


async def _trace_search(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    session_id = _parse_uuid(args.session_id, "session_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await search_transcript(pool, session_id, args.query)
        _print(result, args)
    finally:
        await pool.close()


async def _trace_tasks(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await list_tasks(pool, run_id)
        _print(result, args)
    finally:
        await pool.close()


async def _trace_notepad(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await get_notepad(pool, run_id)
        _print(result, args)
    finally:
        await pool.close()


# -- Event handlers --


async def _event_fire(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    payload: dict[str, Any] | None = None
    if args.payload is not None:
        try:
            parsed = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            _die(f"invalid JSON payload: {exc}")
        if not isinstance(parsed, dict):
            _die("payload must be a JSON object")
        payload = parsed
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        await fire_event(pool, run_id, args.event_name, payload)
        if not args.quiet:
            print(f"event {args.event_name!r} fired for run {run_id}")
    finally:
        await pool.close()


# -- Validate handlers --


async def _validate_agent(args: argparse.Namespace) -> None:
    errors = await validate_agent(Path(args.path))
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)
    if not args.quiet:
        print("valid")


async def _validate_workflow(args: argparse.Namespace) -> None:
    errors = await validate_workflow(Path(args.path))
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)
    if not args.quiet:
        print("valid")


async def _validate_categories(args: argparse.Namespace) -> None:
    errors = await validate_categories(Path(args.path))
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)
    if not args.quiet:
        print("valid")


# -- Config handlers --


async def _config_show(args: argparse.Namespace) -> None:
    db_url = _require_db(args)
    run_id = _parse_uuid(args.run_id, "run_id")
    pool: asyncpg.Pool = await asyncpg.create_pool(db_url)
    try:
        result = await dump_config(pool, run_id)
        if result is None:
            _die(f"run {run_id} not found")
        _print(result, args)
    finally:
        await pool.close()


async def _config_pricing(args: argparse.Namespace) -> None:
    result = await show_pricing()
    _print(result, args)


# -- Dispatch --

_DISPATCH: dict[tuple[str, str], Any] = {
    ("run", "start"): _run_start,
    ("run", "list"): _run_list,
    ("run", "show"): _run_show,
    ("run", "abort"): _run_abort,
    ("run", "pause"): _run_pause,
    ("run", "resume"): _run_resume,
    ("inbox", "list"): _inbox_list,
    ("inbox", "show"): _inbox_show,
    ("inbox", "respond"): _inbox_respond,
    ("inbox", "skip"): _inbox_skip,
    ("inbox", "reject"): _inbox_reject,
    ("trace", "events"): _trace_events,
    ("trace", "transcript"): _trace_transcript,
    ("trace", "search"): _trace_search,
    ("trace", "tasks"): _trace_tasks,
    ("trace", "notepad"): _trace_notepad,
    ("event", "fire"): _event_fire,
    ("validate", "agent"): _validate_agent,
    ("validate", "workflow"): _validate_workflow,
    ("validate", "categories"): _validate_categories,
    ("config", "show"): _config_show,
    ("config", "pricing"): _config_pricing,
}


async def _dispatch(args: argparse.Namespace) -> None:
    command: str = args.command
    subcommand: str = args.subcommand
    handler = _DISPATCH.get((command, subcommand))
    if handler is None:
        _die(f"unknown command: {command} {subcommand}")
    await handler(args)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    asyncio.run(_dispatch(args))
