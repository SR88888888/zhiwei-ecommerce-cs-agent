CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (user_id, session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, session_id)
);

CREATE TABLE IF NOT EXISTS memory_consents (
    user_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_events (
    event_id UUID PRIMARY KEY,
    memory_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('add', 'retract', 'expire')),
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_items (
    memory_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('semantic', 'episodic')),
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    importance REAL NOT NULL,
    confidence REAL NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    status TEXT NOT NULL CHECK (status IN ('active', 'retracted', 'expired')),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_items (user_id, shop_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_memory_search ON memory_items USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS tool_executions (
    execution_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('read', 'write')),
    status TEXT NOT NULL CHECK (status IN ('success', 'business_error', 'retryable_error', 'system_error')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    params_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tool_executions_run ON tool_executions (run_id, created_at);

CREATE TABLE IF NOT EXISTS after_sales_previews (
    preview_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    idempotency_key_hash TEXT,
    after_sales_id TEXT,
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_after_sales_previews_user ON after_sales_previews (user_id, status, expires_at);
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    shop_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'ready',
    content_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'ready',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_status ON knowledge_documents (shop_id, status);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES knowledge_documents(document_id),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(512),
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_fts ON knowledge_chunks USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_vector ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS attachments (
    attachment_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ocr_text TEXT NOT NULL DEFAULT '',
    entities JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_attachments_expire ON attachments (expires_at);