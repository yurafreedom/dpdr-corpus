"""SQLite schema (spec section 14) and dedup-aware upserts.

Works are deduplicated DOI -> PMID -> PMCID -> OpenAlex -> S2 -> normalized
(title, year, first author). Merging never overwrites a non-empty field with an
empty one; abstracts keep the longest version; tags and raw_sources accumulate.
"""
import json
import re
import sqlite3
import unicodedata

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS works(
  id INTEGER PRIMARY KEY,
  doi TEXT, pmid TEXT, pmcid TEXT, openalex_id TEXT, s2_id TEXT,
  title TEXT, abstract TEXT, year INTEGER, venue TEXT, type TEXT, language TEXT,
  oa_status TEXT, oa_url TEXT, fulltext_path TEXT, cited_by_count INTEGER,
  excluded_reason TEXT, raw_sources TEXT,
  -- additions beyond the spec's column list
  keywords TEXT, mesh TEXT, pub_types TEXT, first_author TEXT, title_norm TEXT,
  query_tags TEXT, scope_flags TEXT, is_review INTEGER, is_retracted INTEGER,
  tldr TEXT, s2_influential INTEGER, referenced_works TEXT, embedding TEXT, crossref_funders TEXT,
  license TEXT, snowball_from TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_works_doi ON works(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_works_pmid ON works(pmid) WHERE pmid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_works_oa ON works(openalex_id) WHERE openalex_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_works_pmcid ON works(pmcid);
CREATE INDEX IF NOT EXISTS ix_works_s2 ON works(s2_id);
CREATE INDEX IF NOT EXISTS ix_works_tnorm ON works(title_norm, year);

CREATE TABLE IF NOT EXISTS authors(
  id INTEGER PRIMARY KEY, openalex_id TEXT UNIQUE, orcid TEXT, s2_id TEXT, display_name TEXT,
  last_institution TEXT, last_institution_date TEXT, country TEXT,
  works_count INTEGER, cited_by_count INTEGER, counts_by_year TEXT, affiliations TEXT
);
CREATE INDEX IF NOT EXISTS ix_authors_orcid ON authors(orcid);
CREATE TABLE IF NOT EXISTS authorships(
  work_id INTEGER, author_id INTEGER, position INTEGER, institution_id INTEGER, year INTEGER,
  raw_name TEXT, PRIMARY KEY(work_id, position)
);
CREATE INDEX IF NOT EXISTS ix_authorships_author ON authorships(author_id);
CREATE TABLE IF NOT EXISTS institutions(
  id INTEGER PRIMARY KEY, openalex_id TEXT UNIQUE, name TEXT, country TEXT, type TEXT, ror TEXT
);
CREATE TABLE IF NOT EXISTS citations(
  citing_id TEXT, cited_id TEXT, source TEXT, PRIMARY KEY(citing_id, cited_id, source)
);
CREATE TABLE IF NOT EXISTS trials(
  id TEXT PRIMARY KEY, registry TEXT, title TEXT, conditions TEXT, interventions TEXT, status TEXT,
  phase TEXT, sponsor TEXT, pi TEXT, sites TEXT, start TEXT, "end" TEXT, enrollment INTEGER,
  has_results INTEGER, url TEXT, query_tags TEXT, raw_path TEXT
);
CREATE TABLE IF NOT EXISTS grants(
  id TEXT PRIMARY KEY, funder TEXT, title TEXT, pi TEXT, institution TEXT, start TEXT, "end" TEXT,
  amount REAL, currency TEXT, abstract TEXT, url TEXT, query_tags TEXT, raw_path TEXT
);
CREATE TABLE IF NOT EXISTS posts(
  id TEXT PRIMARY KEY, platform TEXT, author_id TEXT, created TEXT, text TEXT, metrics_json TEXT,
  url TEXT, raw_path TEXT, community TEXT, title TEXT, query_tags TEXT
);
-- Ids removed by the Reddit deletion purge (id and time only), so cached pages never re-insert them.
CREATE TABLE IF NOT EXISTS purged(id TEXT PRIMARY KEY, at TEXT);
CREATE TABLE IF NOT EXISTS people(
  name TEXT PRIMARY KEY, openalex_id TEXT, orcid TEXT, s2_id TEXT, works_total INTEGER,
  works_2022_2026 INTEGER, current_institution TEXT, institution_source TEXT,
  institution_verified TEXT, country TEXT, top5_works TEXT, coauthors TEXT, channels TEXT,
  clinician TEXT, seed INTEGER, corpus_works INTEGER, orcid_employment TEXT, needs_review TEXT
);
CREATE TABLE IF NOT EXISTS records(
  -- generic store for sources that do not fit the tables above
  -- (datasets, books, repository records, podcasts, archive captures)
  id TEXT PRIMARY KEY, kind TEXT, source TEXT, title TEXT, year INTEGER, url TEXT,
  meta_json TEXT, raw_path TEXT
);
"""

LIST_FIELDS = ("query_tags", "raw_sources", "scope_flags")
WORK_COLS = ("doi", "pmid", "pmcid", "openalex_id", "s2_id", "title", "abstract", "year", "venue",
             "type", "language", "oa_status", "oa_url", "fulltext_path", "cited_by_count",
             "keywords", "mesh", "pub_types", "first_author", "is_review", "tldr", "s2_influential",
             "referenced_works", "embedding", "crossref_funders", "license", "snowball_from")


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if "needs_review" not in [r[1] for r in conn.execute("PRAGMA table_info(people)")]:
        conn.execute("ALTER TABLE people ADD COLUMN needs_review TEXT")
    return conn


def norm_doi(doi):
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi)
    return doi or None


def norm_title(t):
    if not t:
        return None
    t = unicodedata.normalize("NFKD", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^a-z0-9а-яё]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()[:200] or None


def norm_surname(name):
    if not name:
        return ""
    name = name.split(",")[0] if "," in name else name.split()[-1] if name.split() else name
    return norm_title(name) or ""


def short_oa(oa_id):
    return oa_id.rsplit("/", 1)[-1] if oa_id else None


def _jl(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return [v]
    return list(v)


def _dump(v):
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def find_work(conn, rec):
    for col in ("doi", "pmid", "pmcid", "openalex_id", "s2_id"):
        if rec.get(col):
            row = conn.execute(f"SELECT * FROM works WHERE {col}=?", (rec[col],)).fetchone()
            if row:
                return row
    tn = norm_title(rec.get("title"))
    if tn and rec.get("year") and len(tn) > 25:
        fa = norm_surname(rec.get("first_author"))
        for row in conn.execute("SELECT * FROM works WHERE title_norm=? AND year BETWEEN ? AND ?",
                                (tn, rec["year"] - 1, rec["year"] + 1)):
            if not fa or not row["first_author"] or norm_surname(row["first_author"]) == fa:
                return row
    return None


def upsert_work(conn, rec, source, raw_ref=None, tags=()):
    """Insert or merge a normalized work record. Returns the works.id."""
    rec = dict(rec)
    rec["doi"] = norm_doi(rec.get("doi"))
    rec["openalex_id"] = short_oa(rec.get("openalex_id"))
    for k in ("pmid", "pmcid"):
        if rec.get(k):
            rec[k] = str(rec[k]).strip()
    if rec.get("pmcid") and not str(rec["pmcid"]).upper().startswith("PMC"):
        rec["pmcid"] = "PMC" + str(rec["pmcid"])
    for k in ("keywords", "mesh", "pub_types", "referenced_works", "embedding", "crossref_funders"):
        rec[k] = _dump(rec.get(k))
    row = find_work(conn, rec)
    raw = [f"{source}:{raw_ref}" if raw_ref else source]
    if row is None:
        cols = [c for c in WORK_COLS if rec.get(c) is not None]
        vals = [rec[c] for c in cols]
        cols += ["title_norm", "raw_sources", "query_tags"]
        vals += [norm_title(rec.get("title")), json.dumps(raw), json.dumps(sorted(set(tags)))]
        try:
            cur = conn.execute(f"INSERT INTO works({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # A unique id collided with a row the matcher missed (e.g. two DOIs); retry as merge.
            row = find_work(conn, rec)
            if row is None:
                raise
    upd = {}
    for c in WORK_COLS:
        new = rec.get(c)
        if new in (None, "", "[]"):
            continue
        old = row[c]
        if c == "abstract":
            if not old or len(new) > len(old):
                upd[c] = new
        elif c in ("cited_by_count", "s2_influential"):
            if old is None or new > old:
                upd[c] = new
        elif old in (None, ""):
            upd[c] = new
    if upd.get("title"):
        upd["title_norm"] = norm_title(upd["title"])
    rs = _jl(row["raw_sources"])
    if raw[0] not in rs:
        upd["raw_sources"] = json.dumps(rs + raw)
    qt = set(_jl(row["query_tags"]))
    if not set(tags) <= qt:
        upd["query_tags"] = json.dumps(sorted(qt | set(tags)))
    # Unique columns may collide with a different row; drop those to keep the merge safe.
    for c in ("doi", "pmid", "openalex_id"):
        if c in upd and conn.execute(f"SELECT 1 FROM works WHERE {c}=? AND id<>?", (upd[c], row["id"])).fetchone():
            del upd[c]
    if upd:
        conn.execute(f"UPDATE works SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?",
                     [*upd.values(), row["id"]])
    return row["id"]


def upsert_institution(conn, inst):
    if not inst or not inst.get("id"):
        return None
    oid = short_oa(inst["id"])
    conn.execute("""INSERT INTO institutions(openalex_id, name, country, type, ror) VALUES(?,?,?,?,?)
                    ON CONFLICT(openalex_id) DO UPDATE SET name=excluded.name""",
                 (oid, inst.get("display_name"), inst.get("country_code"), inst.get("type"), inst.get("ror")))
    return conn.execute("SELECT id FROM institutions WHERE openalex_id=?", (oid,)).fetchone()[0]


def upsert_author(conn, a):
    """a: dict with openalex_id, display_name, orcid, and optional profile fields."""
    oid = short_oa(a.get("openalex_id"))
    if not oid:
        return None
    fields = {k: a.get(k) for k in ("orcid", "s2_id", "display_name", "last_institution",
                                    "last_institution_date", "country", "works_count", "cited_by_count",
                                    "counts_by_year", "affiliations")}
    fields = {k: _dump(v) for k, v in fields.items() if v is not None}
    row = conn.execute("SELECT id FROM authors WHERE openalex_id=?", (oid,)).fetchone()
    if row is None:
        cols = ["openalex_id", *fields]
        conn.execute(f"INSERT INTO authors({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                     [oid, *fields.values()])
        return conn.execute("SELECT id FROM authors WHERE openalex_id=?", (oid,)).fetchone()[0]
    if fields:
        conn.execute(f"UPDATE authors SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     [*fields.values(), row[0]])
    return row[0]


def set_authorship(conn, work_id, position, author_id, institution_id, year, raw_name):
    # An OpenAlex authorship (author_id set) replaces a name-only PubMed/EPMC row at the same position.
    conn.execute("""INSERT INTO authorships(work_id, author_id, position, institution_id, year, raw_name)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(work_id, position) DO UPDATE SET
                    author_id=COALESCE(excluded.author_id, author_id),
                    institution_id=COALESCE(excluded.institution_id, institution_id),
                    raw_name=CASE WHEN excluded.author_id IS NOT NULL THEN excluded.raw_name
                                  ELSE COALESCE(raw_name, excluded.raw_name) END""",
                 (work_id, author_id, position, institution_id, year, raw_name))


def add_citation(conn, citing, cited, source):
    conn.execute("INSERT OR IGNORE INTO citations VALUES(?,?,?)", (citing, cited, source))


def _rel(path):
    """Store paths relative to the project root so the folder can be moved."""
    s = str(path)
    root = str(config.ROOT) + "/"
    return s[len(root):] if s.startswith(root) else s


def _upsert_row(conn, table, row, key="id"):
    row = {k: _dump(v) for k, v in row.items()}
    if row.get("raw_path"):
        row["raw_path"] = _rel(row["raw_path"])
    cols = list(row)
    q = lambda c: f'"{c}"'  # noqa: E731
    conn.execute(
        f"INSERT INTO {table}({','.join(map(q, cols))}) VALUES({','.join('?' * len(cols))}) "
        f"ON CONFLICT({key}) DO UPDATE SET " + ", ".join(f"{q(c)}=excluded.{q(c)}" for c in cols if c != key),
        list(row.values()))


def merge_tags(conn, table, rid, tags):
    row = conn.execute(f"SELECT query_tags FROM {table} WHERE id=?", (rid,)).fetchone()
    old = set(_jl(row[0])) if row else set()
    return sorted(old | set(tags))


def upsert_trial(conn, row, tags=()):
    row["query_tags"] = merge_tags(conn, "trials", row["id"], tags)
    _upsert_row(conn, "trials", row)


def upsert_grant(conn, row, tags=()):
    row["query_tags"] = merge_tags(conn, "grants", row["id"], tags)
    _upsert_row(conn, "grants", row)


def is_purged(conn, rid):
    return conn.execute("SELECT 1 FROM purged WHERE id=?", (rid,)).fetchone() is not None


def upsert_post(conn, row, tags=()):
    if is_purged(conn, row["id"]):
        return
    row["query_tags"] = merge_tags(conn, "posts", row["id"], tags)
    _upsert_row(conn, "posts", row)


def upsert_record(conn, row):
    _upsert_row(conn, "records", row)


def upsert_person(conn, row):
    _upsert_row(conn, "people", row, key="name")
