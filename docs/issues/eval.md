# Issue: Evaluate pipeline performance and the impact of changes

## Goal

Answer two questions with data:

1. Does the pipeline find and rank the Jobs the candidate wants, and where does it lose them?
2. What did a given change do? A change can be the scoring prompt, the Profile, the Search terms, the model, the radius, the exclusion list, a Source, or later a guardrail.

The eval is a read-only evaluation of data the daily runs already store. It calls no `claude`, so it costs no quota, and it works retroactively over everything in the database. It is a new command, `jobsearch eval`, with German output like the rest of the CLI.

## Part 1: Know what was in effect (change markers)

Without this, a before/after comparison is guesswork. Build it first, since every day without it is lost for later comparisons.

- Migration `006`: add `fit_scores.prompt_hash` and `runs.settings_json`.
- `scoring.py`: hash `prompts/scoring.md` with the existing `profile_hash()` helper and write it in `store()`, next to `profile_hash`.
- Each daily run stores a settings snapshot in `runs.settings_json`: scoring model, prompt hashes (scoring, career extraction), Profile hash, active Search terms, hash of the exclusion list, radius values, enabled Sources (alert mail configured, Target companies present).
- `jobsearch eval --changes` lists the dates where the snapshot changed and what changed. These dates are the split points for comparisons.
- Rows from before this change have no markers. Their prompt version can be told from `git log` of `scoring.md` against `scored_at`.

## Part 2: Stage metrics (no labels needed)

From `postings`, `jobs`, `fit_scores`, `posting_terms` and `runs`:

- Postings per Source and day, new Jobs per day.
- Share of eligible, ineligible and unclear Jobs, with ineligible broken down by `ineligible_reason`.
- Share of Generic jobs and Low-confidence scores, unscored backlog, tier distribution.
- Per Search term: Jobs found, their tiers, and how many got a positive Job status. This shows which terms pay off.

All metrics can be limited to a period (`--since`, `--until`) or split at a change date (`--split DATE`).

## Part 3: Fit score against Job status

The Job status the candidate sets in the dashboard is the label:

| Job status | Label |
|---|---|
| `interesting`, `applied`, `rejected` | positive (the candidate wanted the Job; `rejected` is the company's decision) |
| `not_interesting` | negative |
| `new` | no label |

Output: a table of tier × (positive, negative, unlabeled), the precision of ★★★, and the positives in ★ ("übersehen"), which are the costliest misses. Counts are always shown next to percentages. Grouping: `--by model|profile|prompt|month`.

Example layout (made-up numbers):

```
Fit score   positiv   negativ   ohne Label
★★★             8         2         15
★★              4         6         30
★               1         5         60
Treffsicherheit ★★★: 80 % (8 von 10)
Übersehen in ★: 1
```

## Part 4: Found elsewhere (`jobsearch eval --funnel`)

Parts 2 and 3 only see what was collected. This part shows what the pipeline misses entirely.

- The candidate records openings found outside the tool in the private file `private/found_elsewhere.csv` (`company,title,url,found_on`). A dummy version goes into `private.example/`, and `jobsearch init` creates the file like the other private files. Entries can be added retroactively, since the database keeps everything it has seen.
- Each entry is matched to Postings by exact URL, or by `normalize_company` (`companies.py`) plus `title_similarity(title_tokens(...))` (`matching.py`) above a threshold.
- The report gives the stage per entry and in total, and each stage points to its lever:

| Stage | Lever |
|---|---|
| not collected | Search terms, Sources, Target companies |
| ineligible (with reason) | radius, exclusion list, remote detection |
| Generic job, unscored | matching, scoring backlog |
| ★ / ★★ / ★★★ (plus Job status) | scoring prompt, Profile, Feedback notes |

- Known gap: merges cannot be seen afterwards, since `merge()` deletes the merged Job. A merged Posting shows up under its target Job.

## Reading the numbers

These limits belong in the command's help text and in the docs:

- **Small numbers.** With a few dozen labels, a difference of one or two Jobs is noise. Show counts, not only percentages.
- **Selection bias.** The candidate mostly labels Jobs they looked at, which are mostly ★★★. ★ is rarely labeled, and that is where misses hide. Label a few ★ Jobs now and then.
- **Anchoring.** The stars are visible before the candidate decides, so labels are not independent of the AI.
- **Different Jobs before and after.** A split at a change date compares different weeks, and the market changes too. For prompt changes, the clean comparison is rescoring the same Jobs (follow-up).
- **Thresholds first.** Decide what counts as "works" and "needs rework" before reading results, for example the share of found-elsewhere entries not collected, the positives in ★, or the precision of ★★★. Otherwise the bar moves afterwards.

## Out of scope (follow-ups)

- Rescoring labeled Jobs with a changed prompt, without storing the result. It costs quota, and it must leave out the Feedback notes of the Jobs being rescored.
- A synthetic eval set in `evals/` for regression checks after prompt changes.
- A merge log, so merges can be evaluated and undone.

## Implementation notes

- New module `src/jobsearch/evaluation.py` with pure functions over a connection. `cli.py` adds the `eval` subcommand with its options.
- Files touched: `scoring.py` (`store`), `daily.py` and `db.py` (`start_run`, `finish_run`) for the snapshot, `config.py` for the new private file, `migrations/006_*.sql`.

## Tests

In-memory database with synthetic data, no network, no `claude`:

- Settings snapshot is written, `--changes` finds the right dates and differences.
- Stage metrics, including per Search term and the `--split` option.
- Label mapping and the tier × status table, including grouping.
- Funnel stage per case: URL hit, fuzzy hit, ineligible, unscored, not found.
- Migration `006` on an existing database, `store()` writes `prompt_hash`.

## Done when

All parts run against the live database and print plausible German output. Findings go into `docs/implementation-plan.md`.
