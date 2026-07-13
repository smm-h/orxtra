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
    from orxtra.protocols import Capability, DispatchBackend, EventBus


@dataclass(frozen=True)
class DispatchContext:
    """Infrastructure dependencies injected into dispatched calls."""

    pool: asyncpg.Pool | None = None
    dispatch_backend: DispatchBackend | None = None
    event_bus: EventBus | None = None


# Canonical order in which declared inject tokens are passed positionally to
# the service function, plus the human-readable label used in error messages.
# Every token in Capability.VALID_INJECT_TOKENS must appear here, or dispatch
# will hard-error on a capability that declares it (no silent drop).
_INJECT_ORDER: tuple[str, ...] = ("pool", "dispatch_backend")
_INJECT_LABELS: dict[str, str] = {
    "pool": "a database pool",
    "dispatch_backend": "a dispatch backend",
}


def _resolve_injections(
    context: DispatchContext,
    cap: Capability,
) -> list[Any]:
    """Resolve a capability's declared inject tokens to positional arguments.

    Walks the canonical inject order, and for each token the capability
    declares, reads the matching field off the context. A declared token whose
    context field is None is a hard error naming the capability and the missing
    dependency -- never a silent None pass-through. A declared token the
    dispatcher does not know how to route is also a hard error.
    """
    args: list[Any] = []
    handled: set[str] = set()
    for token in _INJECT_ORDER:
        if token not in cap.injects:
            continue
        value = getattr(context, token)
        if value is None:
            msg = f"Capability {cap.name!r} requires {_INJECT_LABELS[token]}"
            raise ValueError(msg)
        args.append(value)
        handled.add(token)

    unrouted = cap.injects - handled
    if unrouted:
        msg = (
            f"Capability {cap.name!r} declares inject token(s) "
            f"{sorted(unrouted)} that the dispatcher cannot route"
        )
        raise ValueError(msg)

    return args


async def dispatch(
    context: DispatchContext,
    capability_name: str,
    raw_args: dict[str, Any],
) -> Any:
    """Dispatch a capability call.

    1. Looks up the capability by name
    2. Validates raw_args via the capability's params_model
    3. Resolves the infrastructure dependencies the capability declares
       (``cap.injects``) from the DispatchContext
    4. Calls the service function (declared infra first, positionally, then
       the validated kwargs) and returns the result

    Raises ValueError if the capability is unknown or a declared dependency
    is missing from the context.
    Raises pydantic.ValidationError if raw_args fail validation.
    """
    cap = get_capability(capability_name)
    if cap is None:
        msg = f"Unknown capability: {capability_name!r}"
        raise ValueError(msg)

    # Validate and parse args through the params model
    validated = cap.params_model(**raw_args)
    kwargs = _prepare_kwargs(validated)

    fn = get_capability_fn(capability_name)

    infra_args = _resolve_injections(context, cap)
    return await fn(*infra_args, **kwargs)


def _prepare_kwargs(validated: Any) -> dict[str, Any]:
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
