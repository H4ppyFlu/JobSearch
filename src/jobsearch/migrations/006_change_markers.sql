-- What was in effect, so the eval can compare results before and after a change.
ALTER TABLE fit_scores ADD COLUMN prompt_hash TEXT;
ALTER TABLE runs ADD COLUMN settings_json TEXT;
