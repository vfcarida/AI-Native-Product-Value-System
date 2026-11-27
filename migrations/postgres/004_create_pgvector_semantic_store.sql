-- Migration 004: pgvector HNSW Temporal Semantic Cache & Layered Memory Store (EVO-P3-10 / EVO-P3-12)
-- Requires PostgreSQL 15+ with pgvector extension enabled.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS semantic_cache_entries (
    cache_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    response_json JSONB NOT NULL,
    temperature DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    cached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    embedding vector(1536)
);

CREATE INDEX IF NOT EXISTS idx_sem_cache_tenant_model_ver
    ON semantic_cache_entries (tenant_id, model, prompt_version, valid_until);

CREATE INDEX IF NOT EXISTS idx_sem_cache_embedding_hnsw
    ON semantic_cache_entries
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS semantic_memory_chunks (
    chunk_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    layer TEXT NOT NULL CHECK (layer IN ('episodic', 'semantic', 'artifact')),
    source_id TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    checksum_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    embedding vector(1536)
);

CREATE INDEX IF NOT EXISTS idx_sem_memory_tenant_layer
    ON semantic_memory_chunks (tenant_id, layer, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sem_memory_embedding_hnsw
    ON semantic_memory_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Multi-tenant Row Level Security (RLS)
ALTER TABLE semantic_cache_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE semantic_memory_chunks ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'semantic_cache_entries' AND policyname = 'tenant_isolation_sem_cache'
    ) THEN
        CREATE POLICY tenant_isolation_sem_cache ON semantic_cache_entries
            USING (
                current_setting('app.current_tenant', true) IS NULL
                OR current_setting('app.current_tenant', true) = ''
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'semantic_memory_chunks' AND policyname = 'tenant_isolation_sem_memory'
    ) THEN
        CREATE POLICY tenant_isolation_sem_memory ON semantic_memory_chunks
            USING (
                current_setting('app.current_tenant', true) IS NULL
                OR current_setting('app.current_tenant', true) = ''
                OR tenant_id = current_setting('app.current_tenant', true)
            );
    END IF;
END
$$;

INSERT INTO schema_migrations (version) VALUES (4) ON CONFLICT (version) DO NOTHING;
