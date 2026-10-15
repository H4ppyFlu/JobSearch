-- A new Job that may be the same opening as an earlier one. The scoring call decides:
-- same → the new Job is merged into the candidate, different → the link is cleared.
ALTER TABLE jobs ADD COLUMN match_candidate INTEGER REFERENCES jobs(id);
