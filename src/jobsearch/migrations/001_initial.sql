CREATE TABLE jobs (
    id                INTEGER PRIMARY KEY,
    company           TEXT NOT NULL,
    title             TEXT NOT NULL,
    location          TEXT,
    lat               REAL,
    lon               REAL,
    distance_km       REAL,
    is_target_company INTEGER NOT NULL DEFAULT 0,
    is_generic        INTEGER NOT NULL DEFAULT 0,
    remote_type       TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (remote_type IN ('unknown', 'none', 'hybrid', 'full')),
    eligibility       TEXT NOT NULL CHECK (eligibility IN ('eligible', 'ineligible', 'unclear')),
    ineligible_reason TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    reopening_of      INTEGER REFERENCES jobs(id),
    status            TEXT NOT NULL DEFAULT 'new'
                      CHECK (status IN ('new', 'interesting', 'not_interesting', 'applied', 'rejected')),
    status_changed_at TEXT,
    applied_on        TEXT
);

CREATE TABLE postings (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER REFERENCES jobs(id),
    source       TEXT NOT NULL CHECK (source IN ('bundesagentur', 'indeed', 'career_page')),
    source_id    TEXT NOT NULL,
    url          TEXT,
    title        TEXT NOT NULL,
    company      TEXT NOT NULL,
    location_raw TEXT,
    description  TEXT,
    is_snippet   INTEGER NOT NULL DEFAULT 0,
    raw_json     TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    UNIQUE (source, source_id)
);
CREATE INDEX postings_job ON postings(job_id);

CREATE TABLE fit_scores (
    id               INTEGER PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES jobs(id),
    tier             INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    reasoning        TEXT NOT NULL,
    matching_skills  TEXT,
    missing_skills   TEXT,
    part_time_signal TEXT,
    remote_signal    TEXT,
    low_confidence   INTEGER NOT NULL DEFAULT 0,
    profile_hash     TEXT NOT NULL,
    model            TEXT NOT NULL,
    scored_at        TEXT NOT NULL
);
CREATE INDEX fit_scores_job ON fit_scores(job_id);

CREATE TABLE feedback_notes (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id),
    status     TEXT NOT NULL,
    note       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE search_terms (
    term         TEXT PRIMARY KEY,
    origin       TEXT NOT NULL CHECK (origin IN ('derived', 'manual')),
    profile_hash TEXT,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE career_pages (
    company      TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    content_hash TEXT,
    last_checked TEXT,
    last_changed TEXT
);

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('daily', 'career_scan', 'score')),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    counts_json TEXT,
    error       TEXT
);
