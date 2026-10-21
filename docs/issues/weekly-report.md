# Issue: Weekly report with recommendations

Status: open. Implement on branch `implement-weekly-report`.

## Goal

`jobsearch eval` already measures the pipeline, but only prints numbers that the candidate has to read and act on. This issue closes the loop: once a week the pipeline collects its own metrics, a single `claude -p` call reads them and derives concrete Recommendations (which Lever to change, and how), and the candidate accepts or rejects them in the dashboard. The next report sees what was changed and how the numbers moved since.

Four rules shape the design:

- **Python measures, the AI judges.** All numbers come from the existing functions in `evaluation.py`. The AI only interprets them ([ADR 0001](../adr/0001-script-first-ai-only-for-judgment.md)).
- **The AI writes no numbers.** Every metric gets an ID, and the AI cites IDs. The report fills in the values from its own input. This is the grounding principle of the Career scan (`career.grounded()`), applied to numbers.
- **Aggregates only.** The AI gets counts, the candidate's own Search terms, rule-made ineligible reasons and settings changes. No job title, company name from an ad or description goes in, so there is no Third-party text in this call and no injection surface.
- **"No change needed" is a valid result.** The AI must not invent a Recommendation every week.

## Facts to build on

- `evaluation.py` has pure functions over a connection: `stage_metrics(conn, period)` (`StageMetrics`: Postings per Source, eligibility, ineligible reasons by `reason_kind()`, Generic jobs, tiers, Low-confidence scores, unscored backlog), `term_metrics(conn, period)` (Jobs, tiers and positive labels per Search term), `score_tables(conn, period, by)` (tier × label), `trace()` / `read_found()` for the funnel of `private/found_elsewhere.csv`, `snapshots()` and `changes()` for settings changes. `Period` builds the date conditions.
- `career.scan_due(conn, today, interval_days)` decides whether the Career scan is due; `config.career_scan_interval_days` comes from `CAREER_SCAN_INTERVAL_DAYS`. The report's due logic follows the same pattern.
- `ai.ask()` is the only place that runs `claude -p`, with a JSON schema and no tools. Tests replace it with a fake.
- The Digest is written from the database to `data/digests/`; the report works the same way.
- The dashboard (`dashboard.py`) protects writes with a token created at each start and listens on `127.0.0.1` only. `scheduler.RunLock` is the file lock that keeps runs from overlapping.
- The Lever table in `docs/issues/eval.md` ("Stage → Lever") is the starting point for the Lever list.
- The last migration is `006_change_markers.sql`; `ai-usage` takes `007`, so this issue takes `008` (or `009` if `market-report` is implemented first).

## Part 1: Report input (metrics with IDs, no AI)

- New module `src/jobsearch/report.py`. A pure function `report_input(conn, today) -> ReportInput` builds everything the AI will see, from the existing `evaluation.py` functions:
  - the last 7 days and the whole history side by side: stage metrics, metrics per Search term, tier × label table, funnel stages of `found_elsewhere.csv` (counts per stage only);
  - the settings changes since the last report (`changes()`; since the first snapshot when there is no report yet).
- Every metric is a `Metric(id, value, n, label)`: a stable dotted ID (for example `term.<term>.jobs.7d`, `stage.ineligible.<reason kind>.all`, `score.tier3.precision.all`), its value, the count it rests on (`n`) and a German label for the report. IDs must be unique. Search terms in IDs are slugified, and the mapping back to the real term is kept for the target check in Part 2.
- `ReportInput.to_prompt_json()` gives the JSON for the prompt. Nothing else from the database goes in.

## Part 2: The report (AI call, checks, storage)

### Prompt and schema

- New prompt `src/jobsearch/prompts/weekly_report.md` (English, with German output), model `MODEL_SCORING`, one `ai.ask()` call per report with `purpose="weekly_report"` (see `ai-usage.md`).
- The prompt carries the limits from "Reading the numbers" in `docs/issues/eval.md` (small numbers, selection bias, anchoring, different weeks), says that no Recommendation is a valid answer, and says that thin labels call for `label_more` rather than a verdict on precision. It states that the input is aggregates only, which is why it has no "Third-party text" section.
- Levers are a list in code (`LEVERS` in `report.py`), and the JSON schema's enum is built from it, so adding a Lever is one line plus a test. Initial list: `search_term_off`, `search_term_new`, `exclusion`, `radius`, `remote_phrase`, `scoring_prompt`, `profile`, `career_url`, `label_more`.
- Answer shape:
  - `summary`: a short German assessment.
  - `recommendations`: 0 to 5, highest priority first. Each has `lever` (from `LEVERS`), `target` (the term, the exclusion word, or empty), `action` (one German sentence), `evidence` (list of metric IDs, at least one) and `confidence` (`niedrig` / `mittel` / `hoch`).
  - `new_levers`: at most 2 ideas for a Lever not in the list, each with `name`, `what`, `why` and `evidence`. They can never be applied, only noted (Part 4).

### Checks (in code, whatever the model answers)

A Recommendation or Lever idea is dropped, logged at INFO with the reason, and counted, when:

- an `evidence` ID is not in the input;
- all of its `evidence` metrics have `n < MIN_EVIDENCE_N` (5);
- its `action`, `what` or `why` contains a number (digits, also as part of `50%` or `3,5`) that is not the value of one of its own `evidence` metrics. The text should not need numbers at all; the report shows the values next to the item;
- `target` does not fit the Lever: `search_term_off` must name an active Search term, `exclusion` must not already be on the list, `search_term_new` must not already be active.

The `summary` follows the same number rule; a `summary` that breaks it is replaced by a German note that the assessment was dropped. Counts go into `ReportCounts` (`recommendations`, `dropped`, `new_levers`) and into the report row.

### Storage and output

- Migration `008_reports.sql`:
  - `reports`: id, created_at, period_start, period_end, model, prompt_hash, input_json, answer_json, counts_json, error.
  - `recommendations`: id, report_id, position, lever, target, action, evidence_json, confidence, kind (`change` / `new_lever`), decision (`open` / `accepted` / `rejected`), decided_at.
- The Markdown report goes to `data/reports/YYYY-Www.md` (ISO week), written from the database like the Digest: summary, then each Recommendation with its evidence as a small table of label, value and `n`, then the Lever ideas, then "verworfen: n" with the reasons in the log.
- New command `jobsearch report` (takes the run lock, makes the call, writes the row and the file, prints the German report). `--dry-run` prints the input JSON without calling the AI.

## Part 3: Integration (due logic, toast, dashboard page)

- `REPORT_INTERVAL_DAYS=7` in `config.py` and `.env.example`. `report_due(conn, today, interval_days)` is true when the last report without an error is that many days old or there is none.
- The Daily run makes the report when due, after scoring, so the week's scores are in. A failed call is stored with its error, does not count as done and is tried again at the next run. It never blocks collection or the Digest.
- The toast mentions a new report and opens the dashboard's report page.
- Dashboard: a page "Bericht" with the newest report and a list of earlier ones, rendered from the database. The settings snapshot gains `report_prompt_hash`, so `eval --changes` shows prompt changes of the report too.

## Part 4: The loop (accept, reject, history)

### Accept and reject

- Each Recommendation on the report page has "übernehmen" and "ablehnen"; each Lever idea has "aufnehmen" and "verwerfen". All are POST forms with the dashboard token.
- Only these Levers can be applied, and only as one appended line in a `private/` file:
  - `search_term_off`: `-<term>` in `private/search_terms.txt`;
  - `search_term_new`: `<term>` in `private/search_terms.txt`;
  - `exclusion`: `<word>` in `private/exclusions.txt`.
- The write takes `scheduler.RunLock`; while a run holds it, the page says to try again later. The decision and its date are stored on the row. There is no undo in the dashboard: the page says the file can be edited by hand.
- All other Levers show "übernehmen" as "erledigt (von Hand)": the candidate makes the change themselves (radius in `.env`, remote phrases in `eligibility.py`, the prompt, a career URL) and marks it. The Profile is never written; for `profile` the page only shows the suggestion.
- The next Daily run picks the change up in its settings snapshot, so it shows in `eval --changes` as a split point.
- "aufnehmen" on a Lever idea only marks it. Turning it into a real Lever is a code change done by hand later.

### History in the next input

- `report_input()` adds the Recommendations and Lever ideas of earlier reports with their decision and date, so the AI does not repeat rejected ones.
- For each accepted Recommendation decided at least 7 days ago, it adds before/after metrics around the decision date (same logic as `--split`), with their own IDs (for example `rec.<id>.term.<term>.jobs.before`). The AI can then judge whether the change worked, and the number rule still holds.

## Language

Add to `CONTEXT.md`:

- **Weekly report** ("Wochenbericht"): the weekly AI reading of the pipeline's own metrics, with a short assessment and up to five Recommendations.
- **Recommendation** ("Empfehlung"): one proposed change to a Lever, backed by metric IDs, which the candidate accepts or rejects.
- **Lever** ("Stellschraube"): a setting that changes what a Stage lets through, from a fixed list in code. The AI can propose new ones; only the candidate adds them.

## Tests

In-memory database with synthetic data, no network, `ai.ask()` faked:

- `report_input()`: unique IDs, correct values and `n` for 7 days and history, the changes since the last report, nothing from postings' text in the JSON.
- Checks: unknown ID, all evidence below `MIN_EVIDENCE_N`, a number in `action` that is not an evidence value, a number that is one, wrong targets per Lever, more than five Recommendations, an empty answer as a valid report.
- `report_due()` with no report, a recent one, an old one and a failed one.
- The Daily run makes the report when due, and a failing fake AI leaves the run and the Digest intact.
- Dashboard: accept appends exactly one line to a temporary `private/` file, reject writes nothing, a request without token is refused, a held run lock is reported.
- History: an accepted Recommendation older than 7 days brings before/after metrics into the next input.
- Migration `008` on an existing database.

## Done when

A live report on the real database gives Recommendations the candidate finds plausible, and one accepted Recommendation shows up in `eval --changes` after the next Daily run. Findings, including how many items the checks dropped, go into `docs/implementation-plan.md`.

## Out of scope (follow-ups)

- Applying Levers that live in `.env` or code.
- A synthetic set of metric inputs to check the prompt's Recommendations after prompt changes, like `check-injection` does for injections.
- Sending the report anywhere but the local disk and dashboard.
