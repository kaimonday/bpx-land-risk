# Design decisions

The choices that shape this project, why each was made, and what it costs. Each one is a question an interviewer could fairly ask.

## Extraction and review

### Validation can only lower confidence
The LLM reports a value, a confidence and a verbatim evidence quote for every field. Deterministic checks then run, and every one of them can only *cap* confidence, never raise it: evidence must appear in the document, values must be in plausible ranges, a document stating two royalty rates or separate execution and effective dates is capped below the review threshold, and parts of a long lease that disagree go to review.

- **Why:** a model's self-reported confidence is poorly calibrated. Checks that can only push a field toward a person keep the failure mode "too much review" rather than "silently wrong".
- **Cost:** checks tuned on synthetic text misfire on real text. On the 20 real leases the verbatim-evidence check fails quotes the model shortens with "...", which is a large share of the 36% review load. The next fix is to accept elided quotes whose pieces all appear in order.

### Measure the two numbers that matter
`evaluate` reports per-field accuracy, but the decision-relevant numbers are **auto-accept precision** (of the fields that skipped review, how many were right: the risk you take by not looking) and **review load** (the share of fields a person still checks: the cost). The threshold (0.85) trades one against the other.

### Long leases are split, and disagreement goes to a person
Groq's free tier allows 8,000 tokens a minute; a 30-page lease is 15,000. Leases are split on line breaks into overlapping parts, extracted separately and merged: the most confident value wins, except that a later part quoting an amendment ("Notwithstanding ...", an Exhibit A rider) overrides earlier parts, and parts that disagree cap confidence below review and keep every reading so the reviewer sees both.

- **Cost:** a clause that spans a split can be missed. The overlap reduces this; the one real Pugh miss was in a long lease.

## Labels and evaluation

### Labels must be made without seeing the model's answer
The first 260 real labels were made by accepting the model's output and matched it on every field, so accuracy read 100%. Label mode can now hide the model's value, confidence and quote until the reviewer records their own answer.

- **Why:** a label that copies the model measures agreement, not accuracy. Anchoring is strongest exactly where the model is unsure.
- **Consequence:** only the blind-labeled field (Pugh clause) is reported as accuracy in the README.

### An accept records the value accepted
Accepting a field stores the value on screen, not a pointer to "whatever was extracted". Re-extracting a lease can then change the model's output without silently changing an approved label.

### The answer key distinguishes "not stated" from "not scored"
In `labels.csv` a blank means the lease doesn't state the field (the truth is null), and `-` means the field isn't scored (real leases have no tract IDs). Only fully labeled leases are exported, so a half-finished sheet can't skew scores.

## Production and risk

### Gaps are measured against the last complete RRC month
RRC data lags, and the PDQ dump carries rows for months nobody has filed yet (zero volumes everywhere) plus a partly filed newest month. The latest reported month is the last one whose **filed reports** reach 90% of the median of the six months before it.

- **Why filed reports:** a version counting producing leases was tried and rejected. When many leases shut in together (the synthetic demo does this on purpose) producing counts drop and the date moved back, hiding real gaps. A lease that stops producing still files a report, with zeros.
- **Cost:** the 90% threshold is a heuristic. A month where many operators file late could lag the date by a month, which errs toward fewer alerts for one cycle.

### "No production linked" is not "never produced"
For synthetic tracts, a missing RRC link means undeveloped. For real leases it usually means nobody has linked them yet. A dataset can declare production unlinked; then a lease gets the production rules only once it has a confirmed link, and otherwise reads "production not linked" instead of "term ended, never produced".

### An unknown primary term doesn't silence alerts
A lease signed with the date left blank has no primary term end. In SQL that null quietly turned off every production rule, so a lease with no production since 1997 showed no flag. An unknown term now counts as over for the production-gap and shut-in rules: unknown should lean toward a person looking.

## Linking leases to RRC production

### Suggest and confirm, never auto-link
Recorded leases and RRC leases share no key. Candidates in the lease's county are scored, and a person confirms. Only 6 of 20 real leases were linked with confidence; the rest are left "not linked" rather than guessed, because a wrong link produces confident, wrong alerts.

### Score on surnames and the land description
- **Surnames, not words:** RRC names people surname-first ("HINDS, F. C."), leases first-name-first ("Frank Hinds"). Word matching linked "Frank Hinds" to an RRC lease named "FRANK"; comparing surnames fixed a whole class of false positives.
- **The land description is the best signal:** leases on land that already produced name the RRC lease ("The F. C. Hinds oil and gas lease", with initials checked) or cite its number ("Railroad Commission of Texas District 7B no. 18751"). A cited number is matched on the number alone because the dump codes districts differently (7B is 08).

### One lease document per RRC lease
A linked lease gets the whole of its RRC leases' production (real leases rarely state how a pooled unit is split), so two documents can't share an RRC lease: that would count its production twice. Saving such a link is refused. This came up for real: a 2013 top lease over the same land as a 2011 lease that was still producing, where only the 2011 lease is in effect.

### Wells are keyed by RRC lease, well number and API number
One wellbore can report to several RRC leases (an oil and a gas lease, or upper and lower completions), so the API number alone isn't unique in real data. The dump also lists some wells twice with a `000000` placeholder shut-in date; those collapse to one row with the latest real date.

## Data and platform

### Synthetic documents look synthetic; real ones stay local
Generated leases are watermarked on every page and name a fictional lessee, so nothing can be mistaken for a real company record. Real leases name real people, so they live under the gitignored `data/` directory and never enter the repo.

### Free, permitted sources only
County clerk portals charge per page and generally forbid automated download, so the real leases come from SEC EDGAR exhibits (free, scripted access allowed with a named user agent, paced at half SEC's rate limit). Production comes from the RRC's free monthly dump.

### DuckDB and dbt, written to move to Snowflake
DuckDB runs the whole warehouse from one file, so the project needs no server. The SQL is standard plus a few DuckDB functions (`arg_max`, `list_filter`, `date_diff`) with direct Snowflake equivalents.

- **Cost:** DuckDB allows one writer at a time. The API writes through a single process-wide lock, and pipeline commands need the API stopped.

### Determinism is tested
The demo once gave different risk results on each run: an unordered query fed a seeded shuffle, so DuckDB's parallel group-by changed the input order. Queries feeding seeded randomness are now ordered, there is a regression test, and every change in this project was checked against the demo's lease-by-lease results.
