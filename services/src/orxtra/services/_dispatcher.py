"""Generic capability dispatcher.

Routes raw dicts to the appropriate service function, validating
parameters via the capability's params model and injecting
infrastructure dependencies from the DispatchContext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.protocols import FilterPredicate

from orxtra.services._registry import get_capability, get_capability_fn

if TYPE_CHECKING:
    import asyncpg

    from orxtra.protocols import DispatchBackend, EventBus


@dataclass(frozen=True)
class DispatchContext:
    """Infrastructure dependencies injected into dispatched calls."""

    pool: asyncpg.Pool | None = None
    dispatch_backend: DispatchBackend | None = None
    event_bus: EventBus | None = None


# Capabilities that require a DispatchBackend instead of a pool.
_DISPATCH_BACKEND_CAPABILITIES: frozenset[str] = frozenset({
    "subscribe",
    "unsubscribe",
    "list_subscriptions",
    "create_source",
    "get_source",
    "get_source_by_slug",
    "list_sources",
    "delete_source",
})

# Capabilities that require no infrastructure at all (pure functions).
_NO_INFRA_CAPABILITIES: frozenset[str] = frozenset({
    "show_pricing",
    "validate_agent",
    "validate_workflow",
    "validate_categories",
})


async def dispatch(
    context: DispatchContext,
    capability_name: str,
    raw_args: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Dispatch a capability call.

    1. Looks up the capability by name
    2. Validates raw_args via the capability's params_model
    3. Determines which infrastructure dependency to inject
    4. Calls the service function and returns the result

    Raises ValueError if the capability is unknown.
    Raises pydantic.ValidationError if raw_args fail validation.
    """
    cap = get_capability(capability_name)
    if cap is None:
        msg = f"Unknown capability: {capability_name!r}"
        raise ValueError(msg)

    # Validate and parse args through the params model
    validated = cap.params_model(**raw_args)
    kwargs = _prepare_kwargs(capability_name, validated)

    fn = get_capability_fn(capability_name)

    # Inject infrastructure dependency
    if capability_name in _NO_INFRA_CAPABILITIES:
        return await fn(**kwargs)

    if capability_name in _DISPATCH_BACKEND_CAPABILITIES:
        if context.dispatch_backend is None:
            msg = f"Capability {capability_name!r} requires a dispatch backend"
            raise ValueError(msg)
        return await fn(context.dispatch_backend, **kwargs)

    # Default: pool-based capabilities
    if context.pool is None:
        msg = f"Capability {capability_name!r} requires a database pool"
        raise ValueError(msg)
    return await fn(context.pool, **kwargs)


def _prepare_kwargs(capability_name: str, validated: Any) -> dict[str, Any]:  # noqa: ANN401
    """Convert a validated params model instance to kwargs for the service function.

    Handles type coercions that the service functions expect:
    - UUID string fields are converted to UUID objects
    - Path string fields are converted to Path objects
    - datetime string fields are converted to datetime objects
    - FilterPredicate dicts are converted to FilterPredicate objects
    """
    raw = validated.model_dump()
    kwargs: dict[str, Any] = {}

    for key, value in raw.items():
        if value is None:
            kwargs[key] = None
            continue

        # UUID fields (identified by json_schema_extra format)
        field_info = type(validated).model_fields.get(key)
        if (
            field_info is not None
            and isinstance(value, str)
            and isinstance(field_info.json_schema_extra, dict)
            and field_info.json_schema_extra.get("format") == "uuid"
        ):
            kwargs[key] = UUID(value)
            continue

        # Path fields for start_run and validate_* commands
        if key in ("config_path", "path") and isinstance(value, str):
            kwargs[key] = Path(value)
            continue

        # datetime fields
        if key == "since" and isinstance(value, str):
            kwargs[key] = datetime.fromisoformat(value)
            continue

        # FilterPredicate for subscribe
        if key == "filter" and isinstance(value, dict):
            kwargs[key] = FilterPredicate(**value)
            continue

        kwargs[key] = value

    return kwargs
