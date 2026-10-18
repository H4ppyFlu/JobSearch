-- Small key/value store, for example the dashboard's last visit.
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
