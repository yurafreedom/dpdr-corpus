# dpdr-corpus

A research corpus on **depersonalization-derealization disorder (DPDR)** and on disturbed time perception in psychopathology. It harvests 30 years of clinical literature, plus trials, grants, preprints, theses, researchers, and how patients describe the condition in their own communities. The questions are what the field knows, who works on it, and how lived experience compares with the clinical description.

`SPEC.md` is the full harvest specification. This repository is the code. Harvested data stays local and is not published here.

## Status

- Literature, trials, grants, preprints and the other non-social sources: running.
- **Reddit Data API module (`harvest/reddit_api.py`): idle, pending API approval.** It does nothing until every `REDDIT_*` variable is set.
- **Historical Reddit posts come from the [Arctic Shift](https://arctic-shift.photon-reddit.com) archive** (`harvest/arctic_shift.py`), not the Reddit API. The API module only adds recent posts and comments and runs the deletion purge.

## Sources

| group | sources |
|---|---|
| literature | PubMed, Europe PMC, OpenAlex (seed, lookup, citation snowball), Crossref, Semantic Scholar, OpenCitations, Unpaywall, CORE, BASE, DOAJ, OpenAIRE, CyberLeninka, retraction notices |
| full text | open-access PDFs and XML found via OpenAlex, Unpaywall, Europe PMC and CORE |
| preprints | bioRxiv/medRxiv, OSF, arXiv |
| trials and grants | ClinicalTrials.gov, ISRCTN, NIH RePORTER, UKRI, CORDIS |
| people | researchers derived from the literature, with OpenAlex and ORCID |
| other | books (Open Library, Google Books, Internet Archive), neuroimaging (OpenNeuro, NeuroVault, NeuroQuery, Neurosynth), theses |
| social | Reddit (Arctic Shift history; Data API for recent items), Bluesky, Mastodon, Wayback snapshots of patient sites, podcasts, YouTube, X (optional, paid) |

## Pipeline stages

`pipeline.py` runs these in order and resumes at the first unfinished module:

1. **seed**: keyword and MeSH searches across the literature databases.
2. **snowball**: references and citations of the seed set (OpenAlex).
3. **enrich**: identifiers, citation counts, open-access status, retractions.
4. **fulltext**: open-access full texts.
5. **preprints**, 6. **trials_grants**, 7. **people**, 8. **other_sources**.
9. **social**: patient communities and social media, including `arctic_shift` and `reddit_api`.
10. **analysis**: scope flags, a relevance classifier and topic clusters over the *literature*, then aggregate exports.

## How to run

```sh
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # optional keys; see below
.venv/bin/python scripts/recon.py           # stage 0: count-only reconnaissance -> exports/recon.md
.venv/bin/python pipeline.py run            # all stages; resumes at the first unfinished module
.venv/bin/python pipeline.py run --all      # rerun everything (cheap: raw/ is a cache) to pick up new records
.venv/bin/python pipeline.py run --module fulltext
.venv/bin/python pipeline.py status
.venv/bin/python -m pytest tests            # Reddit module tests (mocked PRAW, no network)
scripts/daily_reddit.sh                     # daily Reddit fetch + purge, from cron
```

Every response is written to `raw/<source>/` before parsing. A rerun reads that cache instead of the network. Reddit data is the exception: it is pseudonymized before it is cached (see below). Progress checkpoints live in `status/`. A module that needs a missing key is marked `skipped`. A metered API that runs out marks its module `partial`, and the next run continues it.

## Ethics and data handling (Reddit)

The Reddit module is a "script" app on one account, using PRAW over OAuth with User-Agent `script:dpdr-corpus:v1 (by /u/Known-Scale-7565)`.

- **Read-only.** The token requests only the `read` scope. Every HTTP request passes through one gate (`GetOnlyRequestor`) that refuses anything except GET, plus the POST that obtains the token. The module cannot post, comment, vote, message or modify anything. Tests try each of these and check that no request leaves the process.
- **What is collected, once a day:** new posts and comments in r/dpdr and r/Dissociation, and a Reddit-wide search for *depersonalization*, *depersonalisation*, *derealization* and *DPDR*.
- **What is stored:** id, created_utc, subreddit, title/body text, score, and the author as HMAC-SHA256(`REDDIT_HASH_SALT`, author id). Nothing else.
- **No usernames on disk.** The pipeline never writes Reddit usernames to disk. Author fields are replaced by the hash, flair and moderator fields are dropped, and `u/name` mentions in text are redacted, all *before* anything is written to the `raw/` cache or the database. Arctic Shift pages go through the same scrubber. `scripts/scrub_reddit_cache.py` migrates caches created before this version.
- **Deletion purge.** Every daily run re-checks stored ids through `/api/info` (100 ids per request). Items fetched through the API are re-checked every day for their first 30 days. After that, they join the Arctic Shift historical set on a rolling 7-day cycle, a fixed seventh of the ids each day, so every item is re-checked at least weekly. Anything `[deleted]`, `[removed]` or no longer returned is deleted from the database and from the cache. Its id goes on a tombstone list so a cache replay never brings it back.
- **No ML training on Reddit data.** The relevance classifier and the clusters are built on the scientific literature only. Reddit text is not used to train, fine-tune or evaluate any model.
- **Aggregate outputs only.** Anything published from this project reports counts and themes, such as posts per month and subreddit. It never includes individual posts, quotes, or anything that identifies an account. Raw data is not published.
- **Rate limits.** At most one request per second (60/min), under the free tier's 100/min. A daily run (fetch plus purge) totals a few hundred requests. The module is idle unless all of `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD` and `REDDIT_HASH_SALT` are set.

## Layout

| path | what |
|---|---|
| `config.py` | paths, keys from `.env`, the query DSL for spec §1 (verified MeSH IDs), exclusion regexes |
| `harvest/queries.py` | renders the DSL to PubMed, Europe PMC and OpenAlex syntax |
| `harvest/common.py` | raw-first cached HTTP, per-source pacing, backoff, OpenAlex budget guard |
| `db.py` | SQLite schema (spec §14 plus `records`, `epmc_meta`) and dedup-aware upserts |
| `pipeline.py` | stages (spec §15) and the module contract |
| `harvest/<source>.py` | one module per source |
| `harvest/reddit_api.py` | Reddit Data API: daily read-only fetch + deletion purge |
| `harvest/pseudonym.py` | Reddit author hashing and field scrubbing, shared by `reddit_api` and `arctic_shift` |
| `tests/` | mocked-PRAW tests for the Reddit module |
| `harvest/normalize.py` | scope filters and flags, citation edges |
| `harvest/exports.py` | `exports/*.csv`, `reading_list.md`, `stats.md`, `request_from_author.csv`, `spot_check.csv` |

## Scope handling

- `works.query_tags` records which query family found a work: `dpdr` (core union), `dissoc_mesh`, `ketamine_dissoc`, `time`, `ru`, `de`, `snowball_ref`, `snowball_cite`.
- `works.excluded_reason`: `burnout` (spec §1 filter, word-boundary regexes), or `time_ux` for time-only records. Nothing is deleted.
- `works.scope_flags` (kept, for review):
  - `burnout_soft`: occupational vocabulary with no clinical DPDR anchor.
  - `broad_only`: matched only the broad Dissociative-Disorders MeSH or ketamine families, with no depersonalization/derealization wording.
  - `no_term_match`: the server-side search returned a record that never contains the terms (OpenAlex stemming, OpenAIRE description search).
  - `nonclinical_usage`: the third sense of the word ("depersonalized data", "depersonalized bureaucracy"), with no clinical context.
  - `low_relevance`: the embedding classifier (`harvest/relevance.py`, SPECTER v2 + logistic regression, CV AUC ≈ 0.98) scores the work below 0.2.
  - `snowball_offtopic`, `retracted`, `notice`.
- "Core" means not excluded, no flag, and tagged `dpdr` or `time`. The reading list, clusters, full texts and people use core. `exports/spot_check.csv` holds 30 records for the manual check the spec asks for.

## Deviations from the spec, and why

- **OpenAlex is no longer free in practice.** Keyless use is metered at $0.10/day (about 1,000 credits). A search page costs 10 credits, a filter list 1, and a single entity 0. So PubMed and Europe PMC IDs are batch-matched to OpenAlex (100 IDs per credit), search is used only for records outside MEDLINE, and the burnout-heavy complement is fetched last. A free API key raises the allowance.
- **The burnout filter uses word boundaries.** The spec's literal case-insensitive substring `MBI` matches "combined", "ambiguous" and "zombie", and wrongly excluded core DPDR papers in the recon sample. The burnout filter is not applied to time-only records, because "workload" and "nurses" are legitimate in timing research.
- **Europe PMC has no MeSH search field.** An unknown field inside an OR silently becomes a full-text match and inflated counts ~4x. MeSH is PubMed-only.
- **Verified MeSH IDs:** Depersonalization D003861, Dissociative Disorders D004213, Time Perception D013998.
- **Contact email** is empty unless `CONTACT_EMAIL` is set, so Unpaywall is skipped by default. OpenAlex `best_oa_location` is built on Unpaywall data and fills `oa_url` for matched works.
- **OpenCitations** uses the v2 API; v1 now redirects.
- **Server-side search precision is low outside MEDLINE.** OpenAlex and OpenAIRE return many records that use "depersonalized" in a social-science sense or don't contain the term at all. The rule flags plus the relevance classifier take core from about 21k noisy records to about 8k. One mixed cluster ("care, cancer, data", ~200 works) remains; see `exports/clusters.md`.
- **The reading list** requires the topic in the title (or the matching MeSH heading). Otherwise highly cited papers that mention depersonalization in passing (e.g. nursing-methods papers) dominate.
- **Known endpoint changes found while building:** ISRCTN JSON returns 400 (XML is used). CORDIS robots.txt disallows /search and /data (the keyless SPARQL endpoint is used). Bluesky keyless search returns only the first page. scholar.social tag timelines need login. dpdr.org.uk doesn't resolve (Unreal is at unrealcharity.com). dpselfhelp.com's robots.txt disallows AI agents (only the archive.org URL index is kept, no pages). DART-Europe has closed. EThOS has no search API any more. OATD is behind a Cloudflare challenge. CyberLeninka doesn't resolve from this network.
- **Budget-limited work continues daily:** `scripts/daily_openalex.sh` (cron after 00:00 UTC) finishes the OpenAlex seed and snowball, then refreshes people, full texts and exports.
