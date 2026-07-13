from __future__ import annotations

import tomllib
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit, urlunsplit

import uuid6
from orxtra.agent import load_agents, load_categories
from orxtra.overseer import load_knowledge_files
from orxtra.protocols import KIND_RUN, BudgetExhaustionPolicy
from orxtra.scheduler import Scheduler, ToolEntry, load_workflow
from orxtra.secrets import create_secret_registry
from orxtra.services._injection import (
    build_constraints_refresher,
    build_lessons_refresher,
    build_notepad_refresher,
)
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
    from orxtra.protocols import Principal, PrincipalStorage
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
    Monty and command types use ``build_monty_tool`` and
    ``build_command_tool`` respectively, with capability-derived tags.
    """
    from orxtra.tool import (
        CommandExecution,
        HttpExecution,
        MontyExecution,
        build_command_tool,
        build_http_tool,
        build_monty_tool,
        derive_tags,
    )

    builders: dict[type, Any] = {
        HttpExecution: build_http_tool,
        MontyExecution: build_monty_tool,
        CommandExecution: build_command_tool,
    }

    definitions = load_tool_definitions(tools_dir, secret_registry)
    entries: list[ToolEntry] = []
    for defn in definitions:
        # Derive tags from execution type and user-supplied tags.
        derived_tags: set[str] = set()
        if defn.tags:
            derived_tags.update(defn.tags)

        if isinstance(defn.execution, HttpExecution):
            # HTTP: derive from method.
            if defn.execution.method in {"GET", "HEAD"}:
                derived_tags.add("readonly")
            else:
                derived_tags.add("mutation")
        elif isinstance(defn.execution, MontyExecution):
            # Monty: derive tags from capabilities.
            cap_tags = derive_tags(
                defn.execution.capabilities, defn.tags,
            )
            derived_tags.update(cap_tags)
        elif isinstance(defn.execution, CommandExecution):
            # Command: always mutation.
            derived_tags.add("mutation")

        builder = builders.get(type(defn.execution))
        if builder is None:
            msg = (
                f"Unknown execution type for tool {defn.name!r}: "
                f"{type(defn.execution).__name__}"
            )
            raise TypeError(msg)

        def _factory(
            deps: Any,
            *,
            _defn: Any = defn,
            _builder: Any = builder,
        ) -> Any:
            return _builder(_defn, deps)

        factory: Callable[..., Any] = _factory

        entries.append(ToolEntry(
            name=defn.name,
            namespace=defn.namespace,
            tags=frozenset(derived_tags),
            factory=factory,
        ))

    return entries


async def start_run(
    pool: asyncpg.Pool | None,
    principal_storage: PrincipalStorage,
    caller_principal: Principal,
    intent: str,
    config: RunConfig,
    *,
    transport_registry: dict[str, Any] | None = None,
    overseer: Any | None = None,
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
    # Generate the run id BEFORE minting so the run's principal can exist
    # before the row that FKs into it. The run principal shares the run's id
    # (as external_ref) and carries no display name -- the runs table holds
    # the run's own descriptive data (intent, config), so a run principal is
    # a bare identity anchor, not a duplicate label.
    run_id = uuid6.uuid7()
    # Mint-first: the run's principal must exist before the runs row that FKs
    # into it (created_by), and before any event attributed to the run. If
    # create_run fails after this mint, the orphaned run principal is harmless
    # -- nothing references it, swept by recovery (age-guarded).
    run_principal = await principal_storage.mint_principal(KIND_RUN, run_id, None)
    await writer.create_run(
        intent,
        _serialize_config(config),
        config.autonomy_level,
        run_id=run_id,
        created_by=caller_principal.id,
    )
    # Sweep orphaned run principals from prior crashed runs. Placed after
    # create_run so OUR principal already has its matching runs row and is
    # safe from the sweep. The age guard (5 min) makes this doubly safe.
    from orxtra.services._identity import sweep_orphaned_run_principals

    await sweep_orphaned_run_principals(principal_storage)
    try:
        await writer.transition_run(
            run_id, "running", principal_id=run_principal.id,
        )
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
        # Build refresh callbacks when a StorageBackend is
        # available. These bridge trace readers (and overseer
        # staleness logic) into the scheduler at attempt
        # boundaries, without the scheduler importing overseer.
        refresh_constraints_cb = None
        refresh_lessons_cb = None
        refresh_notepad_cb = None
        if backend is not None:
            refresh_constraints_cb = (
                build_constraints_refresher(backend)
            )
            # Collect relevance tags from all agents for
            # lesson queries.
            all_tags: list[str] = []
            for agent_def in agents.values():
                if agent_def.name:
                    all_tags.append(agent_def.name)
                if agent_def.category:
                    all_tags.append(agent_def.category)
            refresh_lessons_cb = build_lessons_refresher(
                backend, config.read_root, all_tags,
            )
            refresh_notepad_cb = (
                build_notepad_refresher(backend)
            )

        scheduler = Scheduler(
            trace_writer=writer,
            transport_registry=registry,
            agents=agents,
            categories=categories,
            run_id=run_id,
            run_principal_id=run_principal.id,
            read_root=config.read_root,
            pool=pool,
            backend=backend,
            overseer_interface=overseer,
            budget_exhaustion_policy=config.budget_exhaustion_policy,
            budget_limit=config.budget,
            autonomy_level=config.autonomy_level,
            secret_registry=secret_registry,
            custom_tools=custom_tools,
            refresh_constraints=refresh_constraints_cb,
            refresh_lessons=refresh_lessons_cb,
            refresh_notepad=refresh_notepad_cb,
        )
        workflow_config = load_workflow(config.workflow_path)
        await load_knowledge_files(
            config.knowledge_dir, writer, run_id,
        )
        await scheduler.execute_workflow(workflow_config)
        await writer.transition_run(
            run_id, "completed", principal_id=run_principal.id,
        )
    except Exception:
        await writer.transition_run(
            run_id, "failed", principal_id=run_principal.id,
        )
        raise
    return run_id


async def start_run_from_file(
    pool: asyncpg.Pool,
    principal_storage: PrincipalStorage,
    caller_principal: Principal,
    intent: str,
    config_path: Path,
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
    return await start_run(
        pool, principal_storage, caller_principal, intent, config,
    )


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> RunReport | None:
    return await read_run_report(pool, run_id)


async def list_runs(pool: asyncpg.Pool) -> list[RunSummary]:
    return await _list_runs(pool)


async def _resolve_run_principal_id(pool: asyncpg.Pool, run_id: UUID) -> UUID:
    """Resolve the run's own principal id for transition attribution.

    Run control operations (abort/pause/resume) emit a run_transition event
    attributed to the run principal. It was minted at run birth; a missing one
    is a hard error (an invariant violation), never a silent fallback.
    """
    from orxtra.identity import PgPrincipalStorage

    storage = PgPrincipalStorage(pool)
    principal = await storage.get_principal_by_ref(KIND_RUN, run_id)
    if principal is None:
        msg = f"run principal missing for run {run_id}"
        raise RuntimeError(msg)
    return principal.id


async def abort_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    principal_id = await _resolve_run_principal_id(pool, run_id)
    await writer.transition_run(run_id, "aborted", principal_id=principal_id)


async def pause_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    principal_id = await _resolve_run_principal_id(pool, run_id)
    await writer.transition_run(run_id, "paused", principal_id=principal_id)


async def resume_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    principal_id = await _resolve_run_principal_id(pool, run_id)
    await writer.transition_run(run_id, "running", principal_id=principal_id)
