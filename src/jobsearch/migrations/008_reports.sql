-- Weekly reports: the AI's reading of the pipeline's own metrics, and its Recommendations.
CREATE TABLE reports (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    period_start TEXT NOT NULL,  -- first day of the week the report looks at
    period_end   TEXT NOT NULL,  -- its last day; the next report shows the settings changes since then
    model        TEXT,
    prompt_hash  TEXT,
    input_json   TEXT,
    answer_json  TEXT,           -- after the checks
    counts_json  TEXT,
    error        TEXT            -- set when the AI call failed; such a report does not count as done
);

CREATE TABLE recommendations (
    id            INTEGER PRIMARY KEY,
    report_id     INTEGER NOT NULL REFERENCES reports(id),
    position      INTEGER NOT NULL,
    kind          TEXT NOT NULL,  -- change | new_lever
    lever         TEXT,           -- for a new_lever: its proposed name
    target        TEXT,
    action        TEXT NOT NULL,  -- for a new_lever: what it would change
    why           TEXT,           -- for a new_lever: why the metrics point to it
    evidence_json TEXT NOT NULL,
    confidence    TEXT,
    decision      TEXT NOT NULL DEFAULT 'open',  -- open | accepted | rejected
    decided_at    TEXT
);
CREATE INDEX recommendations_report ON recommendations(report_id);
