# Issue: Grounding check for the Career scan extraction

Status: open. To start, create the branch `implement-career-grounding` from `main`; then one commit per part, each after the user's go.

## Goal

The Career scan sends the visible text of changed career pages to the AI (`claude -p`, the extraction model), which returns the Postings it sees (`src/jobsearch/career.py`, prompt `src/jobsearch/prompts/career_extract.md`). The prompt says "Do not invent positions or links", but nothing checks it. An invented title or URL becomes a Posting, then a Job, and can reach the Digest.

This issue adds a deterministic check: every title and every URL the AI returns must appear in the page text that was sent. What does not appear is dropped and counted. This is the classic pattern "check LLM output against its source", and it is the first guardrail of the project. It also limits what a manipulated career page could inject, since the page text is untrusted input.

## How the extraction works today (facts to build on)

- `run_career_scan` collects changed pages as `_Page(company, text, hash)`, with `text` cut to `MAX_PAGE_CHARS`. That cut text is what the AI sees, so the check must use the same cut text.
- `page_text()` turns HTML into visible text. Links are written as `label <absolute url>`, block elements become new lines, and HTML entities are already decoded.
- The AI answers per page with items `{title, location, url, homeoffice}`. `url` is `""` when the page shows no link.
- In the batch loop, `found[i]` holds the items of page `i`. They become Postings through `_posting()`, which cleans tracking parameters from the URL (`_clean_url`). The check must run on the raw item, before `_posting()`.
- After storing, `_record(..., content_hash=page.hash, found=len(postings))` saves the page hash and counts empty results. Two empty scans in a row produce a warning (`REPORT_AFTER`).
- Personio feeds (`FEEDS`) do not go through the AI and need no check.

## Part 1: The check

- New pure function in `career.py`, for example `grounded(item, page_text) -> str | None`, returning the reason an item is not grounded, or `None`.
- **Title:** normalize both sides (collapse all whitespace including new lines to one space, `casefold`), then the title must be a substring of the page text. Titles are often split over inline elements, which is why new lines count as spaces.
- **URL:** if not empty, the raw URL must appear in the page text (it is written there in full by `page_text()`). Compare after stripping a trailing `/`.
- **Not checked:** `location` (the prompt allows joining several places with " / ") and `homeoffice`. Both are possible follow-ups.
- Items that fail are dropped before `_posting()`.

## Part 2: Make drops visible

- `ScanCounts` gets `ungrounded: int`. Since `counts.__dict__` is stored in `runs.counts_json`, the number is kept per run without further work, and `jobsearch eval` can read it later.
- Each drop is logged at INFO with company, reason, title and URL, so a false drop can be inspected in `data/logs/jobsearch.log`.
- The career summary line in `cli.career_scan()` adds "verworfen (nicht im Seitentext): n" when n > 0.
- A page where every item was dropped counts as "found nothing" (`found=0`), so the existing two-scans warning also covers a page the AI keeps misreading.

## Decision to keep in mind

The page hash is stored even when items were dropped, so an unchanged page is not sent to the AI again. A real Posting dropped by mistake therefore stays missing until the page changes. The alternative (not storing the hash after drops) would resend the page every week and cost quota. Keep storing the hash, and rely on the log and the counter to spot false drops. If the live check shows false drops, loosen the normalization rather than the rule.

## Tests (`tests/test_career.py`)

No network, no `claude`; the existing `FakeFetch` and `ExtractAi` fakes are enough.

- `grounded()` table: exact title, title split over lines, different case, title not on the page, URL on the page, URL not on the page, empty URL, URL with and without trailing `/`.
- Scan: an invented title and an invented URL are dropped, counted in `ungrounded`, and create no Job; a grounded item next to them is stored.
- A page whose items are all dropped counts as empty for the two-scans warning.
- **Existing tests need care:** several fake answers use URLs that are not in the fake page (for example `JOB` points to `/jobs/1` while `PAGE` links `/jobs/123?...`, and the tracking-parameter test invents URLs). Change the fixtures so the answers are grounded, and keep what each test was about. All examples stay generic (Muster, Example, Musterstadt).

## Done when

- `jobsearch career-scan` against the live Target companies runs with the check. Ideally a few pages have changed since the last scan; otherwise force a re-extraction on a copy of the database (clear `content_hash` there), never on the real one.
- The number of dropped items is reported, and each logged drop has been looked at: invented by the AI, or a false drop from normalization. Write the finding (rough proportions only, no company names) into `docs/implementation-plan.md`.

## Out of scope

- Checking `location` and `homeoffice` against the page.
- A grounding check for the Fit score (quotes from the job ad as evidence); that is a separate, larger change to the scoring prompt.
