-- ============================================================
-- Logintel API — Feedback notes
-- Migration 003 — Persist the optional free-text notes sent with feedback
-- ============================================================

ALTER TABLE feedback
    ADD COLUMN IF NOT EXISTS notes TEXT
        CHECK (notes IS NULL OR char_length(notes) <= 500);
