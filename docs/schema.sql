-- Logintel API — Supabase schema
-- Apply via SQL Editor in Supabase dashboard.

-- Organizations
CREATE TABLE IF NOT EXISTS organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'free'
                CHECK (tier IN ('free', 'starter', 'professional', 'enterprise')),
    rate_limit_hour       INT NOT NULL DEFAULT 100,
    predictions_limit_month INT NOT NULL DEFAULT 1000,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- API keys (hashed)
CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key_hash        TEXT NOT NULL UNIQUE,
    prefix          TEXT NOT NULL,          -- first 8 chars for identification
    name            TEXT NOT NULL DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- Predictions (JSONB storage)
CREATE TABLE IF NOT EXISTS predictions (
    id                  UUID PRIMARY KEY,
    organization_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    data                JSONB NOT NULL,
    total_delay_minutes DOUBLE PRECISION NOT NULL,
    departure_time      TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_predictions_org_created
    ON predictions(organization_id, created_at DESC);

-- Feedback
CREATE TABLE IF NOT EXISTS feedback (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_id           UUID NOT NULL UNIQUE REFERENCES predictions(id) ON DELETE CASCADE,
    organization_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actual_delay_minutes    INT NOT NULL,
    predicted_delay_minutes DOUBLE PRECISION NOT NULL,
    deviation_minutes       DOUBLE PRECISION NOT NULL,
    received_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_prediction ON feedback(prediction_id);

-- Calibration versions
CREATE TABLE IF NOT EXISTS calibration_versions (
    version         SERIAL PRIMARY KEY,
    coefficients    JSONB NOT NULL,          -- string keys like "rain:moderate"
    feedback_count  INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
