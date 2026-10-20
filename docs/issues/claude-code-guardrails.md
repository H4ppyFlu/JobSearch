# Issue: Guardrails for working on this repo with Claude Code

Status: in progress on branch `implement-claude-code-guardrails`. Parts 1 and 2 done; personal-data check and merge into `main` pending.

## Goal

Two rules in `CLAUDE.md` are only written down today, and an agent or a tired human can break them:

1. **The Profile is read only.** `PROFILE_PATH` points to a file outside this repo that belongs to another project. Neither that file nor anything next to it may be changed.
2. **Personal data never goes into git.** The repo is public. Values from `.env` and `private/` must not show up in code, tests, fixtures, docs or commit messages.

This issue turns both rules into checks that run on their own: a Claude Code hook for the first, a git pre-commit check for the second. The point is to learn the difference between an instruction the agent should follow and a guardrail that is enforced.

## Part 1: Protect the Profile (Claude Code `PreToolUse` hook)

- A project hook in `.claude/settings.json` (checked in), matching the tools that write files: `Edit`, `Write`, `MultiEdit`, `NotebookEdit`.
- The hook runs `python -m jobsearch.guard profile` with the project's venv. It reads the tool call as JSON from stdin, takes `tool_input.file_path`, resolves it, and blocks (exit code 2, German reason on stderr) when the path is the Profile or lies inside the Profile's folder.
- `PROFILE_PATH` is read from `.env` at run time through `config.load()`, so the checked-in settings hold no personal path. Without `PROFILE_PATH` there is nothing to protect and the hook allows the call.
- **Bash, best effort:** the same hook also matches `Bash` and blocks a command that names the Profile's folder. This is a heuristic (a command can build the path in ways a string check misses) and the docs say so.
- **Fail closed:** if the hook itself crashes on a write tool, it blocks and says why. A bug must not switch the guard off silently.

## Part 2: Keep personal data out of commits (git pre-commit)

- `python -m jobsearch.guard staged` scans the lines a commit adds (`git diff --cached`), file names included, plus the commit message where git allows (`commit-msg` hook).
- Values it looks for, all read at run time:
  - `.env`: `PROFILE_PATH`, `HOME_LOCATION`, `HOME_LAT`, `HOME_LON`, `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`
  - `private/exclusions.txt` and `private/search_terms.txt`, line by line
  - `private/companies.csv`: names, aliases, locations, career URLs
  - derived Search terms from the database
- Matching: case-insensitive, whole words, values of at least four characters. Values that also appear in `private.example/` are generic and skipped. A leading `-` in `search_terms.txt` (switch a term off) is stripped before matching.
- A missing source (no `.env` key, no private file, no database yet) is skipped with a short note, never an error. `read_list_file` raises `ConfigError` for a missing file, so catch it.
- **Allowlist:** `private/allowed_terms.txt` holds terms the user accepts in the repo, for example job titles already used in existing fixtures. It lives in `private/`, since the list itself is personal.
- On a hit the commit is refused with file, line and kind of value. Passwords are shown masked. `git commit --no-verify` stays possible as a conscious override.
- Git does not version hooks. A tracked `.githooks/pre-commit` (and `commit-msg`) calls the module, and `git config core.hooksPath .githooks` switches it on. `jobsearch init` sets this, and the README names it.
- This covers commits by Claude Code and by hand alike, so no separate Claude hook is needed for commits.

## Implementation notes

- New module `src/jobsearch/guard.py` with pure functions (path check, value collection, scan) and a small `__main__` entry for the two hook commands. It reuses `config.load()`, `read_list_file`, `load_target_companies` and the derived terms from `search_terms.py`.
- New files: `.claude/settings.json`, `.githooks/pre-commit`, `.githooks/commit-msg`, `private.example/allowed_terms.txt` (empty with a comment).
- Windows: the hook command must work from Git Bash and PowerShell. Use `$CLAUDE_PROJECT_DIR` and the venv's `python`. Check the exact form against the current Claude Code hook docs while implementing.

## Tests

Synthetic values only, no network, no `claude`, no real git commit:

- Path check: the Profile, a file next to it, a file in a subfolder, a path with `..`, a different case on Windows, a file in the repo (allowed), no `PROFILE_PATH` (allowed).
- Hook entry: JSON on stdin gives exit 0 or 2, a broken input on a write tool blocks.
- Bash heuristic: a command naming the Profile folder is blocked, others pass.
- Scan: a hit per source, the whole-word rule, the minimum length, the skip for values from `private.example/`, the allowlist, masking of the password.

## Done when

- In a live Claude Code session, an `Edit` on a copy of the Profile's folder path is blocked with the German reason, and an edit in the repo goes through. The real Profile stays untouched; the check uses a temporary `PROFILE_PATH`.
- A staged test file with a synthetic value placed in a temporary `private/` copy makes `git commit` fail, and the same commit without it succeeds.
- Findings go into `docs/implementation-plan.md`.

## Out of scope

- Blocking Claude from reading `.env` or `private/`. Reading is needed for normal work here.
- Cleaning terms that are already on `main`. The user accepted them; the allowlist keeps them from blocking new commits.
