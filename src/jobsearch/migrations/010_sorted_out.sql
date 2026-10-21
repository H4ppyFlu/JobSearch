-- A ★ Job the AI marked as clearly outside the candidate's field. Hidden by default, never deleted.
ALTER TABLE fit_scores ADD COLUMN sorted_out INTEGER NOT NULL DEFAULT 0;
