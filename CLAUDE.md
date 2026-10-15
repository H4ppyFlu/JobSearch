# CLAUDE.md

Daily job discovery pipeline for one candidate (around a home location or fully remote). Python collects and filters, Claude Code (`claude -p`) is used only for judgment.

## Read first

- `CONTEXT.md`: the domain glossary. Use its terms (Posting, Job, Eligibility, Fit score, Reopening, ...) in code, docs and conversation.
- `docs/implementation-plan.md`: build order, verified API facts, and the decisions made per phase. The **Progress** section says where work stands.
- `docs/adr/`: decisions that must not be undone casually.

## Hard constraints

- **No API costs.** AI runs only through `claude -p` on the user's Claude Pro subscription, whose quota is shared with their interactive work. Keep AI calls few and batched, and never add the Anthropic API or an API key.
- **Public repo.** Personal data never goes into git: `.env`, `private/` and `data/` are gitignored. Test fixtures are synthetic. New personal settings go into `.env` or `private/`, with a dummy version in `.env.example` or `private.example/`.
- **The profile is read only.** `PROFILE_PATH` points to a file outside this repo that belongs to another project. Never modify that file or anything next to it.
- **Language.** Code, comments, docs and commit messages in English. Everything the user sees (CLI output, Digest, dashboard, score reasoning) in German.

## Working here

- Install: `python -m venv .venv` then `.venv\Scripts\python -m pip install -e ".[dev]"`
- Tests: `.venv\Scripts\python -m pytest`. Tests never touch the network or call `claude`.
- Live check: `.venv\Scripts\python -m jobsearch daily` then `... status` (set `PYTHONIOENCODING=utf-8` in Git Bash).
- Each phase in the plan ends with a "Done when" check. Run it against live data before calling a phase finished, and write new findings into the plan.
