-- Which Search terms found a Bundesagentur Posting, to judge the terms by their results.
CREATE TABLE posting_terms (
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    term       TEXT NOT NULL,
    PRIMARY KEY (posting_id, term)
);
CREATE INDEX posting_terms_term ON posting_terms(term);
