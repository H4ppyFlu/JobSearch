# Issue: Market report (skill groups and active companies)

Status: in progress on branch `implement-market-report`. Parts 1 to 4 done.

## Goal

The pipeline judges each Job on its own. Over weeks, those judgments also say something about the market: which skills the Jobs worth applying to keep asking for that the Profile does not show, which strengths are in demand, and which companies keep hiring. This issue adds a Market report that answers both, mostly with plain Python and at most a few cheap AI calls to group skill texts.

It is a command of its own, `jobsearch market`, and a dashboard page "Markt". It does not run automatically and writes no file.

## Facts to build on

- `fit_scores.missing_skills` and `fit_scores.matching_skills` are JSON lists of up to six short free-text items per score (`prompts/scoring.md`), stored by `scoring.store()`. A Job can have several score rows; the newest (`scored_at`) counts.
- The items are free text written by the scoring model after reading the ad. On the live data almost every item is unique, even after casefolding, so counting them as they are says nothing. They need grouping: "Staplerschein" and "Gabelstaplerführerschein" are the same skill.
- `companies.normalize_company(name)` gives comparable company tokens; `jobs.is_target_company`, `jobs.is_generic`, `jobs.eligibility` and `jobs.reopening_of` are on the Job. The dashboard's `reopening_chain()` follows `reopening_of`.
- `evaluation.label(status)` maps a Job status to positive, negative or no label. `evaluation.Period` builds date conditions for `--since` / `--until`.
- `ai.ask()` is the only place that runs `claude -p`; tests replace it with a fake. `MODEL_EXTRACTION` (Haiku) is the model for mechanical work.

## Part 1: Active companies (no AI)

- New module `src/jobsearch/market.py`. A pure function `active_companies(conn, today) -> list[CompanySignal]`.
- It counts distinct Jobs per `normalize_company` key over the last 90 days (`first_seen`), only Jobs in reach (`eligible`) and never Generic jobs, since staffing agencies would otherwise top the list.
- A company is listed when it has 3 or more such Jobs, or at least one Reopening in the period.
- Per company: display name (the most frequent spelling), number of Jobs, best Fit score tier, number of Reopenings, and whether it is a Target company. A company that is not a Target company gets the note "Kandidat für companies.csv". Nothing is written to `private/companies.csv`.
- New command `jobsearch market` prints this table in German.

## Part 2: Skill groups (one table, a few AI calls)

### Storage

- Migration `009` (or `008` if this issue is implemented before `weekly-report`; `ai-usage` takes `007`): `skill_groups(text TEXT PRIMARY KEY, group_name TEXT NOT NULL, grouped_at, model)`. `text` is the skill item as stored, whitespace collapsed. One row per distinct text, so a text is grouped once and keeps its group.

### Grouping

- `jobsearch market` first collects all distinct skill texts (missing and matching) from the newest Fit score of every ★★ or ★★★ Job that have no row in `skill_groups`.
- It sends at most `MAX_SKILLS_PER_CALL` (400) of them per call to `MODEL_EXTRACTION`, together with the list of existing group names, and makes at most 3 calls per run. What is left waits for the next run.
- New prompt `src/jobsearch/prompts/skill_groups.md`. The model assigns each text to an existing group or creates a new one. Group names are German, with technical terms kept in their original form. A group is something one can learn or show in a CV: a tool, a method, a language, a field. Groups as broad as "IT-Kenntnisse" are not allowed.
- **Third-party text.** The skill texts were written by the model from job ads, so an ad can steer them. They reach the model only inside the prompt's JSON, and the prompt gets the usual "Third-party text" section. `check-injection` is not extended: the worst outcome is a wrong group, and the code checks the mapping.
- Schema: a list of `{index, group}`. Each text gets an index in the prompt and the answer refers to it, not to the text (as in the career setup), so the model cannot tidy up a text.
- **Check in code:** every index sent is answered exactly once and no other index appears; group names are non-empty and at most 60 characters. A failing answer gets one retry through `ai.ask()`'s usual path, then the texts stay ungrouped. Calls pass `purpose="skill_groups"` (see `ai-usage.md`).
- `jobsearch market --regroup` deletes all rows and groups everything again, with the same limits per run.

### When the AI fails

The report is still printed. Active companies and already grouped skills come from Python; new texts are counted as "ohne Gruppe", and the console names the error in German. The dashboard page never calls the AI.

## Part 3: Skill table in the console

- A pure function `skill_table(conn, period, today) -> list[SkillRow]` over the newest Fit score of every ★★ and ★★★ Job. Within one Job each group counts once per side.
- One row per group with the columns: fehlt (last 90 days / total), vorhanden (last 90 days / total), davon positiv (Jobs with a positive label), davon Low confidence. "Total" means the chosen period: everything by default, or `--since` / `--until` as in `jobsearch eval`.
- Sorted by "fehlt, total". The console shows the top 20 groups and "ohne Gruppe: n".
- A group can appear on both sides. That is not an error: the scoring model judged different ads differently.
- There is no "rising" marker, neither from a threshold nor from the AI. With small numbers it would be noise; the two time columns let the reader see a trend.

## Part 4: Dashboard page "Markt"

- A page "Markt" with the active companies and the full skill table, computed live from the database, without AI.
- A detail view per group lists its original texts, so wrong groupings are easy to spot. They are fixed with `--regroup`; there is no manual editing.
- A note under the skill table tells how many texts are still ungrouped and that `jobsearch market` groups them.

## Language

Add to `CONTEXT.md`:

- **Market report** ("Marktbericht"): the view of skill groups and active companies across all scored Jobs, from `jobsearch market` and the dashboard page "Markt".
- **Skill group** ("Skill-Gruppe"): a lasting group that skill texts from Fit scores belong to, so that differently worded skills are counted together.
- **Active company** ("aktive Firma"): a company with 3 or more Jobs in reach within 90 days, or a Reopening, not counting Generic jobs.

## Tests

In-memory database with synthetic data, no network, `ai.ask()` faked:

- Active companies: the threshold, the 90-day window, Generic jobs and ineligible Jobs left out, Reopenings counted, the Target company note.
- Grouping: only ungrouped texts from ★★/★★★ Jobs are sent, the per-call and per-run limits, a missing or extra index rejects the answer, a run with nothing new makes no call, `--regroup` sends everything again.
- Skill table: newest score only, one count per group and Job, the positive and Low-confidence columns, `--since` / `--until`, ungrouped texts counted.
- A failing fake AI still gives the report with "ohne Gruppe".
- Dashboard: the page and a group's detail view render, and no AI call happens.
- The migration on an existing database.

## Done when

A live `jobsearch market` groups all skill texts, a second run without new Fit scores makes no AI call, and the candidate finds the top groups and the company list plausible. Findings go into `docs/implementation-plan.md`.
