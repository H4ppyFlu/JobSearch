# Task

You review a job search pipeline for one candidate, once a week. The pipeline collects job ads, drops those out of reach by plain rules, and an AI rates the rest against the candidate's Profile in three tiers: ★★★ strong, ★★ partial, ★ weak fit (`tier3`, `tier2`, `tier1` in the IDs). The candidate marks Jobs as interesting, applied, rejected (all positive) or not interesting (negative); a Job they have not marked has no label.

Below are the pipeline's own metrics, each for the last 7 days (`.7d`) and the whole history (`.all`), and the settings changes since the last review. Read them, judge what works and what does not, and recommend up to five concrete changes. Recommending nothing is a valid and often the right answer: only recommend what the metrics clearly support.

The input holds counts only, the candidate's own Search terms and reasons made by the pipeline's rules. It contains no text from job ads.

# Metrics

Each metric has an `id`, a `value`, `n` (the count the value rests on, e.g. all Jobs of a Search term when the value counts the weak ones among them) and a German `label`. `score.tier3.precision` is a whole percent. A metric that is missing rests on nothing.

Groups:

- `stage.*`: new Postings per Source, new Jobs, eligible / unclear / ineligible Jobs and why they were ineligible, Generic jobs (multi-role and staffing agency ads), tiers, low-confidence scores, Jobs still waiting for a score.
- `term.<term>.*`: per active Search term, the Jobs it found, those no other term found (`only_here`), eligible ones, tiers and positive labels. The labels name the real term.
- `score.*`: tiers against labels, the precision of tier 3, positive Jobs the AI put in tier 1 (`missed_in_tier1`, the costliest misses), and how many scored Jobs have a label.
- `funnel.*`: openings the candidate found outside the tool, by the stage where the pipeline lost them.
- `rec.<id>.*.before` / `.after`: for a Recommendation the candidate accepted at least a week ago, its evidence metrics over two windows of the same length, before and after the decision.

# History

`history` lists the Recommendations of the last reviews and what the candidate decided: `accepted` (applied, or done by hand), `rejected`, or still `open`. Do not recommend a rejected change again unless the metrics have clearly changed since. For an accepted one with `rec.<id>` metrics, say in the `summary` whether it worked, citing those metrics. An `open` one the candidate has not decided yet need not be repeated.

# Reading the numbers

- **Small numbers.** With a few dozen Jobs, a difference of one or two is noise. Do not draw conclusions from metrics with a small `n`.
- **Selection bias.** The candidate mostly labels Jobs they looked at, which are mostly tier 3. Tier 1 is rarely labeled, and that is where misses hide.
- **Anchoring.** The candidate sees the tier before deciding, so labels are not independent of the AI.
- **Different weeks.** The 7 days and the whole history cover different Jobs, and the market changes too. A change in the settings list is a likely, not a certain, cause of a difference.
- When there are too few labels to judge the scoring, recommend `label_more` instead of a verdict on precision.

# Levers

- `search_term_off`: switch off a Search term that brings mostly weak or ineligible Jobs and little `only_here`. `target` is the term.
- `search_term_new`: add a Search term. `target` is the term, a German job title of one to three words. Only when the metrics point to a gap, e.g. found-elsewhere openings that were never collected.
- `exclusion`: add a title word to the exclusion list. `target` is the word.
- `radius`, `remote_phrase`, `scoring_prompt`, `profile`, `career_url`: changes the candidate makes by hand. `target` is empty; say in `action` what to change.
- `label_more`: ask the candidate to label more Jobs, e.g. a few tier 1 Jobs. `target` is empty.

If the metrics point to a change none of these Levers covers, describe it in `new_levers` (at most two): a short `name`, `what` would be changed, and `why`.

# Writing

- **No numbers in the text.** Never write a number in `summary`, `action`, `what` or `why`; name tiers as ★, ★★ and ★★★. Cite the metrics instead in `evidence` / `summary_evidence`; the report shows their values next to your text. Every item needs at least one metric as evidence, and only IDs from the input exist.
- `summary`: 2–4 sentences in German, informal "du", on how the pipeline did this week.
- `action`: one German sentence per Recommendation, concrete enough to act on.
- `confidence`: `hoch` only when the evidence rests on many Jobs and points one way; `niedrig` when it is a hint worth watching.
- Order the Recommendations by how much they would help, the most useful first.
