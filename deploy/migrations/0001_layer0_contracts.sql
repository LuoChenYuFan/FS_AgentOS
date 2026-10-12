-- Layer 0: empty contract tables.
-- This script only defines columns. It does not connect the app, and nothing writes these rows yet.
-- Resume-from-checkpoint and idempotent side effects are later layers.
-- Idempotency is unique per tenant, so two tenants can reuse the same client key.

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX sessions_tenant_user_idx ON sessions (tenant_id, user_id);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions (id),
    tenant_id TEXT NOT NULL,
    user_input TEXT NOT NULL,
    plan_json JSONB,
    state_json JSONB,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'suspended', 'awaiting_approval', 'done', 'failed')
    ),
    current_node TEXT,
    replan_count INTEGER NOT NULL DEFAULT 0,
    budget_token_used INTEGER NOT NULL DEFAULT 0,
    budget_cost_used NUMERIC NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resumed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX runs_session_idx ON runs (session_id);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (id),
    task_index INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    args_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    deps INTEGER[] NOT NULL DEFAULT '{}',
    output_key TEXT,
    condition_json JSONB,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'done', 'skipped', 'failed')
    ),
    condition_result BOOLEAN,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    UNIQUE (run_id, task_index)
);

CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (id),
    node_name TEXT NOT NULL,
    step_seq INTEGER NOT NULL CHECK (step_seq >= 0),
    state_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_seq)
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (id),
    task_id TEXT NOT NULL REFERENCES tasks (id),
    output_key TEXT NOT NULL,
    payload_json JSONB,
    payload_ref TEXT,
    token_size INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (payload_json IS NOT NULL OR payload_ref IS NOT NULL)
);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (id),
    task_id TEXT NOT NULL REFERENCES tasks (id),
    action_type TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved', 'rejected', 'expired')
    ),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    expires_at TIMESTAMPTZ,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE idempotency_keys (
    key TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs (id),
    action_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'unknown')),
    result_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (id),
    task_id TEXT REFERENCES tasks (id),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intent_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs (id),
    tenant_id TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    intent TEXT NOT NULL,
    route TEXT NOT NULL CHECK (route IN ('knowledge', 'orchestrate', 'unknown')),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    layer_hit TEXT NOT NULL CHECK (
        layer_hit IN ('regex', 'rules', 'vector', 'small_llm', 'large_llm', 'none')
    ),
    passed BOOLEAN NOT NULL,
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    false_positive BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (route = 'unknown' AND intent = 'unknown' AND passed = FALSE)
        OR (route <> 'unknown')
    )
);

CREATE INDEX intent_decisions_tenant_layer_idx ON intent_decisions (tenant_id, layer_hit);
