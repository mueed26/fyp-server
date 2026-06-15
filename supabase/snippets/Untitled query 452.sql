-- 005_add_payments.sql
-- Adds Stripe + plan + credits to the users table, plus a payments history table.

-- ──────────────────────────────────────────────────────────────────────
-- 1. Extend users with plan & Stripe identifiers
-- ──────────────────────────────────────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS plan_purchased_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS email TEXT;

-- Enforce valid plan values
ALTER TABLE users
  DROP CONSTRAINT IF EXISTS users_plan_check;
ALTER TABLE users
  ADD CONSTRAINT users_plan_check CHECK (plan IN ('free', 'pro', 'elite'));

CREATE INDEX IF NOT EXISTS idx_users_stripe_customer_id ON users(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_users_plan ON users(plan);

-- ──────────────────────────────────────────────────────────────────────
-- 2. Payments history table (one row per successful checkout)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    credits_granted INTEGER NOT NULL DEFAULT 0,
    stripe_checkout_session_id TEXT UNIQUE,
    stripe_payment_intent_id TEXT,
    stripe_customer_id TEXT,
    status TEXT NOT NULL DEFAULT 'succeeded',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_clerk_id ON payments(clerk_id);
CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at DESC);