-- ═══════════════════════════════════════════════════════════════════
-- Curabook PHI — Complete Database Schema
-- Run this ONCE in Supabase SQL Editor
-- Safe to re-run (all statements use IF NOT EXISTS / IF NOT EXISTS)
-- ═══════════════════════════════════════════════════════════════════

-- ── Core tables ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    first_name       TEXT,
    last_name        TEXT,
    age              INTEGER,
    date_of_birth    DATE,
    gender           TEXT,
    timezone         TEXT DEFAULT 'UTC',
    role             TEXT NOT NULL DEFAULT 'patient' CHECK (role IN ('patient','doctor','admin')),
    plan             TEXT DEFAULT 'free',
    reports_remaining INTEGER DEFAULT 1,
    stripe_customer_id TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title      TEXT DEFAULT 'New Chat',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chats (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content          TEXT NOT NULL,
    is_phi           BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health_markers (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    marker_name      TEXT NOT NULL,
    value            NUMERIC,
    unit             TEXT DEFAULT '',
    reference_range  TEXT DEFAULT '',
    status           TEXT DEFAULT 'UNKNOWN',
    date             DATE,
    source_document  TEXT DEFAULT '',
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health_insights (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    insights_json   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS user_consents (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    consent_type     TEXT NOT NULL,
    consent_version  TEXT DEFAULT 'v2.0',
    ip_address       TEXT,
    user_agent       TEXT,
    is_active        BOOLEAN DEFAULT TRUE,
    granted_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, consent_type)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT DEFAULT '',
    category   TEXT DEFAULT 'GENERAL',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Medical documents table (required for document_id secure flow) ─

CREATE TABLE IF NOT EXISTS medical_documents (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    filename   TEXT,
    content    TEXT,
    doc_type   TEXT DEFAULT 'lab_report',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Safe column additions (run even if tables exist) ──────────────

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'patient';
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'free';
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS reports_remaining INTEGER DEFAULT 1;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;

-- ── Indexes for performance ────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_chats_conv       ON chats(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chats_user       ON chats(user_id);
CREATE INDEX IF NOT EXISTS idx_convs_user       ON conversations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_markers_user     ON health_markers(user_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_markers_name     ON health_markers(user_id, marker_name);
CREATE INDEX IF NOT EXISTS idx_docs_user        ON medical_documents(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user       ON audit_logs(user_id, created_at DESC);

-- Deduplicate marker upserts
CREATE UNIQUE INDEX IF NOT EXISTS idx_markers_unique
    ON health_markers(user_id, marker_name, date);

-- ── Row Level Security ─────────────────────────────────────────────

ALTER TABLE user_profiles    ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE chats             ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_markers    ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_insights   ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_consents     ENABLE ROW LEVEL SECURITY;
ALTER TABLE medical_documents ENABLE ROW LEVEL SECURITY;

-- Users can only see their own data
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='user_profiles' AND policyname='own_profile') THEN
        CREATE POLICY own_profile    ON user_profiles    FOR ALL USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='conversations' AND policyname='own_convs') THEN
        CREATE POLICY own_convs      ON conversations     FOR ALL USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='chats' AND policyname='own_chats') THEN
        CREATE POLICY own_chats      ON chats             FOR ALL USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='health_markers' AND policyname='own_markers') THEN
        CREATE POLICY own_markers    ON health_markers    FOR ALL USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='health_insights' AND policyname='own_insights') THEN
        CREATE POLICY own_insights   ON health_insights   FOR ALL USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='user_consents' AND policyname='own_consents') THEN
        CREATE POLICY own_consents   ON user_consents     FOR ALL USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='medical_documents' AND policyname='own_docs') THEN
        CREATE POLICY own_docs       ON medical_documents FOR ALL USING (auth.uid() = user_id);
    END IF;
END $$;

-- ── Done ──────────────────────────────────────────────────────────
-- Tables: user_profiles, conversations, chats, health_markers,
--         health_insights, user_consents, audit_logs, medical_documents