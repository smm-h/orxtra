"""Pydantic parameter models for capability-eligible service functions.

Each model captures the user-facing parameters of a service function,
omitting infrastructure dependencies (pool, backend, bus) that are
injected by the dispatcher.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# -- Run params --


class StartRunParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    config_path: str = Field(description="Path to run config file.")
    intent: str = Field(description="Intent description for the run.")


class ListRunsParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class GetRunParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


class AbortRunParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


class PauseRunParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


class ResumeRunParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


# -- Inbox params --


class ListInboxParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})
    status: str | None = Field(
        default=None, description="Status filter."
    )


class GetInboxItemParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    item_id: str = Field(
        description="Inbox item ID.", json_schema_extra={"format": "uuid"}
    )


class RespondToInboxParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    item_id: str = Field(
        description="Inbox item ID.", json_schema_extra={"format": "uuid"}
    )
    answer: str = Field(description="The answer text.")


class SkipInboxItemParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    item_id: str = Field(
        description="Inbox item ID.", json_schema_extra={"format": "uuid"}
    )


class RejectInboxItemParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    item_id: str = Field(
        description="Inbox item ID.", json_schema_extra={"format": "uuid"}
    )
    reason: str = Field(description="Reason for rejection.")


# -- Trace params --


class ListTasksParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


class GetTaskAttemptsParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task_id: str = Field(
        description="Task ID.", json_schema_extra={"format": "uuid"}
    )


class GetTranscriptParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    session_id: str = Field(
        description="Session ID.", json_schema_extra={"format": "uuid"}
    )


class SearchTranscriptParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    session_id: str = Field(
        description="Session ID.", json_schema_extra={"format": "uuid"}
    )
    query: str = Field(description="Search query.")


class QueryEventsParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})
    event_type: str | None = Field(default=None, description="Filter by event type.")
    since: str | None = Field(
        default=None,
        description="Only events after this timestamp.",
        json_schema_extra={"format": "date-time"},
    )
    limit: int = Field(default=100, description="Maximum events to return.")


class GetNotepadParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


# -- Event params --


class FireEventParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})
    event_name: str = Field(description="Event name.")
    payload: dict[str, Any] | None = Field(
        default=None, description="Event payload."
    )


# -- Config params --


class ShowConfigParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str = Field(description="Run ID.", json_schema_extra={"format": "uuid"})


class ShowPricingParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


# -- Validate params --


class ValidateAgentParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="Path to agent TOML file.")


class ValidateWorkflowParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="Path to workflow TOML file.")


class ValidateCategoriesParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(description="Path to categories TOML file.")


# -- Dispatch params --


class SubscribeParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    filter: dict[str, Any] = Field(description="Filter predicate for matching events.")
    actions: list[dict[str, Any]] = Field(
        description="List of action configurations."
    )
    storage: str = Field(
        default="persistent", description="Storage type for the subscription."
    )
    owner_run_id: str | None = Field(
        default=None,
        description="Run ID that owns this subscription.",
        json_schema_extra={"format": "uuid"},
    )


class UnsubscribeParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    subscription_id: str = Field(
        description="Subscription ID.",
        json_schema_extra={"format": "uuid"},
    )


class ListSubscriptionsParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    enabled_only: bool = Field(
        default=True, description="Only show enabled subscriptions."
    )


class CreateSourceParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    slug: str = Field(description="URL-friendly source identifier.")
    name: str = Field(description="Human-readable source name.")
    credential_id: str | None = Field(
        default=None,
        description="Credential ID for source authentication.",
        json_schema_extra={"format": "uuid"},
    )
    config: dict[str, Any] | None = Field(
        default=None,
        description="Per-source mapping config (event_type extraction, etc.).",
    )


class GetSourceParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source_id: str = Field(
        description="Source ID.", json_schema_extra={"format": "uuid"}
    )


class GetSourceBySlugParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    slug: str = Field(description="URL-friendly source identifier.")


class ListSourcesParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class DeleteSourceParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source_id: str = Field(
        description="Source ID.", json_schema_extra={"format": "uuid"}
    )


# -- Principal params --


class CreatePrincipalParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    kind: str = Field(description="Principal kind (validated against the registry).")
    external_ref: str = Field(
        description="External reference the principal points at.",
        json_schema_extra={"format": "uuid"},
    )
    display_name: str | None = Field(
        default=None, description="Optional human-readable display name."
    )


class GetPrincipalParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    principal_id: str = Field(
        description="Principal ID.", json_schema_extra={"format": "uuid"}
    )


class ListPrincipalsParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    kind: str | None = Field(default=None, description="Filter by principal kind.")


class DeletePrincipalParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    principal_id: str = Field(
        description="Principal ID.", json_schema_extra={"format": "uuid"}
    )
