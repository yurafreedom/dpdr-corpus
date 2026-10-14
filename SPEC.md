# DPDR + Time Perception: 30-Year Corpus Harvest Specification

Specification for Claude Code. Goal: build the most complete *legal* corpus on clinical depersonalization-derealization (DPDR) and on disturbed/restored time perception in psychopathology, from 1990 onward (plus pre-1990 foundational work), including raw API responses, open-access full texts, clinical trials, grants, authors with affiliations, preprints, theses, patient communities and social media. Deliverables: `corpus.db` (SQLite), `raw/` and `fulltext/` directories, CSV exports and a reading list.

## 0. Principles

1. Raw context first: every API response is written verbatim to `raw/<source>/<id>.json` before any processing. Normalization is a separate step on top of `raw/`.
2. Legal sources only: open APIs, OA copies, public posts. Do not scrape Google Scholar (no API, aggressive blocking), do not use pirate aggregators, do not download through someone else's institutional proxy, respect robots.txt and rate limits.
3. Incremental and idempotent: one source = one module `harvest/<source>.py`, responses cached on disk, re-runs never duplicate and resume from the last checkpoint (`status/<source>.json`).
4. API politeness: `mailto`/User-Agent with a contact address, free API keys where available, exponential backoff on 429/5xx.
5. Ethics for social data: store user IDs and public text only, do not mass-collect user profiles, never aggregate patients' personal stories with identifying data.

## 1. Scope definition and queries

The term `depersonalization` has three meanings; only the clinical one is in scope (DSM-5-TR 300.6, ICD-11 6B66, ICD-10 F48.1, DSM-III/IV "depersonalization disorder", DSM-II "depersonalization neurosis", ICD-9 300.6). Exclude Maslach-style burnout (MBI, cynicism, nurses, teachers, clinicians, workplace) and sociological "depersonalization" of environments/institutions.

Exclusion filter at normalization (title + abstract + keywords, case-insensitive): `burnout`, `Maslach`, `MBI`, `cynicism`, `emotional exhaustion`, `job satisfaction`, `workload`, `nurs`, `teacher`, `customer`, `dehumaniz`. Mark matches as `excluded_reason='burnout'`; do not delete.

### 1.1 DPDR queries

Both spellings everywhere: `depersonali(z|s)ation`, `dereali(z|s)ation`.

MeSH (PubMed): `"Depersonalization"[MeSH]`, `"Dissociative Disorders"[MeSH]` (with post-filtering), `"Time Perception"[MeSH]`. Verify descriptor IDs in the MeSH Browser before use.

Free text (combine): `"depersonalization-derealization disorder"`, `"depersonalisation disorder"`, `"depersonalization disorder"`, `DPDR`, `"chronic depersonalization"`, `"feelings of unreality"`, `"dissociative detachment"`, `"Cambridge Depersonalisation Scale"`, `"corticolimbic disconnection"`, `interoception AND depersonalization`, `insula AND depersonalization`, `"predictive processing" AND depersonalization`, `"minimal self" AND depersonalisation`, `ketamine AND dissociation`, `"cannabis-induced depersonalization"`, `lamotrigine AND depersonalization`, `naltrexone AND depersonalization`, `rTMS AND depersonalization`, `tDCS AND depersonalization`, `CBT AND depersonalisation`, `depersonalization AND COVID`, `depersonalization AND adolescents`, `autonomic AND depersonalization`, `HPA AND depersonalization`.

### 1.2 Time-perception queries

`"time perception" AND (depersonalization OR dissociation OR depression OR anxiety OR PTSD OR schizophrenia OR psychedelic OR ketamine OR "long COVID")`, `"temporal experience" AND (dissociation OR psychopathology)`, `"subjective time" AND psychopathology`, `"temporal disintegration"`, `"lived time"`, `"passage of time judgment"`, `"duration estimation" AND (depression OR anxiety)`, `interoception AND "time perception"`, `"temporal binding" AND "sense of agency"`, `"time perception" AND (intervention OR training OR rehabilitation OR therapy)`, `"sensorimotor synchronization" AND (therapy OR clinical)`, `"interval timing" AND dopamine AND (clinical OR patients)`.

Exclude ergonomics/marketing/UX: `queue`, `waiting time`, `consumer`, `user interface`, `latency`.

### 1.3 Russian- and German-language literature

A distinct school: Yu. L. Nuller and the Leningrad/St. Petersburg tradition (depersonalization, clonazepam, naloxone, 1970s–2000s). Terms: `деперсонализация`, `дереализация`, `синдром деперсонализации-дереализации`, `психическая анестезия`, `anaesthesia psychica dolorosa`. German: `Depersonalisation`, `Derealisation`, `Depersonalisationsstörung`, `Entfremdungserleben`.

## 2. Scholarly literature: metadata and abstracts

| Source | Provides | Access | Notes |
|---|---|---|---|
| OpenAlex | Corpus backbone: works back to the 1980s and earlier, abstracts (inverted index), authors, affiliations by year, citations, OA status, preprints | `https://api.openalex.org/works?search=...&filter=publication_year:1980-2026&mailto=you@x` | No key. Polite pool via `mailto`. Primary source; everything else is enrichment |
| PubMed E-utilities | MeSH precision, PMIDs, dates, publication types | `esearch.fcgi` → `efetch.fcgi?db=pubmed&rettype=abstract&retmode=xml` | Free NCBI API key raises the limit to 10 req/s |
| Europe PMC REST | PubMed + preprints + OA full-text XML, citations | `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=...&format=json&resultType=core` and `/fullTextXML` | No key. Best single entry point for biomedicine |
| Semantic Scholar Graph API | citations/references, tldr, SPECTER embeddings, bulk search | `https://api.semanticscholar.org/graph/v1/paper/search/bulk?query=...` | Free key on request raises limits; bulk datasets available |
| Crossref REST | DOI metadata, funders, licenses, retraction updates | `https://api.crossref.org/works?query=...&mailto=` | No key |
| OpenCitations | Open citation links by DOI | `https://opencitations.net/index/api/v1/citations/{doi}` | No key |
| CORE API v3 | Aggregated university repositories, green-OA full texts | `https://api.core.ac.uk/v3/search/works?q=...` | Free key on registration |
| BASE (Bielefeld) | OAI-PMH aggregator, 300M+ records | API on free registration | Grey literature and non-English repositories |
| OpenAIRE Graph API | EU-funded outputs linked to projects | `https://api.openaire.eu/search/publications?keywords=` | No key |
| DOAJ API | OA journals and articles | `https://doaj.org/api/search/articles/...` | No key |
| CyberLeninka | Russian-language articles, free PDFs | Site search, PDF links | No API; careful WebFetch over search results |
| eLIBRARY.RU | RSCI | No free API | Manual review of key authors only (Nuller, Tochilov et al.) |

Do not use: Google Scholar (no API, scraping prohibited and blocked), Scopus / Web of Science / PsycINFO (paid APIs). Citation counts come from OpenAlex and Semantic Scholar.

## 3. Full texts, legally

1. Europe PMC `fullTextXML` for everything with `isOpenAccess=Y` or `inEPMC=Y`.
2. PMC OA subset: OA Web Service (`https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMCxxxx`) or bulk packages via FTP/S3.
3. Unpaywall for every DOI: `https://api.unpaywall.org/v2/{doi}?email=` → `best_oa_location.url_for_pdf`. This is the main route to a legal publisher or repository copy.
4. CORE PDFs for matched works.
5. Preprint servers (section 4) as substitutes for paywalled versions.
6. Expected coverage: 40–60% of post-2015 works, markedly less for 1990–2010. For uncovered key works, build `exports/request_from_author.csv` (authors may legally share manuscripts on request).

Store `fulltext/<work_id>.(xml|pdf|txt)` and the extracted text path in `works.fulltext_path`.

## 4. Preprints

OpenAlex already indexes bioRxiv, medRxiv, PsyArXiv, SSRN, arXiv and Research Square, so a separate pass is only needed for full texts and for very recent (last weeks) records:
- bioRxiv/medRxiv: `https://api.biorxiv.org/details/{biorxiv|medrxiv}/{from}/{to}/{cursor}` — walk by date, filter locally.
- PsyArXiv via OSF API v2: `https://api.osf.io/v2/preprints/?filter[provider]=psyarxiv`; no full-text search, filter against the OpenAlex list.
- OSF Registrations (preregistrations): `https://api.osf.io/v2/registrations/`.
- arXiv API for time-perception models (q-bio.NC, cs.AI): `http://export.arxiv.org/api/query?search_query=all:"time perception"`.

## 5. Clinical trial registries

| Registry | Access |
|---|---|
| ClinicalTrials.gov v2 | `https://clinicaltrials.gov/api/v2/studies?query.term=depersonalization&pageSize=1000` (JSON, no key) |
| ISRCTN | `https://www.isrctn.com/api/query/format/json?q=depersonalisation` |
| WHO ICTRP | Aggregates all registries; portal export, limited API — use as a completeness check |
| EU CTR / CTIS | Public portals, HTML/CSV export |
| DRKS (Germany) | Site search and export |
| ANZCTR, jRCT, ChiCTR | Via ICTRP |

Queries: `depersonalization`, `depersonalisation`, `derealization`, `dissociative`, `"time perception"`, `"temporal processing"`, `interoception`. Fields: id, title, conditions, interventions, phase, status, sponsor, PI, sites, dates, enrollment, has_results, links.

## 6. Grants: who is active right now

Grants reveal active groups before their publications appear.
- NIH RePORTER API v2: `POST https://api.reporter.nih.gov/v2/projects/search` with `criteria.advanced_text_search.search_text`.
- UKRI Gateway to Research: `https://gtr.ukri.org/gtr/api/projects?q=depersonalisation` (JSON via Accept header).
- CORDIS (EU): open project datasets, keyword search.
- DFG GEPRIS: web search only, careful WebFetch.
- Wellcome: open grants dataset (CSV).
- ERC: project listings on the site.

Queries: `depersonalization`, `derealization`, `dissociat*`, `"time perception"`, `"temporal processing"`, `interoception`.

## 7. People and institutions

Goal: a list of active authors 2022–2026 with verified current affiliations.
- OpenAlex `/authors`: `works_count`, `counts_by_year`, `last_known_institutions`, year-by-year `affiliations` history — this is the mechanism for verifying "where they are now".
- ORCID public API: `https://pub.orcid.org/v3.0/search/?q=family-name:Sierra+AND+given-names:Mauricio`, then `/v3.0/{orcid}/employments`.
- Semantic Scholar `/author/{id}`.
- Institutional portals for spot checks: King's College London Research Portal (Pure), Mainz Gutenberg Open Science, Mount Sinai, Sussex (Seth/Critchley).
- Lab websites, Google Scholar profile pages (view single pages only, no list scraping), LinkedIn (manual).

Seed list: Mauricio Sierra, Anthony David, Elaine Hunter, Nick Medford, Daphne Simeon, Antonio Mantovani, Matthias Michal, Fugen Neziroglu, Anna Ciaunica, Anil Seth, Hugo Critchley, Philip Gerrans, Erwin Lemche, Orna Guralnik, Marc Wittmann, Sylvie Droit-Volet, Georg Northoff, Thomas Fuchs, Kai Vogeley, Giovanni Stanghellini, Virginie van Wassenhove, Devin Terhune, Ruth Ogden, Dan Zakay, Jennifer Coull, Hugo Merchant. Expand via co-authorship and citations.

Output `exports/people.csv`: name, openalex_id, orcid, s2_id, works_total, works_2022_2026, current institution (source, verification date), country, top-5 works, co-authors, channels (website, scholar, bluesky, x, youtube), "clinician / sees patients" flag (manual check).

## 8. University repositories and theses

There is no separate "Harvard API" or "Stanford API", and none is needed: every university repository (DSpace, EPrints, Pure, Fedora) exposes standard OAI-PMH, and CORE, BASE and OpenAIRE have already aggregated them. Direct access, if a targeted pull is needed:
- Harvard DASH: `https://dash.harvard.edu/oai/request?verb=ListRecords&metadataPrefix=oai_dc`
- Stanford Digital Repository / SearchWorks, DSpace@MIT, Apollo (Cambridge), ORA (Oxford), UCL Discovery, King's Pure (`kclpure.kcl.ac.uk`), Edinburgh Research Explorer, Spiral (Imperial), eScholarship (UC), Gutenberg Open Science (Mainz), HAL (France), Zenodo, Figshare.
For DPDR the repositories that actually matter are King's (Sierra, David, Medford, Hunter, Lemche), Mainz (Michal), Mount Sinai (Simeon), Sussex (Seth, Critchley), Lisbon/UCL (Ciaunica), Freiburg IGPP (Wittmann), Clermont-Ferrand (Droit-Volet), Ottawa (Northoff), Heidelberg (Fuchs).

Theses: OATD.org (aggregator), DART-Europe, NDLTD, ProQuest PQDT Open (free portion), DissOnline/DNB (Germany), EThOS (verify current availability after the 2023 incident), theses.fr (France).

## 9. Patient communities, forums, social media

| Platform | How to collect |
|---|---|
| Reddit: r/dpdr, r/Dissociation, r/DPDRecoveryStories, r/dpdrecovery (verify names) | Recent: official API (OAuth, free non-commercial tier, PRAW). History: Arctic Shift (API and dumps; verify current endpoint). Store raw JSON; exports carry author IDs only |
| dpselfhelp.com | Oldest DPDR forum. Check robots.txt/ToS; history via Wayback CDX API (`http://web.archive.org/cdx/search/cdx?url=dpselfhelp.com/*&output=json`) |
| Unreal (dpdr.org.uk) | WebFetch pages: news, research, webinars, recruitment calls |
| Bluesky | Public API, no key: `https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=depersonalisation`. Many academics have moved here |
| Mastodon (mastodon.social, fediscience.org, scholar.social) | Public `/api/v2/search` and hashtag timelines |
| X/Twitter | $10 voucher only, see section 10 |
| YouTube Data API v3 | Free quota 10,000 units/day: `search.list` by query + `commentThreads.list` for clinicians' channels |
| Podcasts | Apple Search API, no key: `https://itunes.apple.com/search?media=podcast&term=depersonalization`; Podcast Index API (free key) for episodes and transcripts |
| General UK/US forums (Mental Health Forum, Psych Forums, HealthUnlocked) | Check for DPDR boards; WebFetch within ToS |
| Facebook groups, Quora, Discord | No API, skip |

## 10. X/Twitter under the $10 voucher

Verified state as of 2026: X API pay-per-use is the default model since February 2026; $0.005 per post read, $0.01 per post created, 2M reads/month cap; search is limited to the last 7 days; full-archive search is only in Pro ($5,000/month) or Enterprise and is not available to self-serve developers. The $10 is the one-time migration voucher, i.e. roughly 2,000 post reads. Reads are billed per post returned, not per request.

Consequences:
1. Historical search on X is out of scope. Do not spend anything on `/2/tweets/search/recent` for topic queries; its 7-day window is covered better and for free by Bluesky and Mastodon.
2. Spend the voucher on timelines only: `GET /2/users/:id/tweets` for 20–30 accounts (researchers from section 7 who are on X, Unreal, ISSTD, specialized clinics). Set `max_results` and pagination deliberately: 20 accounts × 100 posts = the whole budget. Prefer `since_id` on reruns.
3. Hard stop: a spend counter in `status/x_budget.json`, abort at 90% of the budget, every response to `raw/x/`.
4. Request fields: `tweet.fields=created_at,public_metrics,entities,referenced_tweets`, `expansions=author_id`, `user.fields=description,url`.
5. What to extract: links to new papers and preprints, recruitment announcements, lectures, podcasts. Do not mass-collect patients' personal stories.

## 11. Neuro data and meta-maps

OpenNeuro (search datasets for `depersonalization`, `dissociation`), NeuroVault (statistical maps), NeuroSynth / NeuroQuery (term-based meta-analytic maps). Expect near-empty results; record "empty" in `exports/stats.md` as a finding.

## 12. Books and grey literature

Google Books API and Open Library API for monograph metadata (Sierra 2009; Simeon & Abugel 2006; Hunter & Baker; Nuller). Internet Archive for public-domain texts (Janet, Dugas, Schilder, Mayer-Gross — the historical foundation). Conference abstracts: ISSTD, Timing Research Forum, ECNP (supplements to European Neuropsychopharmacology via Europe PMC), SOBP, ASSC.

## 13. Corpus quality checks

- Retractions: Crossref `update-to` / Retraction Watch database (open via Crossref), PubPeer by DOI.
- Deduplication: DOI → PMID → normalized title + year + first author.
- Coverage: compare record counts across OpenAlex, PubMed and Europe PMC for the same query; explain discrepancies.
- Manual spot check of 30 records for false inclusions (burnout) and false exclusions.

## 14. Data schema (SQLite `corpus.db`)

`works(id, doi, pmid, pmcid, openalex_id, s2_id, title, abstract, year, venue, type, language, oa_status, oa_url, fulltext_path, cited_by_count, excluded_reason, raw_sources)`
`authors(id, openalex_id, orcid, s2_id, display_name, last_institution, last_institution_date, country)`
`authorships(work_id, author_id, position, institution_id, year)`
`institutions(id, openalex_id, name, country, type)`
`citations(citing_id, cited_id, source)`
`trials(id, registry, title, conditions, interventions, status, phase, sponsor, pi, sites, start, end, enrollment, has_results, url)`
`grants(id, funder, title, pi, institution, start, end, amount, abstract, url)`
`posts(id, platform, author_id, created, text, metrics_json, url, raw_path)`
`people(…)` as in section 7.

## 15. Stages with checkpoints

1. Seed: OpenAlex + PubMed + Europe PMC for all queries in section 1 → `works`, counts by year.
2. Snowball: one citation hop from the top-150 most-cited works and from all 2022–2026 works (OpenAlex `referenced_works`, `cites:`).
3. Enrichment: Crossref, Semantic Scholar (tldr, embeddings), Unpaywall.
4. Full texts: Europe PMC, PMC OA, Unpaywall links, CORE.
5. Trials and grants.
6. People: OpenAlex authors, ORCID, institutions, manual verification of the top 40.
7. Social media and forums: Reddit, Bluesky, Mastodon, YouTube, podcasts, X within the voucher.
8. Analysis and exports.

Each stage writes `status/<stage>.json` with progress; a restart resumes from the first unfinished stage.

## 16. Outputs

- `corpus.db`, `raw/`, `fulltext/`.
- `exports/works.csv`, `exports/people.csv`, `exports/trials.csv`, `exports/grants.csv`, `exports/posts_summary.csv`.
- `exports/reading_list.md`: top-10 by citations in each five-year window since 1990, plus top-30 works from 2022–2026 by citations and by S2 influence; reviews and meta-analyses listed separately.
- `exports/clusters.md`: topic clusters from embeddings (mechanisms, pharmacotherapy, psychotherapy, neuromodulation, time, phenomenology) with 5 central works per cluster.
- `exports/stats.md`: works by year, country, institution; OA share; full-text coverage; active groups by grants; trials by status.
- `exports/request_from_author.csv`.

## 17. Do not

Scrape Google Scholar; use Sci-Hub, LibGen or similar; download through someone else's institutional access; circumvent API rate limits; mass-collect personal stories with identifying data; store full texts obtained outside an OA license.
