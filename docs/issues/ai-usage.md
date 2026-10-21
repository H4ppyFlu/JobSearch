# Issue: Track AI usage per call

Status: done (2026-10-21), merged into `main`.

## Goal

The AI runs on a subscription whose quota is shared with the candidate's interactive work, and the whole design is built around using little of it (batching, caps, Haiku for mechanical work). So far nothing measures how much it actually uses. This issue records the AI usage of every `claude -p` call, shows it in `status`, the Digest and `jobsearch eval --usage`, and derives metrics such as the API equivalent per scored Job.

Two limits belong in the help text and the docs:

- **Not a share of the quota.** The subscription limit is not published in tokens, it depends on a time window, and the candidate's interactive use counts against it too, which this tool never sees. The numbers show how the pipeline's own usage develops, not how much quota is left.
- **Not a real cost.** `costUSD` is the list price the same calls would cost through the API. Nothing is billed. It is still the best single number, since it weights the token kinds correctly per model.

## Facts to build on

- `ai.ask()` in `src/jobsearch/ai.py` is the only place that runs `claude -p`, with `--output-format json`. Each attempt is one process; an invalid answer is asked for once more. `_parse()` reads the JSON envelope but keeps only the model name from `modelUsage` and drops the rest.
- Checked live with one small Haiku call. The envelope carries:
  - `usage`: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `server_tool_use.web_search_requests`, among others;
  - `modelUsage.<model>`: `inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`, `thinkingTokens`, `webSearchRequests`, `costUSD`, `costBasis` (`"list"`);
  - `total_cost_usd`, `duration_ms`, `num_turns` (2 for a structured answer, since the answer is a tool turn), `is_error`, `subtype`.
- Even a one-word answer wrote more than a thousand tokens to the cache: the system prompt and schema are a fixed overhead per call. That is why batching pays off, and it is worth measuring.
- Callers take `ask` as an injectable parameter: `scoring.run_scoring`, `career.run_career_scan`, `career_setup.run_setup`, `search_terms.refresh_derived`, `injection.run`. Tests pass a fake.
- The last migration is `006_change_markers.sql`; this issue takes `007`.

## Part 1: Record

### In `ai.py`

- New frozen dataclass `CallUsage`: `purpose`, `model`, `attempt`, `outcome`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `thinking_tokens`, `web_search_requests`, `cost_usd`, `duration_ms`, `num_turns`, `started_at`. Token and cost fields are `None` when the envelope did not arrive.
- `ask()` gets a required keyword `purpose`, one of a fixed set in `ai.PURPOSES`: `scoring`, `career_extract`, `career_setup`, `search_terms`, `check_injection`, `weekly_report`, `skill_groups`. An unknown purpose raises `ValueError`, so a typo shows in tests.
- `ask()` gets an optional keyword `usage_recorder: Callable[[CallUsage], None] | None`. It is called once per attempt, whatever the outcome:
  - `ok`: the answer passed the schema and the caller's check;
  - `invalid`: the caller's check or the missing structured answer rejected it (the retry gets its own row);
  - `error`: the CLI reported an error (`is_error`, non-zero exit, unparsable output). Usage is read from the envelope when there is one;
  - `timeout`: no envelope, all usage fields `None`.
- Usage comes from `modelUsage` (summed if more than one model answered), falling back to `usage` and `total_cost_usd`. A recorder that raises is logged at WARNING and never fails the call.
- Prompt and answer text are not recorded: they hold the Profile and Third-party text.

### Storage

- Migration `007_ai_calls.sql`: table `ai_calls` with the fields of `CallUsage` plus `id` and `run_id` (nullable, → `runs.id`).
- `db.usage_recorder(conn, run_id) -> Callable[[CallUsage], None]` writes one row per call and commits it right away, so the rows survive a crash later in the run.

### Wiring

- The recorder is built where the connection and the run id already are: `run_scoring` and `run_career_scan` start their own run (`db.start_run`) and pass `usage_recorder=db.usage_recorder(conn, run_id)`; `refresh_derived` runs before the Daily run starts and passes it without a run id. `career-setup` and `check-injection` have no connection of their own, so `cli.py` passes `functools.partial(ai.ask, usage_recorder=...)` as their `ask`, without a run id. (Changed during implementation: the CLI never knows the run id, since the modules start their runs themselves.)
- Without a recorder nothing is stored, so existing tests keep working. New tests use a list as recorder.
- No usage exists for calls before this change; the count starts with it.

## Part 2: Show

- `jobsearch status`: one line for the last 7 days: calls, tokens by kind, API equivalent.
- The Digest: one line "KI-Verbrauch heute: n Aufrufe, API-Gegenwert y $ (Tokens: Eingabe …, Ausgabe …, Cache gelesen …, Cache geschrieben …)" over all calls of the day. (Changed during implementation: tokens broken down by kind, as the rule below asks, not one sum.)
- `jobsearch eval --usage`, with `--since`, `--until` and `--split` like the other eval views:
  - a table per day, per purpose and per model: calls, input, output, cache read, cache write, API equivalent;
  - API equivalent per scored Job, per ★★★/★★ Job, and per career page sent to the AI;
  - share of retries (`attempt = 2`) per purpose;
  - fixed overhead: average cache tokens per call, per purpose.
- The API equivalent is the headline number, labelled "API-Gegenwert (Listenpreis)", in dollars without conversion. Tokens are shown next to it, broken down by kind, never as one sum. The help text states both limits from the Goal.

## Changes to other issues

- `docs/issues/weekly-report.md` and `docs/issues/market-report.md`: their `ai.ask()` calls pass `purpose="weekly_report"` and `purpose="skill_groups"`. Their migrations become `008` and `009` (in the order they are implemented).

## Language

Add to `CONTEXT.md`:

- **AI usage** ("KI-Verbrauch"): the measured tokens, duration and API equivalent of every `claude -p` call, recorded per purpose.
- **API equivalent** ("API-Gegenwert"): the list price the same calls would cost through the API. Neither a real cost nor a share of the subscription quota.

## Tests

No network, no `claude`; `runner` in `ai.ask()` is faked with canned envelopes:

- A successful call records one `ok` row with all fields read from the envelope.
- An answer rejected by the check records `invalid` for attempt 1 and the outcome of attempt 2.
- An error envelope records `error` with its usage; a timeout records `timeout` with empty usage; a quota error still records before `AiError` is raised.
- A recorder that raises does not fail the call.
- An unknown `purpose` raises.
- `db.usage_recorder` writes rows with the run id; the migration runs on an existing database.
- `eval --usage`: tables, per-Job metrics, retry share and `--split` on synthetic rows; `status` and the Digest line.

## Done when

A live Daily run writes `ai_calls` rows whose values match the envelope of the same call (`ai.ask()` logs the envelope's usage at INFO for every call, so the log can be compared with the row), and `eval --usage` shows plausible numbers. Findings go into `docs/implementation-plan.md`.

## Out of scope (follow-ups)

- A warning threshold in the Digest, once a few weeks of data show what a normal day looks like.
- Usage of the candidate's interactive Claude Code sessions.
