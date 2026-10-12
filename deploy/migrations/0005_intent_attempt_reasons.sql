ALTER TABLE intent_decisions
    DROP CONSTRAINT IF EXISTS intent_decisions_failure_reason_check;

ALTER TABLE intent_decisions
    ADD CONSTRAINT intent_decisions_failure_reason_check
    CHECK (
        failure_reason IN (
            '',
            'parse_error',
            'schema_error',
            'business_confidence',
            'model_error'
        )
    );

ALTER TABLE intent_decisions
    ADD COLUMN IF NOT EXISTS attempt_reasons JSONB NOT NULL DEFAULT '[]'::jsonb;
