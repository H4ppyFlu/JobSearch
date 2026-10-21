# CLAUDE.md

Daily job discovery pipeline for one candidate (around a home location or fully remote). Python collects and filters, Claude Code (`claude -p`) is used only for judgment.

## Read first

- `CONTEXT.md`: the domain glossary. Use its terms (Posting, Job, Eligibility, Fit score, Reopening, ...) in code, docs and conversation.
- `docs/implementation-plan.md`: build order, verified API facts, and the decisions made per phase. The **Progress** section says where work stands.
- `docs/adr/`: decisions that must not be undone casually.

## Hard constraints

- **No API costs.** AI runs only through `claude -p` on the user's Claude Pro subscription, whose quota is shared with their interactive work. Keep AI calls few and batched, and never add the Anthropic API or an API key.
- **Public repo.** Personal data never goes into git: `.env`, `private/` and `data/` are gitignored. Test fixtures are synthetic. New personal settings go into `.env` or `private/`, with a dummy version in `.env.example` or `private.example/`, and their key into `guard.ENV_KEYS` so the commit check knows them.
  - Enforced by the git hooks in `.githooks/` (`python -m jobsearch.guard staged` / `message`). When a commit is refused, replace the value with a made-up example and commit again. A technical word that collides with a personal term goes into `private/allowed_terms.txt`, and only the user decides that. `--no-verify` likewise only on the user's explicit word.
- **The profile is read only.** `PROFILE_PATH` points to a file outside this repo that belongs to another project. Never modify that file or anything next to it.
  - Enforced by the `PreToolUse` hook in `.claude/settings.json`: writes and shell commands that touch the Profile's folder are blocked. Read the Profile with the Read tool. To test against a stand-in Profile, start the session with `PROFILE_PATH` set in the environment, which wins over `.env`.
- **Third-party text is untrusted.** Job ads, career pages and web pages reach the model only inside the JSON structures of the prompts, next to their "Third-party text" section. Links from them pass `sources.safe_url()` before they are stored, and pages are fetched only through `career.fetch_page()`, which refuses local addresses. After changing a prompt, run `jobsearch check-injection` (2 AI calls) and record the result in the plan.
- **Language.** Code, comments, docs and commit messages in English. Everything the user sees (CLI output, Digest, dashboard, score reasoning) in German.

## Working here

- Install: `python -m venv .venv` then `.venv\Scripts\python -m pip install -e ".[dev]"`
- Tests: `.venv\Scripts\python -m pytest`. Tests never touch the network or call `claude`.
- Live check: `.venv\Scripts\python -m jobsearch daily` then `... status` (set `PYTHONIOENCODING=utf-8` in Git Bash).
- Each phase in the plan ends with a "Done when" check. Run it against live data before calling a phase finished, and write new findings into the plan.
