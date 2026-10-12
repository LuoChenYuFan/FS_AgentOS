-- Layer 5: document metadata. The file body lives in object storage.
-- Chunk text is kept so a citation can point back at the passage.

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    title TEXT NOT NULL,
    oss_key TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    media_type TEXT NOT NULL,
    index_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX documents_tenant_idx ON documents (tenant_id, index_version);

CREATE TABLE document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents (id),
    tenant_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    body TEXT NOT NULL,
    token_size INTEGER NOT NULL DEFAULT 0,
    entity_id TEXT,
    source_uri TEXT NOT NULL,
    UNIQUE (document_id, ordinal)
);

CREATE INDEX document_chunks_tenant_idx ON document_chunks (tenant_id);
