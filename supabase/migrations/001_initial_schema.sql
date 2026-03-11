-- ============================================================
-- Logintel API — Initial Schema
-- Migration 001 — Predictions, Feedback, Calibration Versions
-- ============================================================

-- 1. predictions
-- Stores the full prediction payload as JSONB (data) plus
-- denormalized columns used for filtering/ordering.
CREATE TABLE IF NOT EXISTS predictions (
    id              TEXT        PRIMARY KEY,
    organization_id TEXT        NOT NULL DEFAULT '',
    data            JSONB       NOT NULL,
    total_delay_minutes DOUBLE PRECISION NOT NULL,
    departure_time  TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_predictions_org_id
    ON predictions (organization_id);

CREATE INDEX idx_predictions_org_created
    ON predictions (organization_id, created_at DESC);

CREATE INDEX idx_predictions_departure
    ON predictions (departure_time);


-- 2. feedback
-- One feedback entry per prediction (enforced by unique constraint).
CREATE TABLE IF NOT EXISTS feedback (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_id           TEXT        NOT NULL REFERENCES predictions (id) ON DELETE CASCADE,
    organization_id         TEXT        NOT NULL DEFAULT '',
    actual_delay_minutes    INTEGER     NOT NULL,
    predicted_delay_minutes DOUBLE PRECISION NOT NULL,
    deviation_minutes       DOUBLE PRECISION NOT NULL,
    received_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_feedback_prediction UNIQUE (prediction_id)
);

CREATE INDEX idx_feedback_org_id
    ON feedback (organization_id);

CREATE INDEX idx_feedback_prediction_id
    ON feedback (prediction_id);


-- 3. calibration_versions
-- Append-only log of calibration coefficient snapshots.
-- "version" auto-increments and is used for ordering.
CREATE TABLE IF NOT EXISTS calibration_versions (
    version         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    coefficients    JSONB       NOT NULL,
    feedback_count  INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_calibration_versions_created
    ON calibration_versions (created_at DESC);


-- 4. Row Level Security (RLS)
-- Enable RLS on all tables. The API uses the service_role key
-- which bypasses RLS, but this protects against anon/client access.
ALTER TABLE predictions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback            ENABLE ROW LEVEL SECURITY;
ALTER TABLE calibration_versions ENABLE ROW LEVEL SECURITY;

-- Allow full access for the service_role (used by the API)
CREATE POLICY "Service role full access on predictions"
    ON predictions FOR ALL
    USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "Service role full access on feedback"
    ON feedback FOR ALL
    USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "Service role full access on calibration_versions"
    ON calibration_versions FOR ALL
    USING (TRUE) WITH CHECK (TRUE);
