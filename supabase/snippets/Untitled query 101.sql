-- 002_add_features.sql
-- Add per-document generated feature columns to project_documents
ALTER TABLE project_documents
  ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS faq TEXT,
  ADD COLUMN IF NOT EXISTS study_guide TEXT,
  ADD COLUMN IF NOT EXISTS briefing_doc TEXT,
  ADD COLUMN IF NOT EXISTS mind_map TEXT,
  ADD COLUMN IF NOT EXISTS features_status TEXT DEFAULT 'pending';

-- Generated sources table (merged outputs from multiple documents)
CREATE TABLE IF NOT EXISTS generated_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clerk_id TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,  -- 'summary', 'faq', 'study_guide', 'briefing_doc', 'mind_map'
    content TEXT NOT NULL,
    document_ids UUID[] NOT NULL,  -- array of project_document IDs used to generate this
    total_sources INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_generated_sources_project 
  ON generated_sources(project_id, source_type);