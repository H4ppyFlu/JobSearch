# Script first, AI only for judgment, on the Claude subscription

The project must run without API costs on a Claude Pro subscription, whose usage quota is shared with the candidate's interactive work. So collecting, normalizing, deduplicating by source ID and checking Eligibility are plain Python, and Claude Code runs headless (`claude -p`) only for decisions that need judgment: Fit scores, cross-source Job matching and extracting Postings from career pages that have no feed. Each run scores at most a configurable number of Jobs, the rest wait as unscored, and a failed AI step never blocks collection.

## Considered Options

- An agent doing the whole search every day (an earlier approach). Rejected because it would regularly exhaust the Pro quota and silently skips pages.
- The Claude API. Rejected because of the cost.

## Consequences

The Career scan runs weekly and not daily. Python renders career pages that need JavaScript itself (Playwright), hashes the visible text, and only changed pages go to the AI, which extracts Postings from the text and never browses.
