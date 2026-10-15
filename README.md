# JobSearch

Daily, mostly automated job discovery for one candidate. It collects postings from the Bundesagentur für Arbeit (and later Indeed alert mails and company career pages), merges duplicates into real openings, decides with plain rules whether a job is in reach, and keeps everything in a local SQLite database. AI scoring runs through Claude Code on a subscription, with no API costs.

The vocabulary is defined in [CONTEXT.md](CONTEXT.md), the main decisions are in [docs/adr](docs/adr/), and the build order is in [docs/implementation-plan.md](docs/implementation-plan.md).

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m jobsearch init
```

`init` creates `data/`, the database, `.env` and the `private/` files from the examples. Then fill in:

- `.env`: home location and radii, and later the profile path and mail credentials
- `private/companies.csv`: Target companies, with aliases for the names they use in job ads
- `private/exclusions.txt`: title words that rule a job out (internships, theses, ...)
- `private/search_terms.txt`: Search terms

`.env`, `private/` and `data/` are gitignored and never leave your machine.

## Usage

```
.venv\Scripts\python -m jobsearch daily    # collect, check Eligibility, fetch descriptions
.venv\Scripts\python -m jobsearch status   # counts and the last runs
.venv\Scripts\python -m pytest             # tests, never touch the network
```

## How Eligibility works

- Distances are straight-line from home. Within `RADIUS_KM` is in reach, and within `RADIUS_TARGET_COMPANY_KM` for Target companies.
- A title containing a word from the exclusion list is out.
- Jobs outside the radius are only kept when the posting allows home office *and* the description contains a full-remote phrase ("100% remote", "ortsunabhängig", ...). Those stay `unclear` until the AI scoring confirms them.

Every decision is stored with its reason, including the ineligible ones.
