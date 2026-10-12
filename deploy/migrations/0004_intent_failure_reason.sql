ALTER TABLE intent_decisions
    ADD COLUMN failure_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE intent_decisions
    ADD CONSTRAINT intent_decisions_failure_reason_check
    CHECK (failure_reason IN ('', 'parse_error', 'schema_error', 'business_confidence'));
