# Issue: Sorted out Jobs (AI marks clear mismatches during scoring)

Status: Part 1 done. Implement on branch `implement-sorted-out`.

## Goal

Python can only rule out a Job by its title (exclusion list), its distance or missing full remote work. Everything else reaches the scoring model, and on the live data about three quarters of all scored Jobs end up at ★. Many of them are not a weak fit but a different field altogether, found by a broad Search term (for example a chef position for a profile in accounting). They clutter the dashboard list and the Market report.

This issue lets the scoring model mark such Jobs as **Sorted out** in the same call that scores them. A Sorted out Job is kept in the database with its Fit score and reasoning, but hidden by default. A new manual command `jobsearch rescore` applies the new judgment to the existing ★ Jobs once.

## Decisions

- **Hide, never delete.** The lasting record, `eval --funnel` and Reopening detection need the Job. The Job stays exactly as it is; only the list hides it.
- **A flag next to the tier, not a fourth tier.** `sorted_out` is a boolean on the Fit score and is only allowed with tier 1. So every Sorted out Job is a ★ Job, all existing ★ metrics stay valid, and a positive Job that was sorted out shows up as "positiv in ★" in `eval --scores` without any change there. A tier 0 was rejected: it needs a rebuild of `fit_scores` (the `CHECK (tier IN (1, 2, 3))`) and changes in every `(3, 2, 1)` loop.
- **Not through Eligibility.** Setting `ineligible` with an AI reason (as `AI_NOT_REMOTE_REASON` does) was rejected: Eligibility is the rule-based reach decision, and the funnel would blame the wrong Stage.
- **Strict criterion.** Sorted out means a clearly different occupational field, or a role the Profile shows no path to. A conflict with a stated preference (on-site work, kind of employer, ...) gives ★ but is not a reason to sort out. In doubt, do not sort out.
- **No exceptions.** Jobs of Target companies and Generic jobs can be sorted out as well; they are only hidden.
- **The candidate's own status wins.** Only Jobs that are Sorted out *and* still have Job status new are hidden. Once the candidate sets any status, the Job is back in the default list.
- **Working hours are not part of this issue.** The candidate states their preference in the Profile; the prompt gets no rule about full-time or part-time.

## Facts to build on

- `scoring.SCHEMA` lists the fields per Job; `check_answer()` already rejects answers with missing or extra Jobs, and a failing check gets one retry through `ai.ask()`.
- `scoring.store()` writes one `fit_scores` row per Job and also merges same Jobs, sets `is_generic`, and settles the remote decision of `unclear` Jobs.
- Migrations go up to `009_skill_groups.sql`; this issue takes `010`.
- Until now a Job practically never has more than one `fit_scores` row. `rescore` changes that, and not every reader picks the newest row:
  - newest row: `dashboard.LATEST_SCORE` (max id), `market.NEWEST_SCORE`, the Digest (`max(id)`).
  - every row: `evaluation.stage_metrics()` and `term_metrics()` (`LEFT JOIN fit_scores s ON s.job_id = j.id`), `evaluation._POSTINGS` (funnel), `score_tables()`, and the tier counts in `cli.py` (`status`). These would count a rescored Job twice.
- The Digest selects Jobs whose newest score is from that day (`s.scored_at LIKE ?`), so a rescored old Job would appear there as new.
- `MAX_SCORED_PER_RUN` (default 40) caps the Jobs per scoring call.
- `jobsearch check-injection` builds the real scoring prompt from synthetic fixtures. Its injected Jobs are deliberately in another field than the synthetic Profile, so they will likely be sorted out from now on.

## Part 1: Judgment in the scoring call

- `SCHEMA` gets `sorted_out` (boolean, required). `check_answer()` rejects an answer where `sorted_out` is true with a tier other than 1.
- Migration `010_sorted_out.sql`: `ALTER TABLE fit_scores ADD COLUMN sorted_out INTEGER NOT NULL DEFAULT 0`. Existing rows stay 0.
- `store()` writes the flag. `ScoreCounts.sorted_out` counts it (kept in `runs.counts_json`).
- `prompts/scoring.md`, a new field description: `sorted_out` is `true` only for tier 1, and only when the Job is in a clearly different occupational field or a role the Profile shows no path to. A conflict with a stated preference alone is tier 1 but not sorted out. In doubt, `false`. Rate the Job and fill in every other field as usual.
- The Digest gets one line "Von der KI aussortiert: n" for the day (★ Jobs are not listed there anyway).
- After the prompt change, run `jobsearch check-injection` and record the result in the plan. The expectations stay as they are; an injected Job that is sorted out still meets "stays at ★".

## Part 2: Hide in the dashboard and the Market report

- Dashboard list: by default, Jobs whose newest Fit score is Sorted out and whose Job status is new are hidden. The tier filter gets the value "aussortiert", which shows exactly those Jobs. The other tier values keep hiding them.
- Job detail page: a note "Von der KI aussortiert" next to the score and its reasoning.
- `market.active_companies()` does not count Sorted out Jobs. The skill table is unaffected, since it only reads ★★ and ★★★.

## Part 3: Evaluation

- All readers listed under "Facts" that count every `fit_scores` row switch to the newest score per Job, so a rescored Job counts once. Exception: `score_tables()` with `--by` (model, Profile, prompt, month) keeps counting every row in its group, so `eval --scores --by prompt` compares the old and the new judgment of the same Jobs. Without `--by` it uses the newest score.
- `jobsearch eval`: a row "davon aussortiert" under ★.
- Weekly report: a metric `stage.sorted_out.<key>` (share of scored Jobs).
- `eval --funnel`: a found-elsewhere opening whose Job is Sorted out gets the stage `sorted_out` ("aussortiert", lever "Prompt, Profil, Feedback notes") instead of `tier1`.

## Part 4: `jobsearch rescore`

A manual command, never part of the Daily run or the scheduler.

- **Selection:** Jobs that are not `ineligible`, have Job status new, and whose newest Fit score is tier 1 with a `prompt_hash` other than the current scoring prompt's (or none).
- **No limit:** one call of the command works through all of them, newest first, in batches of `MAX_SCORED_PER_RUN`, one `claude -p` call per batch with the usual scoring model and prompt. Calls pass `purpose="rescoring"`.
- **On failure:** when a batch fails (after the usual retry), the command stops. Batches already written stay. The console names the error and how many Jobs are left. Calling it again continues, since the done Jobs carry the new prompt hash.
- **What is written:** a new `fit_scores` row per Job; older rows stay. Only the score fields are new (tier, reasoning, skills, `part_time`, `remote` signal, `sorted_out`, `injection_suspected`, Low confidence). `generic` can set `is_generic`, never clear it. There is no match candidate (it is empty once a Job is scored), so nothing is merged. Eligibility, the remote decision and `jobs.remote_type` stay as they are.
- **No Digest noise:** the Digest leaves out Jobs that already had a score before that day.
- **Console summary (German):** bewertet n, davon aussortiert n, Tier verändert n, übrig n, and the Jobs that moved up (★ → ★★/★★★) with title and company, since they may be worth a look now.

## Language

Add to `CONTEXT.md` under Judging:

- **Sorted out** ("aussortiert"): a ★ Job the AI marked as clearly outside the candidate's field. It stays in the database with its Fit score but is hidden by default until the candidate sets a Job status. In code it is `sorted_out`. _Avoid_: Rejected, dismissed, junk.

## Tests

In-memory database with synthetic data, no network, `ai.ask()` faked:

- Schema and check: `sorted_out` with tier 2 or 3 is rejected; with tier 1 it is stored, and `ScoreCounts.sorted_out` counts it.
- The migration on an existing database leaves old rows at 0.
- Dashboard: the default list hides a Sorted out Job with status new, shows it again once a status is set, and the filter "aussortiert" lists it; the detail page shows the note.
- Market report: a Sorted out Job does not count towards an Active company.
- Evaluation: a Job with two score rows counts once in stage, term and funnel metrics and in `status`, twice in `score_tables(by="prompt")`; the "davon aussortiert" row and the `sorted_out` funnel stage.
- Rescore: selects only ★ Jobs with status new, not ineligible and an old prompt hash; works in batches until none is left; keeps the old rows; never touches eligibility, merges or `is_generic = 1`; stops on a failing batch and continues on the next call; a second call with nothing left makes no AI call; rescored Jobs stay out of the Digest.
- Digest: the "aussortiert" line.

## Done when

`check-injection` after the prompt change is recorded in the plan. A live `jobsearch rescore` works through all ★ Jobs with status new, and a second call makes no AI call. The candidate reviews a sample of Sorted out Jobs in the dashboard filter and of the Jobs that moved up, and finds the judgment sound. After the next Daily run the Digest shows the "aussortiert" line and no rescored Job. Findings go into `docs/implementation-plan.md`.
