from __future__ import annotations

# Schema authority: pgdesign TOML (schema/dispatch.toml) is the source of truth.
# This file is a hand-maintained Python DDL that will be replaced by pgdesign
# codegen once that capability exists. Known divergences from pgdesign output:
#
# - UUID generation: pgdesign emits gen_random_uuid(); this file uses
#   uuid_generate_v7() (pg-uuidv7 extension) for time-ordered UUIDs.
# - Enum columns: pgdesign emits proper CREATE TYPE ... AS ENUM; this file
#   uses TEXT for all enum-like columns.
# - FK definitions: pgdesign emits ALTER TABLE ... ADD CONSTRAINT; this
#   file uses inline REFERENCES in CREATE TABLE.
# - Cross-file FKs: subscriptions.owner_run_id -> runs (trace-owned),
#   accumulator_buffer.event_id -> events (trace-owned). These tables
#   must exist before dispatch tables are created.

# Table name constants: logical name -> SQL table name.
TABLE_NAMES: dict[str, str] = {
    "sources": "sources",
    "subscriptions": "subscriptions",
    "subscription_actions": "subscription_actions",
    "accumulator_buffer": "accumulator_buffer",
}

# ---------------------------------------------------------------------------
# CREATE TABLE statements (in FK dependency order)
# ---------------------------------------------------------------------------

CREATE_SOURCES = """\
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    auth_method TEXT,
    auth_config JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_SUBSCRIPTIONS = """\
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    source_id UUID REFERENCES sources(id) ON DELETE SET NULL,
    filter_expr JSONB NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT true,
    storage TEXT NOT NULL DEFAULT 'persistent',
    owner_run_id UUID REFERENCES runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_SUBSCRIPTION_ACTIONS = """\
CREATE TABLE subscription_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    subscription_id UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    position INT NOT NULL,
    action_type TEXT NOT NULL,
    action_config JSONB NOT NULL DEFAULT '{}',
    accumulator_config JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subscription_id, position)
);
"""

CREATE_ACCUMULATOR_BUFFER = """\
CREATE TABLE accumulator_buffer (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    subscription_action_id UUID NOT NULL REFERENCES subscription_actions(id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

CREATE_INDEXES = """\
CREATE INDEX idx_subscriptions_source ON subscriptions (source_id);
CREATE INDEX idx_subscriptions_enabled ON subscriptions (enabled);
CREATE INDEX idx_subscription_actions_sub ON subscription_actions (subscription_id);
CREATE INDEX idx_accumulator_buffer_action ON accumulator_buffer (subscription_action_id);
CREATE INDEX idx_accumulator_buffer_event ON accumulator_buffer (event_id);
"""

# ---------------------------------------------------------------------------
# All statements in dependency order
# ---------------------------------------------------------------------------

ALL_CREATE_STATEMENTS: list[str] = [
    CREATE_SOURCES,
    CREATE_SUBSCRIPTIONS,
    CREATE_SUBSCRIPTION_ACTIONS,
    CREATE_ACCUMULATOR_BUFFER,
    CREATE_INDEXES,
]
