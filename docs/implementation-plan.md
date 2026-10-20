# Implementation plan

How JobSearch gets built. The domain language is in [CONTEXT.md](../CONTEXT.md) and the main decisions are in [docs/adr](adr/). This plan is ordered so that every phase ends with something that runs and is useful on its own.

## Progress

- **Phase 1 done** (2026-10-15)
- **Phase 2 done** (2026-10-15). See "Phase 2 decisions".
- **Phase 3 done** (2026-10-15). The live daily run scored 40 Jobs in one call. The user reviewed the first Digest and found the scores and reasoning sound. There was no formal tally against their own ratings yet. See "Phase 3 decisions".
- **Phase 4 done** (2026-10-15). Fixture tests cover the four cases, and a live scoring run decided real candidate pairs correctly. See "Phase 4 decisions".
- **Phase 5 done** (2026-10-18). A live run parsed six real alert mails, stored their Jobs and moved the mails. See "Phase 5 decisions".
- **Phase 6 done** (2026-10-19). Tests cover once per day, the lock, the network retry and the 4-day catch-up, and a headless `pythonw -m jobsearch run` on a day with a successful run did nothing twice, as it should. On the next day `install-task` registered the task, and a run started by Task Scheduler collected, scored with `claude -p` (so the Claude login is visible there) and wrote the Digest; a second start the same day did nothing. See "Phase 6 decisions".
- **Phase 7 built** (2026-10-18). Tests go through the Flask test client, including the "Done when": a status with a note set in the dashboard appears in the next scoring prompt. On a copy of the live database every page rendered in well under a second. Waiting for the user's first look in the browser. See "Phase 7 decisions".
- **Phase 8 built** (2026-10-18). Setup, scan, Personio feed, browser fallback and the two-scans report run live. A repeat scan on the same day sent one page out of more than twenty to the AI. Coverage depends on the setup's URLs: after the setup and one recheck, about three quarters of the Target companies had a URL, and about half of those delivered Postings. Roughly half of the pages needed the browser. The rest needs URLs checked by hand, see "Phase 8 decisions".
- **Open for the user (Phase 8):** check the URLs in `private/companies.csv`. Replace landing pages and broken links with the page that actually lists the jobs; `jobsearch status` and the Digest warnings name the pages that fail or list nothing. Phase 8 counts as done once most Target companies deliver Postings.
- **Phase 9 done** (2026-10-18). A live daily run derived a full set of terms from the Profile in one call and searched them together with the manual ones; a second run made no call. An edited copy of the Profile (the real file stays untouched) gave a different set in one call, and existing Fit scores stayed as they were. See "Phase 9 decisions".
- **All phases built.** Still open: the Phase 8 URL check above.
- **Eval Part 1 done** (2026-10-20, `docs/issues/eval.md`). Each Daily run stores a settings snapshot and each Fit score its prompt hash. `eval --changes` on the live database showed the start of recording after one collect-only run. Fit scores from before have no prompt hash; `git log` of `scoring.md` dates them.
- Phase 0 is the user's job: IMAP access + `verarbeitet` folder, and Indeed alerts (sample mails needed for Phase 5).

## Verified facts (2026-10-15)

Checked live against the Bundesagentur API before planning:

- The current API is **v6**: `https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs` with header `X-API-Key: jobboerse-jobsuche`. v4 and v5 answer 403.
- Results are in `ergebnisliste`. Each entry has `referenznummer` (stable source ID), `stellenangebotsTitel`, `firma`, `stellenlokationen` with coordinates, `entfernung` (km from the search location), `homeofficemoeglich`, `datumErsteVeroeffentlichung`, part-time flags and sometimes a salary range.
- `wo=<city>&umkreis=30` works on the server, and `entfernung` comes back with each result. **No postal code table is needed for Bundesagentur Postings.**
- `veroeffentlichtseit=<days>` limits results to recent ones, which is what catch-up after missed days needs.
- `arbeitszeit=tz` (part-time) works. **There is no working home office filter**: `arbeitszeit=ho` returns 0, and `homeofficemoeglich` exists only as a result field.
- The details endpoint answers 403. **The full description is in the public HTML page** `https://www.arbeitsagentur.de/jobsuche/jobdetail/<referenznummer>`, inside the embedded `ng-state` JSON (`stellenangebotsBeschreibung`).

Consequence for remote Jobs: there is no server filter for home office, so a nationwide query per Search term returns thousands of results for a common term. Phase 2 includes a short spike to pick a strategy. The current favorite: a nationwide query with remote words in `was` (for example "<term> Homeoffice"), restricted to recent results, keep only `homeofficemoeglich = true`, and let the scoring step decide "fully remote or hybrid".

## Phase 2 decisions (2026-10-15, from the first live runs)

- **Radius query without date limit.** Per Search term the radius query asks for everything currently online within `RADIUS_TARGET_COMPANY_KM` (a few hundred results in total). Every Job in reach is therefore seen again each day and `last_seen` stays accurate, which the Reopening rule depends on.
- **Remote strategy.** A nationwide query per Search term, limited by `veroeffentlichtseit` to the days since the last successful run (at most `REMOTE_MAX_WINDOW_DAYS`), keeping only `homeofficemoeglich = true`. Measured: dozens of new nationwide offers per term and day, a small share with home office.
- **Home office is usually hybrid.** In the first run only about 2% of the home office Jobs outside the radius had a full-remote phrase in their description. So Eligibility got a second, rule-based step: after the description is fetched, a home office Job outside the radius stays `unclear` only with such a phrase, otherwise it is ineligible with the reason stored. This keeps the AI quota for real candidates. The trade-off is that fully remote ads without one of the phrases are dropped. Check the stored reasons now and then, and extend the phrase list in `eligibility.py` if needed.
- **Distances are straight-line**, so they come out shorter than road distance.
- **Untitled offers.** A few Bundesagentur offers have no ad title. The occupation (`hauptberuf`) is used instead.
- Remote Jobs are only seen while they are inside the nationwide date window, so their `last_seen` is not accurate. Phase 4 has to keep this in mind for Reopenings of remote Jobs.

## Phase 3 decisions (2026-10-15)

- **How `claude -p` is called** (`ai.py`): prompt on stdin, `--json-schema` so the CLI itself enforces the answer shape and returns it in `structured_output`, `--tools ""`, `--strict-mcp-config`, `--disable-slash-commands`, a short `--system-prompt` instead of the coding one, `--no-session-persistence`, and the temp directory as working directory so no project CLAUDE.md is loaded. The model that actually answered is read from `modelUsage` and stored with each score.
- **`--bare` is not usable.** It would skip even more, but it only accepts an API key and never reads the subscription login.
- **Two layers of checking.** The schema catches malformed answers. `scoring.py` also checks that every Job in the batch was scored exactly once. Either failure gets one retry, then the run records the error and the Jobs stay unscored. Quota and login errors are recognized from the CLI's message and are not retried.
- **Remote candidates are settled in the scoring call.** Unclear Jobs with a full-remote phrase are scored like the rest. Only an AI `remote = full` makes them eligible, anything else makes them ineligible with the reason stored. Their score is kept either way.
- **Low confidence** is set when a Job has no description at all or only snippets. Two Jobs in the first run had no description, because their offers live on partner portals.
- **Descriptions are cut at 6,000 characters** to keep a 40-Job batch at a predictable size. A full batch took about one minute with Sonnet.
- **`daily` scores by default** (`--no-score` skips it), and `jobsearch score [--limit N]` scores alone. A small `--limit` is the cheap way to test prompt changes.
- **The Digest covers the whole day.** It lists every Job scored that day, so a second run rewrites it with everything. ★ Jobs are a plain link list, ★★★ and ★★ get the reasoning and skills.
- The toast (`win11toast`, non-blocking `notify`) was confirmed live. It also fires when scoring failed, so a quota problem does not go unnoticed.
- Unclear Jobs with an unknown location are not scored yet. They stay unclear until a later phase decides what to do with them.

## Phase 4 decisions (2026-10-15)

- **Provisional Job instead of `job_id = null`.** A Posting with a recent candidate becomes a Job right away, with `jobs.match_candidate` pointing at the candidate (migration 002). It needs a Job anyway: Eligibility and placement belong to it, and the scoring call has to rate it. When the AI says "same", `matching.merge` moves its Postings to the candidate and deletes it, before the Digest is written. So no duplicate is ever shown, and nothing waits on anything.
- **Candidate rule:** same `normalize_company` key and a Jaccard overlap of at least 0.5 between title tokens, with gender markers ((m/w/d), :in, all genders, ...) and filler words removed. On the live data, about 10% of the Jobs in reach got a candidate. Several pairs were clearly different roles at large employers, which is cheap to let the AI decide (about 1,500 characters per candidate in the prompt).
- **Only Jobs in reach are matched.** Ineligible Postings never look for candidates and are never candidates, since a duplicate nobody sees costs nothing.
- **Reopening needs a reliable `last_seen`.** It only applies to candidates within `RADIUS_TARGET_COMPANY_KM`, which the daily radius query sees every day. For remote Jobs the AI always decides "same Job?", whatever the age. The trade-off: a real reopening of a remote Job is merged into the old one.
- **The same ad coming back** (same source ID) after `REOPENING_DAYS` of absence is also a Reopening: its Posting moves to the new Job, the old Job keeps its history.
- **One call, three answers.** The scoring schema gained `generic` and `same_as_candidate`. A merged Job gets no score of its own. A chain of same Jobs in one batch collapses into the oldest. Generic jobs are never merged and never candidates.
- **Most Generic jobs are staffing agency ads.** In the live run, 11 of 40 Jobs were flagged generic, 10 of them from staffing agencies, which matches the definition in CONTEXT.md. The Digest marks them as "Sammelanzeige".
- Jobs collected before Phase 4 got their candidates once through a local script, so the live run had real pairs to decide.

## Phase 5 decisions (2026-10-16 and 2026-10-18)

- **Built without sample mails:** IMAP settings (`IMAP_*`, optional, so the Daily run skips mail when they are empty), `Mailbox` with login, search by sender, `BODY.PEEK` so mails stay unread, and MOVE with a COPY + delete fallback. In `run_daily`, a mail is moved only after its Postings are committed, and a mail without Postings stays in the inbox and shows up as a warning in the Digest. A mailbox failure is recorded as a collection error and does not stop the run.
- **The parser reads the plain-text part**, not the HTML. Each job is one block: title, "Company - Place", optional salary and marker lines ("Schnellbewerbung", "Aktiver Arbeitgeber"), snippet, age, tracking link. Header and footer blocks are skipped because their last line is not a job link. The HTML part is layout only.
- **Two kinds of results.** Organic ones link to `/rc/clk` with the job key `jk`, which becomes the source ID and a neutral `viewjob?jk=` URL. Sponsored ones (`/pagead/clk`) carry no key, so their source ID is a hash of title, company and place: the same ad in a second mail is recognized. Personalized tracking links are not kept for organic results, as the mail asks not to pass them on. In the samples, more than half of the results were sponsored, and many of them were off-topic or outside the alert's radius.
- **Tests use a synthetic copy** of the real block structure (`tests/fixtures/indeed_alert.eml`). The real mails stay in `data/` and never go into git, since they contain personalized links.
- **Places are looked up in the GeoNames postcode table**, which moved here from Phase 8: `jobsearch init` downloads DE and CH (CC BY 4.0) into `data/geo/places.tsv`. Postcodes of one place and district are averaged. A short or ambiguous name resolves to the candidate nearest home, which is safe because alerts are limited to a radius around home. Country-only places ("Deutschland", typical for sponsored ads) get no location and stay `unclear`, so they are never scored.
- **A "Remote" place sets home office**, so such a Job goes through the same full-remote check as a Bundesagentur Job. No remote alert was in the samples yet, so this path is only covered by the synthetic fixture.
- **The processed folder is resolved by name** before any mail is touched. A server may nest it under the inbox (`INBOX/verarbeitet`), so a folder whose last path part matches is accepted. A missing folder fails the mail step before anything is stored.
- **A missing Profile no longer aborts the run.** It is recorded like an AI failure, the Digest is still written, and the Jobs stay unscored.
- **Runtime grows with Search terms.** Every new home office Job outside the radius gets its description fetched for the full-remote check. After a few days without a successful run and with more Search terms, one run fetched several hundred descriptions and took about six minutes. Daily runs stay small, since the window only covers the days since the last successful run.
- **Indeed also sends account mails** (login codes, alert confirmations, profile changes), from the same domain. They are recognized by whole subject phrases and left alone, so they are not reported every day. Single words are not enough, since words like "Account" also appear in job titles.
- **A new alert only starts after its confirmation mail is clicked.** Worth noting in Phase 0.

## Changed from the design session

Career pages that need JavaScript are rendered by Python with Playwright (headless Chromium, free) and not handed to an agent. A Claude agent's web fetch does not run JavaScript either, so the agent would have had the same problem. The AI in the Career scan now only *extracts* Postings from already rendered page text and never browses. This makes ADR 0001 hold more strictly.

## Tech stack

- Python 3.11, packaged with `pyproject.toml`, installed in a local `.venv`
- `requests`, `beautifulsoup4`, `python-dotenv`, `flask` (dashboard), `win11toast` (toasts with click action), `playwright` (Career scan only), `pytest`
- SQLite through the standard library `sqlite3`, schema versioned with plain numbered SQL files
- AI through `claude -p` (Claude Code headless, Pro subscription), called as a subprocess with input on stdin, `--model` from config, `--output-format json`, and no tools allowed for scoring and extraction
- Windows Task Scheduler for the trigger, running `pythonw` so no console window appears

## Repository layout

```
JobSearch/
├── CONTEXT.md
├── README.md
├── pyproject.toml
├── .gitignore                 # .env, private/, data/, .venv/
├── .env.example
├── private.example/           # dummy versions of the private files
├── private/                   # gitignored: real company list, exclusions, Search terms
├── data/                      # gitignored: jobsearch.sqlite, digests/, postcodes, logs
├── docs/adr/, docs/implementation-plan.md
├── src/jobsearch/
│   ├── cli.py                 # jobsearch init | run | daily | career-scan | score | dashboard | status
│   ├── config.py              # loads .env and private/ files, fails clearly when something is missing
│   ├── db.py, migrations/
│   ├── sources/bundesagentur.py, sources/indeed_mail.py, sources/career_pages.py
│   ├── eligibility.py         # distance, Obvious mismatch rule
│   ├── matching.py            # Posting → Job, Reopening, Generic job flag
│   ├── ai.py                  # the only module that calls claude -p
│   ├── scoring.py, prompts/
│   ├── digest.py, notify.py
│   ├── scheduler.py           # what is due, lock file, catch-up window
│   └── dashboard/             # Flask app + templates (German labels)
└── tests/ (with fixtures/: saved API JSON, detail HTML, Indeed .eml, career pages)
```

## Configuration

`.env` (private):
`PROFILE_PATH`, `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_PROCESSED_FOLDER=verarbeitet`, `MODEL_SCORING=sonnet`, `MODEL_EXTRACTION=haiku`, `MAX_SCORED_PER_RUN=40`, `FEEDBACK_NOTES_IN_PROMPT=20`, `HOME_LOCATION`, `HOME_LAT`, `HOME_LON` (required, no defaults), `RADIUS_KM=30`, `RADIUS_TARGET_COMPANY_KM=70`, `REOPENING_DAYS=21`, `CAREER_SCAN_INTERVAL_DAYS=7`

`private/` (gitignored, with dummies in `private.example/`):
- `companies.csv`: name, locations, career URL, ATS type, render mode (filled in by the Career scan setup)
- `exclusions.txt`: one title pattern per line (Praktikum, Werkstudent, Abschlussarbeit, ...)
- `search_terms.txt`: manual Search terms. Derived terms are stored in the database with the Profile hash.

## Database schema (first version)

- **jobs**: id, company, title, location, lat, lon, distance_km, is_target_company, is_generic, remote_type (unknown/hybrid/full), eligibility (eligible/ineligible/unclear), ineligible_reason, first_seen, last_seen, reopening_of (→ jobs.id), status (new/interesting/not_interesting/applied/rejected), status_changed_at, applied_on
- **postings**: id, job_id (null while a match is pending), source (bundesagentur/indeed/career_page), source_id, url, title, company, location_raw, description, is_snippet, raw_json, first_seen, last_seen. Unique on (source, source_id).
- **fit_scores**: job_id, tier (3/2/1), reasoning, matching_skills, missing_skills, part_time_signal, remote_signal, low_confidence, profile_hash, model, scored_at. One row per scoring, so history is kept.
- **feedback_notes**: id, job_id, status, note, created_at
- **search_terms**: term, origin (derived/manual), profile_hash, active
- **career_pages**: company, url, content_hash, last_checked, last_changed
- **runs**: id, kind (daily/career_scan/score), started_at, finished_at, ok, counts_json, error

## Phases

### Phase 0: Prerequisites (by hand)

1. Mailbox: enable IMAP in the settings, create the folder `verarbeitet`, note the password for `.env`.
2. Indeed: create alerts around the home location with a radius of 30 km and a few remote alerts, all sent to the new address, and click the confirmation mail of each. Let 2–3 mails arrive, since Phase 5 needs real samples.
3. Check that `claude -p "say ok"` works in a fresh terminal with the Pro login.

### Phase 1: Skeleton

- `pyproject.toml`, `.gitignore`, `.env.example`, `private.example/`, README with setup steps
- `config.py` with clear errors for missing values, and `db.py` with migration 001 (schema above)
- CLI `jobsearch init` (creates `data/`, the database and `private/` from the examples) and `jobsearch status` (counts per table, last runs)
- **Done when** `jobsearch init && jobsearch status` works on a fresh clone, and `git status` shows no private file.

### Phase 2: Bundesagentur collection and Eligibility (no AI yet)

- `sources/bundesagentur.py`: radius query per Search term (manual terms for now) with `veroeffentlichtseit` = days since the last successful run (at least 1, at most 100). Paging, and a polite delay between requests.
- Fetch the detail HTML for each *new* `referenznummer` only, and extract `stellenangebotsBeschreibung` from `ng-state`.
- Remote strategy spike (see Verified facts). Decide, and record the result in this file.
- `eligibility.py`: Obvious mismatch from `exclusions.txt` (case-insensitive word match on the title), distance from `entfernung` or coordinates, Target company radius when the company matches the list.
- `matching.py`, minimal version: one Posting per Job, deduplicated only by source ID, and `last_seen` updated on every sighting.
- **Done when** `jobsearch daily` fills the database with real Jobs, a second run on the same day adds nothing new, and the tests pass on saved API fixtures.

### Phase 3: Fit scoring, Digest, toast

- `ai.py`: one function that runs `claude -p` with prompt + JSON input on stdin, parses the JSON answer, retries once on invalid output, and raises a clear error on quota or login problems.
- `scoring.py`: picks up to `MAX_SCORED_PER_RUN` unscored eligible Jobs (newest first), batches them into one call with the Profile text, the last Feedback notes and the scoring rubric (tiers, signals, German reasoning). Snippet-only Jobs are marked `low_confidence`. Writes `fit_scores` with the Profile hash.
- Failure handling: when the AI step fails, the run still counts as collected, Jobs stay unscored, and the error lands in `runs`.
- `digest.py`: German Markdown in `data/digests/YYYY-MM-DD.md` with new Jobs by tier, Reopenings and "x Jobs waiting for scoring".
- `notify.py`: a toast only when there are new ★★★/★★ Jobs or a scoring backlog. Clicking it opens the dashboard (a Digest file until Phase 7).
- **Done when** one real daily run scores at most 40 Jobs and the reasoning holds up when you read it. Check this against 10 Jobs you would rate yourself.

### Phase 4: Job matching across Sources, Generic jobs, Reopenings

- Candidate search for a new Posting: same normalized company (legal forms and "(m/w/d)" stripped) and similar title (token overlap above a threshold).
- If the candidate was last seen less than `REOPENING_DAYS` ago, the AI decides "same Job?" in the same daily call as scoring. Until then the Posting has `job_id = null`, and it never blocks anything else.
- If the candidate was last seen 21+ days ago, create a new Job with `reopening_of`.
- Generic job flag: the AI sets it during scoring, and matching skips flagged Jobs.
- **Done when** fixture tests cover: same Job on two Sources, same title for two different Jobs, a repost within 21 days, and a Reopening after 30 days.

### Phase 5: Indeed alert mail

- `sources/indeed_mail.py`: IMAP over SSL to `IMAP_HOST`, only mails from Indeed senders in the inbox. Parse each job block (title, company, location, snippet, link), resolve the `jk` job key from the tracking link as the source ID, and move the mail to `verarbeitet` only after its Postings are stored.
- The parser is built and tested against the real sample mails from Phase 0. A mail that yields zero Postings stays in the inbox and is reported in the Digest.
- Snippet rule: a Low-confidence score is shown normally. If the snippet already looks like a mismatch, no lookup for a fuller description happens.
- **Done when** the sample mails parse in tests and a live run moves them.

### Phase 6: Scheduling

- `scheduler.py` and `jobsearch run`: take a lock file, run the Daily run if none succeeded today, run the Career scan if the last one is `CAREER_SCAN_INTERVAL_DAYS` old or older, then score, then send the Digest and toast. If the network is not up yet, retry 3 times with backoff and then give up quietly with a toast.
- `jobsearch install-task`: registers a Task Scheduler task with a logon trigger and 5-minute delay, running `pythonw -m jobsearch run` as the current user (so the Claude login is available). `jobsearch uninstall-task` removes it.
- Logging to `data/logs/` with rotation.
- **Done when** two logons on one day give exactly one run, and a simulated 4-day gap catches up 4 days.

## Phase 6 decisions (2026-10-18)

- **"Done today" means a Daily run finished today, with or without errors.** Otherwise a lasting error (a wrong mail password) would repeat the whole run and its AI call at every logon. The errors are in the Digest and the toast, and the next day's remote window still reaches back to the last run without errors.
- **The lock is an OS file lock** (`data/run.lock`), not a PID file. Windows drops it when the process dies, so a crash never blocks later runs. Manual `daily` and `score` take the same lock, so they never overlap the scheduled run.
- **Network check:** a TCP connect to the Bundesagentur host, retried after 30, 60 and 120 seconds. Then a toast, and the next logon tries again.
- **The task is defined as XML** and registered with `schtasks /XML`, since plain `schtasks` options cannot set a logon delay together with "run on battery" and "only in the user's session". It runs with the user's interactive token and least privilege, so `claude -p` sees the user's login.
- **Headless means no console.** Under `pythonw` there is no stderr, so the console log handler is skipped, every failure ends in the log and a toast, and `claude -p` is started without a window (`CREATE_NO_WINDOW`).
- **Logs rotate** at about 1 MB, five files kept.
- **The Career scan is not part of `run` yet.** It joins in Phase 8, using `CAREER_SCAN_INTERVAL_DAYS`.

### Phase 7: Dashboard

- `jobsearch dashboard`: Flask on `127.0.0.1`, opens the browser.
- Job list with filters (Fit score, Job status, Eligibility, date, Source), "new since last visit" marker, low-confidence and Generic job badges.
- Detail view: full description, all Postings with links, score reasoning with matching and missing skills, Reopening chain.
- Status buttons with an optional Feedback note. "applied" sets `applied_on` to today.
- German labels, read and write on the same SQLite file.
- **Done when** a status change in the browser shows up in the next scoring prompt's Feedback notes.

## Phase 7 decisions (2026-10-18)

- **Server-rendered Flask with Jinja templates**, no JavaScript build. The filters submit on change; without JavaScript there is a button.
- **The list hides ineligible Jobs by default** ("in Reichweite oder unklar"). "alle" and "außerhalb" make them visible, to find wrong filter decisions.
- **"New since last visit" uses Job ids**, not dates: the dashboard remembers the highest Job id of the previous visit in a small `meta` table (migration 003). Page views less than an hour apart count as one visit, detail pages included, so filtering and reading keep the markers. A visit ends after eight hours at the latest. The very first visit marks everything as new. If a run holds the write lock, the visit is just not recorded and the page still loads.
- **Local only:** the server listens on `127.0.0.1`, refuses requests with another host name (against DNS rebinding), and status changes need a token created at each start. Any web page could otherwise send a form to `127.0.0.1` and change a status, and with it the scoring prompt.
- **The status can also be set right in the list**, for Jobs whose title already rules them out. The page reloads at the same row with the same filters; a note needs the detail page.
- **Search terms are recorded per Posting** (`posting_terms`, migration 004), so the list can be filtered by the term that found a Job, with a Job count per term. This helps to judge which terms bring mostly noise. Only Bundesagentur Postings have terms. Older Postings get theirs when a run sees them again, which the radius query does daily for everything in reach.
- **"applied" sets `applied_on` only once.** Choosing it again later keeps the first date.
- **A Feedback note is stored only when text is entered**, together with the status chosen at that moment. The scoring prompt already reads the most recent notes (`FEEDBACK_NOTES_IN_PROMPT`).
- **The Reopening chain** follows `reopening_of` in both directions, so every advertisement of the same opening links to the others.

### Phase 8: Career scan

- **Setup** (`jobsearch career-setup`, one time and then on demand): for each company in `private/companies.csv` without a URL, a `claude -p` call with web search finds the career page and guesses the ATS type. The result is written back to the CSV for you to check. This is the only step where the AI browses.
- Postcode table: already there since Phase 5 (`geo.py`), reuse it for career page locations.
- **Weekly scan:**
  1. Known ATS feeds (for example Personio XML) are parsed directly, without AI.
  2. Other pages are fetched with `requests`, or Playwright when the render mode says so. Visible text is normalized and hashed.
  3. Only pages whose hash changed go to `MODEL_EXTRACTION` (Haiku), which turns the text into Posting JSON.
  4. Postings then run through the normal Eligibility, matching and scoring.
- Report companies whose page failed or returned no Postings for two scans in a row.
- **Done when** the first full scan covers all companies and reports how many needed Playwright, and the second scan in the same week sends close to nothing to the AI.

## Phase 8 decisions (2026-10-18)

- **Setup answers by number, not by name.** The first live run failed because the model tidied up company names ("XY GmbH" became "XY"), so the check never matched. Each company now gets a number in the prompt.
- **The setup finds landing pages more often than job lists.** About two thirds of the companies got a URL in the first pass, and most of those were general "Karriere" pages without the list itself. `career-setup --recheck` asks again for the companies whose page failed or listed nothing, and passes on what went wrong. Entries without problems are never overwritten. `companies.csv.bak` keeps the state from before the first setup.
- **Static HTML first, browser only when needed.** Almost every German job title carries a gender marker like "(m/w/d)". A page without any is rendered with Playwright instead of being sent to the AI, which in the first scan would have saved most of the extraction calls. After rendering the page goes to the AI either way, since English pages have no marker.
- **Playwright is optional** (`pip install -e ".[browser]"`, then `playwright install chromium`). Without it, such pages are reported as needing a browser and cost no AI call.
- **The hash covers the visible text without URLs**, so tracking parameters in links do not count as a change. Rendered pages turned out stable between scans.
- **An unchanged page keeps its Postings fresh.** Their `last_seen` moves to the scan date, otherwise a weekly page would look absent and later produce false Reopenings.
- **Feeds before AI.** Personio's XML feed gives title, offices and full description without any AI call. Other ATS types were rare in the setup result (a couple of Workday and SuccessFactors sites), so they go through the page path for now.
- **A Posting without a place gets the company's locations** from `companies.csv`. Many job lists show no place per job, and without one the Job would stay unclear and unscored.
- **Descriptions without AI.** For new career Postings in reach, the linked job page is fetched and its visible text stored as the description, so the scoring sees more than the title.
- **Reported after two scans in a row:** a page that fails (with the error) or lists no Postings. A changed URL starts counting from zero.
- **The scan runs inside the Daily run when due** (`CAREER_SCAN_INTERVAL_DAYS`, default 7), before scoring, so its Jobs are scored in the same run. A scan whose AI step failed does not count as done. `jobsearch career-scan` runs it alone.

### Phase 9: Search terms derived from the Profile

- When the Profile hash changes, one `claude -p` call proposes 5–8 Search terms from the Profile. They are stored as `derived` and used together with `private/search_terms.txt`.
- Shown in `jobsearch status`, so you can see which terms are active.
- **Done when** editing the Profile file changes the derived terms in the next run, and existing Fit scores are left alone (as decided, no re-scoring).

## Phase 9 decisions (2026-10-18)

- **Only derived terms live in the `search_terms` table**, each with the Profile hash it came from. Manual terms stay in `private/search_terms.txt`, so the file remains the one place to edit by hand.
- **A changed Profile replaces the whole derived set.** In the live check a small addition to the Profile kept only about a quarter of the terms, so the set can shift noticeably on an edit. Terms found earlier stay recorded on their Postings.
- **Opt-out with `-term`** in `private/search_terms.txt`, for derived and manual terms alike. It survives a new derivation, since the AI does not see it.
- **A failed derivation keeps the old terms** and is tried again at the next run. It becomes a warning in the Digest. Without a Profile, the daily run searches the manual terms only; the scoring step already reports the missing Profile.
- **Plain terms only.** An answer with empty or duplicate terms, more than four words, or parentheses and quotes is asked for again, since each term goes into the Bundesagentur `was` field as it is.
- **More terms, more Jobs to score.** The first run with derived terms brought a few dozen new Jobs. `MAX_SCORED_PER_RUN` still caps each scoring call, so the extra Jobs are spread over the next runs.
- No re-scoring when the Profile changes, as decided earlier.

## Testing approach

- Every Source parser is tested against saved real responses in `tests/fixtures/`, using anonymized copies and never personal data.
- Eligibility, matching and Reopening logic are pure functions with table-driven tests.
- `ai.py` is replaced by a fake in tests, so the test suite never uses quota.
- One manual end-to-end check per phase against the live Sources, as noted under "Done when".

## Risks and open points

- **Unofficial API:** the Bundesagentur API is public but not formally supported, and it already moved from v4 to v6. Keep the client small and fail loudly on schema changes.
- **Indeed mail format** can change without notice. The parser fails visibly, since mails stay in the inbox.
- **Pro quota:** the per-run cap and the Haiku extraction keep usage low. Watch the first week and tune `MAX_SCORED_PER_RUN`.
- **Claude login inside Task Scheduler:** the task runs in the user's session, which should be enough. Confirm with the first run started by the task (see Progress).
- **Career pages** change layout and URLs. The "no Postings twice in a row" report catches this.
