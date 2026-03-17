-- ============================================================
-- Logintel API — Organizations & API Keys
-- Migration 002 — Auth tables for X-API-Key authentication
-- ============================================================

-- 1. organizations
-- Stores tenant information, tier, and rate limits.
CREATE TABLE IF NOT EXISTS organizations (
    id                      TEXT        PRIMARY KEY,
    name                    TEXT        NOT NULL,
    tier                    TEXT        NOT NULL DEFAULT 'free',
    rate_limit_hour         INTEGER     NOT NULL DEFAULT 100,
    predictions_limit_month INTEGER     NOT NULL DEFAULT 1000,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- 2. api_keys
-- Stores hashed API keys linked to organizations.
-- The raw key is never stored — only its SHA-256 hex digest.
CREATE TABLE IF NOT EXISTS api_keys (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_hash            TEXT        NOT NULL UNIQUE,
    organization_id     TEXT        NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_api_keys_key_hash
    ON api_keys (key_hash);

CREATE INDEX idx_api_keys_org_id
    ON api_keys (organization_id);


-- 3. Add FK from existing tables to organizations (optional, not enforced
--    retroactively to avoid breaking existing rows with empty org ids).


-- 4. Row Level Security
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys      ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on organizations"
    ON organizations FOR ALL
    USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "Service role full access on api_keys"
    ON api_keys FOR ALL
    USING (TRUE) WITH CHECK (TRUE);
