-- Layer 7: facts that outlive one run. The run stack stays in checkpoints.

CREATE TABLE memory_records (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('session', 'working', 'long_term')),
    memory_key TEXT NOT NULL,
    content JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, scope, memory_key)
);

CREATE INDEX memory_records_owner_idx ON memory_records (tenant_id, user_id, scope);
