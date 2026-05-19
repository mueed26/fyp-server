-- Add notes table (integrates with existing schema)
CREATE TABLE notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clerk_id TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Create indexes for performance
CREATE INDEX idx_notes_project_id ON notes(project_id);
CREATE INDEX idx_notes_clerk_id ON notes(clerk_id);
CREATE INDEX idx_notes_created_at ON notes(created_at DESC);

-- Enable RLS
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;

-- RLS policies
CREATE POLICY "Users can view their own notes"
  ON notes FOR SELECT
  USING (clerk_id = auth.jwt() ->> 'sub');

CREATE POLICY "Users can insert their own notes"
  ON notes FOR INSERT
  WITH CHECK (clerk_id = auth.jwt() ->> 'sub');

CREATE POLICY "Users can update their own notes"
  ON notes FOR UPDATE
  USING (clerk_id = auth.jwt() ->> 'sub')
  WITH CHECK (clerk_id = auth.jwt() ->> 'sub');

CREATE POLICY "Users can delete their own notes"
  ON notes FOR DELETE
  USING (clerk_id = auth.jwt() ->> 'sub');