-- Career scan health per page, to report pages that fail or list nothing twice in a row.
ALTER TABLE career_pages ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE career_pages ADD COLUMN empty_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE career_pages ADD COLUMN last_error TEXT;
