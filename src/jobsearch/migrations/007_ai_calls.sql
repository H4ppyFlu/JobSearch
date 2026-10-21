-- AI usage: one row per claude -p process, retries included. Token and cost columns are NULL
-- when no answer envelope arrived (timeout). cost_usd is the API list price, nothing is billed.
CREATE TABLE ai_calls (
    id                    INTEGER PRIMARY KEY,
    run_id                INTEGER REFERENCES runs(id),
    started_at            TEXT NOT NULL,
    purpose               TEXT NOT NULL,
    model                 TEXT NOT NULL,
    attempt               INTEGER NOT NULL,
    outcome               TEXT NOT NULL,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER,
    thinking_tokens       INTEGER,
    web_search_requests   INTEGER,
    cost_usd              REAL,
    duration_ms           INTEGER,
    num_turns             INTEGER
);
CREATE INDEX ai_calls_started_at ON ai_calls (started_at);
