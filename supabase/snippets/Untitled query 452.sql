-- =============================================================================
-- Migration: Add Stripe payment + credit fields to users table
--            Create payments history table
-- =============================================================================

-- 1. Add payment + credit columns to existing users table
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS credits           INTEGER      DEFAULT 0,
  ADD COLUMN IF NOT EXISTS plan              TEXT         DEFAULT 'free',   -- 'free' | 'pro' | 'elite'
  ADD COLUMN IF NOT EXISTS stripe_customer_id         TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS stripe_payment_method_id   TEXT,
  ADD COLUMN IF NOT EXISTS payment_type               TEXT,        -- 'card', etc.
  ADD COLUMN IF NOT EXISTS card_brand                 TEXT,
  ADD COLUMN IF NOT EXISTS card_last4                 TEXT,
  ADD COLUMN IF NOT EXISTS card_exp_month             INTEGER,
  ADD COLUMN IF NOT EXISTS card_exp_year              INTEGER,
  ADD COLUMN IF NOT EXISTS billing_email              TEXT,
  ADD COLUMN IF NOT EXISTS billing_name               TEXT,
  ADD COLUMN IF NOT EXISTS billing_country            TEXT;

-- 2. Payments history table (one row per successful charge)
CREATE TABLE IF NOT EXISTS payments (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id                    TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    stripe_payment_intent_id    TEXT NOT NULL UNIQUE,
    stripe_customer_id          TEXT NOT NULL,
    stripe_payment_method_id    TEXT NOT NULL,
    amount                      INTEGER NOT NULL,       -- USD
    currency                    TEXT DEFAULT 'usd',
    status                      TEXT NOT NULL,          -- 'succeeded', etc.
    credits_added               INTEGER NOT NULL,
    card_brand                  TEXT,
    card_last4                  TEXT,
    created_at                  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_clerk_id   ON payments(clerk_id);
CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at DESC);

-- 3. Row-level security on payments (users see only their own)
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own payments"
  ON payments FOR SELECT
  USING (clerk_id = auth.jwt() ->> 'sub');

-- =============================================================================
-- Plan limits reference (enforced in route logic, NOT in DB constraints)
-- =============================================================================
--
-- FREE plan ($0)
--   credits: 0 (no credit purchases needed)
--   max_projects:  3
--   max_docs_per_project: 5  (including past-year papers)
--   max_pages_per_doc: 20
--   max_chats_per_project: 2
--   max_chat_messages_per_chat: 10
--   feature_generations: 1 per doc (no expand)
--   expand_allowed: false
--
-- PRO plan ($5 → 50 credits)
--   max_projects: 15
--   max_docs_per_project: 20
--   max_pages_per_doc: 100
--   max_chats_per_project: 10
--   max_chat_messages_per_chat: unlimited
--   feature_generations: 1 per doc + 1 expand
--   expand_allowed: true (1x per source)
--
-- ELITE plan ($20 → 250 credits, 200 base + 50 bonus)
--   max_projects: 100
--   max_docs_per_project: 50
--   max_pages_per_doc: 300+
--   max_chats_per_project: unlimited
--   max_chat_messages_per_chat: unlimited
--   feature_generations: unlimited
--   expand_allowed: true (unlimited)
-- =============================================================================