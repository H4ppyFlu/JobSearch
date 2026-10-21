-- Skill groups for the Market report: each skill text from a Fit score (whitespace collapsed) is
-- grouped once by the AI and keeps its group, so differently worded skills are counted together.
CREATE TABLE skill_groups (
    text       TEXT PRIMARY KEY,
    group_name TEXT NOT NULL,
    grouped_at TEXT,
    model      TEXT
);
