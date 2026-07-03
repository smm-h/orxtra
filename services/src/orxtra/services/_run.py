from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit, urlunsplit

from orxtra.agent import load_agents, load_categories
from orxtra.overseer import load_knowledge_files
from orxtra.protocols import BudgetExhaustionPolicy
from orxtra.scheduler import Scheduler, load_workflow
from orxtra.scheduler._tool_registry import ToolEntry
from orxtra.secrets import create_secret_registry
from orxtra.services._providers import build_transport_registry
from orxtra.tool import load_tool_definitions
from orxtra.trace import (
    RunReport,
    RunSummary,
    StorageBackend,
    TraceWriter,
    read_run_report,
)
from orxtra.trace import list_runs as _list_runs
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxtra.secrets import SecretRegistry


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    workflow_path: Path
    agents_dir: Path
    knowledge_dir: Path
    categories_path: Path
    read_root: Path
    db_url: str
    provider_configs: dict[str, dict[str, str]]
    budget: Decimal
    autonomy_level: str
    budget_exhaustion_policy: BudgetExhaustionPolicy = BudgetExhaustionPolicy.UNLIMITED
    secrets_env: dict[str, str] | None = None
    tools_dir: Path | None = None


_REDACTED = "[REDACTED]"


def _redact_netloc(netloc: str) -> str:
    """Redact the password in a netloc, keeping everything else verbatim.

    Works on the raw string (not parsed properties) so percent-encoded
    userinfo, IPv6 brackets, and host case survive untouched.  Userinfo is
    everything before the last ``@``; the password is everything after the
    first ``:`` within it.
    """
    userinfo, sep, host_port = netloc.rpartition("@")
    if not sep:
        return netloc
    user, colon, _password = userinfo.partition(":")
    if not colon:
        return netloc
    return f"{user}:{_REDACTED}@{host_port}"


def _redact_query(query: str) -> str:
    """Redact the value of any ``password`` query parameter.

    Other parameters are preserved verbatim.  Keys are percent-decoded for
    comparison (libpq decodes URI parameter keys) but emitted as written.
    """
    if not query:
        return query
    parts = []
    for part in query.split("&"):
        key, eq, _value = part.partition("=")
        if eq and unquote(key) == "password":
            parts.append(f"{key}={_REDACTED}")
        else:
            parts.append(part)
    return "&".join(parts)


def _redact_db_url(db_url: str) -> str:
    """Redact password material in a database URL, if present.

    Covers both the userinfo password (``postgres://u:pw@host/db``) and the
    libpq/asyncpg query-parameter form (``postgres://host/db?password=pw``).
    """
    parsed = urlsplit(db_url)
    return urlunsplit(parsed._replace(
        netloc=_redact_netloc(parsed.netloc),
        query=_redact_query(parsed.query),
    ))


def _serialize_config(config: RunConfig) -> dict[str, Any]:
    """Serialize a RunConfig for persistence in the run record.

    Credential-bearing values (provider api_keys, db_url password) are
    redacted to a fixed placeholder -- the snapshot is stored verbatim in
    PostgreSQL and must never contain plaintext secrets.
    """
    data = config.model_dump()
    data["agents_dir"] = str(config.agents_dir)
    data["knowledge_dir"] = str(config.knowledge_dir)
    data["categories_path"] = str(config.categories_path)
    data["read_root"] = str(config.read_root)
    data["workflow_path"] = str(config.workflow_path)
    data["budget"] = str(config.budget)
    data["db_url"] = _redact_db_url(config.db_url)
    if config.tools_dir is not None:
        data["tools_dir"] = str(config.tools_dir)
    data["provider_configs"] = {
        name: {
            key: _REDACTED if key == "api_key" else value
            for key, value in provider.items()
        }
        for name, provider in config.provider_configs.items()
    }
    return data


def _load_custom_tools(
    tools_dir: Path,
    secret_registry: SecretRegistry | None,
) -> list[ToolEntry]:
    """Load data-defined tools and convert to ToolEntry objects.

    Http-type definitions get a real factory via ``build_http_tool``.
    Monty and command types raise NotImplementedError (pending 3.3).
    """
    from orxtra.tool._data_tool_http import build_http_tool  # noqa: PLC0415
    from orxtra.tool._data_tool_types import (  # noqa: PLC0415
        HttpExecution,
    )

    definitions = load_tool_definitions(tools_dir, secret_registry)
    entries: list[ToolEntry] = []
    for defn in definitions:
        # Derive tags from execution type and user-supplied tags.
        derived_tags: set[str] = set()
        if defn.tags:
            derived_tags.update(defn.tags)
        # Derive effect tags from execution config.
        if isinstance(defn.execution, HttpExecution):
            if defn.execution.method in {"GET", "HEAD"}:
                derived_tags.add("readonly")
            else:
                derived_tags.add("mutation")
        else:
            # monty and command: tags are derived from capabilities
            # at execution time (3.3) -- for now mark as mutation.
            derived_tags.add("mutation")

        if isinstance(defn.execution, HttpExecution):
            def _http_factory(
                deps: Any,  # noqa: ANN401
                *,
                _defn: Any = defn,  # noqa: ANN401
                _sr: SecretRegistry | None = secret_registry,
            ) -> Any:  # noqa: ANN401
                return build_http_tool(
                    _defn,
                    secret_registry=_sr,
                    preview_threshold=deps.preview_threshold,
                    preview_lines=deps.preview_lines,
                )

            factory = _http_factory
        else:
            def _placeholder_factory(
                deps: Any,  # noqa: ANN401
                *,
                _name: str = defn.name,
            ) -> Any:  # noqa: ANN401
                msg = (
                    f"Data-defined tool {_name!r} execution not yet "
                    f"implemented (pending phase 3.3)"
                )
                raise NotImplementedError(msg)

            factory = _placeholder_factory

        entries.append(ToolEntry(
            name=defn.name,
            namespace=defn.namespace,
            tags=frozenset(derived_tags),
            factory=factory,
        ))

    return entries


async def start_run(
    pool: asyncpg.Pool | None,
    intent: str,
    config: RunConfig,
    *,
    transport_registry: dict[str, Any] | None = None,
    overseer: Any | None = None,  # noqa: ANN401
    backend: StorageBackend | None = None,
) -> UUID:
    # When a StorageBackend is provided, use it for all operations.
    # Otherwise, create a TraceWriter from the pool (backward compat).
    writer: TraceWriter | StorageBackend
    if backend is not None:
        writer = backend
    else:
        if pool is None:
            msg = "Either pool or backend must be provided"
            raise ValueError(msg)
        writer = TraceWriter(pool)
    run_id = await writer.create_run(
        intent, _serialize_config(config), config.autonomy_level
    )
    try:
        await writer.transition_run(run_id, "running")
        agents = load_agents(config.agents_dir)
        categories = load_categories(config.categories_path)
        if transport_registry is not None:
            registry = transport_registry
        elif config.provider_configs:
            registry = build_transport_registry(config.provider_configs)
        else:
            registry = {}
        secret_registry = (
            create_secret_registry(config.secrets_env)
            if config.secrets_env is not None
            else None
        )
        # Load data-defined tools BEFORE Scheduler construction.
        # The Scheduler registers custom tools and then validates
        # all agents' allow lists -- the ordering guarantee is that
        # data-defined tools are available for that validation.
        custom_tools: list[ToolEntry] | None = None
        if config.tools_dir is not None:
            custom_tools = _load_custom_tools(
                config.tools_dir, secret_registry,
            )
        scheduler = Scheduler(
            trace_writer=writer,
            transport_registry=registry,
            agents=agents,
            categories=categories,
            run_id=run_id,
            read_root=config.read_root,
            pool=pool,
            backend=backend,
            overseer_interface=overseer,
            budget_exhaustion_policy=config.budget_exhaustion_policy,
            budget_limit=config.budget,
            autonomy_level=config.autonomy_level,
            secret_registry=secret_registry,
            custom_tools=custom_tools,
        )
        workflow_config = load_workflow(config.workflow_path)
        await load_knowledge_files(
            config.knowledge_dir, writer, run_id,
        )
        await scheduler.execute_workflow(workflow_config)
        await writer.transition_run(run_id, "completed")
    except Exception:
        await writer.transition_run(run_id, "failed")
        raise
    return run_id


async def start_run_from_file(
    pool: asyncpg.Pool, intent: str, config_path: Path
) -> UUID:
    if not config_path.is_file():  # noqa: ASYNC240
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)
    with config_path.open("rb") as f:
        raw = tomllib.load(f)
    path_keys = (
        "workflow_path",
        "agents_dir",
        "knowledge_dir",
        "categories_path",
        "read_root",
        "tools_dir",
    )
    for key in path_keys:
        if key in raw and isinstance(raw[key], str):
            raw[key] = Path(raw[key])
    if "budget" in raw and not isinstance(raw["budget"], Decimal):
        raw["budget"] = Decimal(str(raw["budget"]))
    config = RunConfig(**raw)
    return await start_run(pool, intent, config)


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> RunReport | None:
    return await read_run_report(pool, run_id)


async def list_runs(pool: asyncpg.Pool) -> list[RunSummary]:
    return await _list_runs(pool)


async def abort_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    await writer.transition_run(run_id, "aborted")


async def pause_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    await writer.transition_run(run_id, "paused")


async def resume_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    await writer.transition_run(run_id, "running")
