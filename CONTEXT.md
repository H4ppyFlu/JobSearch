# JobSearch

Daily, mostly automated discovery of job openings for one candidate within reach of their home location or fully remote. It collects ads from several sources, merges duplicates into real openings, judges them against the candidate's profile and keeps a lasting record of everything seen.

## Language

### Sources and ads

**Source**:
A place ads come from, such as the Bundesagentur API, the Indeed alert mail or a target company's career page.
_Avoid_: Portal, channel, feed

**Posting**:
One ad as it appears on one Source, identified by that Source's own ID or URL.
_Avoid_: Listing, ad, Stellenanzeige

**Target company**:
A company on the candidate's curated company list whose career page is checked directly.
_Avoid_: Firma, employer

### Openings

**Job**:
The real opening behind one or more Postings. Several Postings can belong to the same Job, and one job title can belong to different Jobs.
_Avoid_: Stelle, position, vacancy

**Generic job**:
A Job without one concrete opening behind it, such as a multi-role ad, an open application call or a recruiter ad for an unnamed client. It is never merged with a specific Job.
_Avoid_: Sammelanzeige, umbrella posting

**Reopening**:
A new Job created when an opening reappears after it was absent from every Source for 21 days or more. It stays linked to the earlier Job.
_Avoid_: Repost, duplicate

### Judging

**Profile**:
The candidate's documented skills, experience and preferences, kept outside this repo and only read here.
_Avoid_: CV, knowhow

**Eligibility**:
A rule-based yes or no on whether a Job is in reach. A Job is eligible when it is within 30 km of the home location, within 70 km for Target companies, or fully remote, and it is not an Obvious mismatch. No judgment involved.
_Avoid_: Filter, fit

**Obvious mismatch**:
A Job whose title alone rules it out, such as an internship or a thesis, based on the candidate's private exclusion list.
_Avoid_: Junk, spam

**Fit score**:
The AI's judgment of how well an eligible Job matches the Profile, in three tiers (★★★, ★★, ★).
_Avoid_: Rating, match, relevance

**Low-confidence score**:
A Fit score based only on a short snippet because no full description was available. It is shown like any other score, never hidden.
_Avoid_: Preliminary score

**Job status**:
The candidate's own progress with a Job, set by hand. It is separate from Eligibility and Fit score. One of new, interesting, not interesting, applied or rejected.
_Avoid_: State, stage

**Not interesting**:
The candidate's own decision that a Job is not worth applying to.
_Avoid_: Dismissed, rejected, declined

**Rejected**:
The company's decision against the candidate after an application.
_Avoid_: Declined, Absage (when the candidate decided)

**Feedback note**:
An optional free-text comment the candidate adds when setting a Job status. Recent notes guide future Fit scores.
_Avoid_: Reason, comment

**Search term**:
A keyword used to query a Source. Each term is either derived from the Profile or added by hand.
_Avoid_: Query, keyword list

### Runs and output

**Daily run**:
The collection of Postings from the Bundesagentur and the alert mail, due at the first logon of each day.
_Avoid_: Sync, crawl

**Career scan**:
The check of all Target company career pages, due once it is 7 or more days since the last one.
_Avoid_: Company search, weekly run

**Digest**:
The summary of newly found Jobs and Reopenings, grouped by Fit score.
_Avoid_: Report, results file
