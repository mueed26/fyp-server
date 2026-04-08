ALTER TABLE project_documents
  ADD COLUMN IF NOT EXISTS source_tag TEXT DEFAULT 'lecture_notes',
  ADD COLUMN IF NOT EXISTS flashcards TEXT,
  ADD COLUMN IF NOT EXISTS practice_questions TEXT;