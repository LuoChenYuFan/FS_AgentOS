-- Same title in one tenant is the same document. The hash says whether the text changed.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS documents_tenant_title_idx
    ON documents (tenant_id, title);
