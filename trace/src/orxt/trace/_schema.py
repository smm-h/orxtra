from __future__ import annotations

# Table name constants: logical name -> SQL table name.
TABLE_NAMES: dict[str, str] = {
    "runs": "runs",
    "tasks": "tasks",
    "task_attempts": "task_attempts",
    "task_iterations": "task_iterations",
    "events": "events",
    "transcripts": "transcripts",
    "notepad_entries": "notepad_entries",
    "inbox_items": "inbox_items",
    "context_diffs": "context_diffs",
    "decisions": "decisions",
    "constraints": "constraints",
    "assumptions": "assumptions",
    "lessons": "lessons",
    "overseer_workflow_status": "overseer_workflow_status",
}

# ---------------------------------------------------------------------------
# CREATE TABLE statements
# ---------------------------------------------------------------------------

CREATE_RUNS = """\
CREATE TABLE runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    intent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    autonomy_level TEXT NOT NULL,
    config_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    total_input_tokens BIGINT NOT NULL DEFAULT 0,
    total_output_tokens BIGINT NOT NULL DEFAULT 0,
    total_reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    total_cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    total_cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    total_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    coherence_summary TEXT
);
"""

CREATE_TASKS = """\
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    parent_task_id UUID REFERENCES tasks(id),
    name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    config JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_TASK_ATTEMPTS = """\
CREATE TABLE task_attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    task_id UUID NOT NULL REFERENCES tasks(id),
    attempt INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    agent_output TEXT,
    structured_output JSONB,
    check_result JSONB,
    check_verdict TEXT,
    session_id UUID,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    duration_seconds DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, attempt)
);
"""

CREATE_TASK_ITERATIONS = """\
CREATE TABLE task_iterations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    task_id UUID NOT NULL REFERENCES tasks(id),
    iteration_index INT NOT NULL,
    item_value JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    output TEXT,
    structured_output JSONB,
    check_results JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (task_id, iteration_index)
);
"""

CREATE_EVENTS = """\
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    task_id UUID REFERENCES tasks(id),
    event_type TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_TRANSCRIPTS = """\
CREATE TABLE transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    session_id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(id),
    turn INT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    tokens JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_NOTEPAD_ENTRIES = """\
CREATE TABLE notepad_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    task_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_INBOX_ITEMS = """\
CREATE TABLE inbox_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    status TEXT NOT NULL DEFAULT 'pending',
    decision_type TEXT NOT NULL,
    question TEXT NOT NULL,
    options JSONB NOT NULL DEFAULT '[]',
    assumed_option TEXT,
    work_proceeding TEXT,
    contradiction_impact TEXT,
    tags JSONB NOT NULL DEFAULT '[]',
    deadline TIMESTAMPTZ,
    answer TEXT,
    answer_event TEXT,
    rejection_reason TEXT,
    answered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_CONTEXT_DIFFS = """\
CREATE TABLE context_diffs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    attempt_id UUID NOT NULL REFERENCES task_attempts(id),
    pre_refinement TEXT NOT NULL,
    refinement_diff TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_DECISIONS = """\
CREATE TABLE decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    decision_type TEXT NOT NULL,
    choice TEXT NOT NULL,
    rationale TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_CONSTRAINTS = """\
CREATE TABLE constraints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    text TEXT NOT NULL,
    tier TEXT NOT NULL,
    kind TEXT NOT NULL,
    args JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_ASSUMPTIONS = """\
CREATE TABLE assumptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    text TEXT NOT NULL,
    scope TEXT NOT NULL,
    inbox_item_id UUID REFERENCES inbox_items(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_LESSONS = """\
CREATE TABLE lessons (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    run_id UUID NOT NULL REFERENCES runs(id),
    text TEXT NOT NULL,
    relevance_tags JSONB NOT NULL DEFAULT '[]',
    permanent BOOLEAN NOT NULL DEFAULT false,
    source_files JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_OVERSEER_WORKFLOW_STATUS = """\
CREATE TABLE overseer_workflow_status (
    workflow_id UUID PRIMARY KEY,
    current_step TEXT NOT NULL,
    health TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_RUN_HEARTBEATS = """\
CREATE TABLE run_heartbeats (
    run_id UUID PRIMARY KEY REFERENCES runs(id),
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

CREATE_EVENTS_INDEXES = """\
CREATE INDEX idx_events_run_created ON events (run_id, created_at DESC);
CREATE INDEX idx_events_type_created ON events (event_type, created_at DESC);
"""

# ---------------------------------------------------------------------------
# Immutability: revoke mutations on append-only tables
# ---------------------------------------------------------------------------

REVOKE_EVENTS = """\
REVOKE UPDATE, DELETE ON events FROM PUBLIC;
"""

REVOKE_TRANSCRIPTS = """\
REVOKE UPDATE, DELETE ON transcripts FROM PUBLIC;
"""

REVOKE_NOTEPAD_ENTRIES = """\
REVOKE UPDATE, DELETE ON notepad_entries FROM PUBLIC;
"""

# ---------------------------------------------------------------------------
# LISTEN/NOTIFY trigger for real-time event streaming
# ---------------------------------------------------------------------------

CREATE_NOTIFY_FUNCTION = """\
CREATE OR REPLACE FUNCTION notify_orxt_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('orxt_events', json_build_object(
        'event_id', NEW.id,
        'run_id', NEW.run_id,
        'event_type', NEW.event_type
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CREATE_NOTIFY_TRIGGER = """\
CREATE TRIGGER trg_notify_event
    AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION notify_orxt_event();
"""

# ---------------------------------------------------------------------------
# All statements in dependency order (FK targets before FK sources)
# ---------------------------------------------------------------------------

ALL_CREATE_STATEMENTS: list[str] = [
    CREATE_RUNS,
    CREATE_TASKS,
    CREATE_TASK_ATTEMPTS,
    CREATE_TASK_ITERATIONS,
    CREATE_EVENTS,
    CREATE_TRANSCRIPTS,
    CREATE_NOTEPAD_ENTRIES,
    CREATE_INBOX_ITEMS,
    CREATE_CONTEXT_DIFFS,
    CREATE_DECISIONS,
    CREATE_CONSTRAINTS,
    CREATE_ASSUMPTIONS,
    CREATE_LESSONS,
    CREATE_OVERSEER_WORKFLOW_STATUS,
    CREATE_RUN_HEARTBEATS,
    CREATE_EVENTS_INDEXES,
    REVOKE_EVENTS,
    REVOKE_TRANSCRIPTS,
    REVOKE_NOTEPAD_ENTRIES,
    CREATE_NOTIFY_FUNCTION,
    CREATE_NOTIFY_TRIGGER,
]
