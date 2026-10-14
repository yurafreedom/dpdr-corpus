"""Europe PMC REST: cursor-paged core results per query family.

Europe PMC adds preprints (SRC:PPR), theses (SRC:ETH), Chinese abstracts (CBA)
and the OA flags the fulltext stage needs (kept in table epmc_meta).
"""
import json

import db
from harvest import queries as Q
from harvest.common import _pace, _session, raw_path, load_status, save_status, log, params_key
from harvest.families import families, family_tag

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/searchPOST"
SRC_TYPE = {"PPR": "preprint", "ETH": "dissertation", "PAT": "patent", "CTX": "clinical-guideline",
            "HIR": "report", "AGR": "article", "CBA": "article", "MED": "article", "PMC": "article"}

META_SQL = """CREATE TABLE IF NOT EXISTS epmc_meta(
  work_id INTEGER, epmc_id TEXT, src TEXT, pmcid TEXT, is_oa INTEGER, in_epmc INTEGER, in_pmc INTEGER,
  has_pdf INTEGER, license TEXT, fulltext_urls TEXT, PRIMARY KEY(src, epmc_id))"""


def post(data, key):
    """POST search, cached verbatim like harvest.common.fetch."""
    path = raw_path("europepmc", key)
    if path.exists():
        return json.loads(path.read_text())
    delay = 2
    for attempt in range(6):
        _pace("europepmc")
        r = _session.post(SEARCH, data=data, timeout=120)
        if r.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            import time
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
    raise RuntimeError(f"Europe PMC failed: {key}")


def norm(r):
    j = r.get("journalInfo", {}).get("journal", {})
    authors = r.get("authorList", {}).get("author", [])
    pts = r.get("pubTypeList", {}).get("pubType", [])
    year = r.get("pubYear")
    first = authors[0] if authors else {}
    fa = first.get("fullName") or first.get("collectiveName")
    return {
        "pmid": r.get("pmid"), "pmcid": r.get("pmcid"), "doi": r.get("doi"),
        "title": r.get("title"), "abstract": r.get("abstractText"),
        "year": int(year) if year and year.isdigit() else None,
        "venue": j.get("title") or r.get("bookOrReportDetails", {}).get("publisher"),
        "type": SRC_TYPE.get(r.get("source"), "article") if not any("review" in p.lower() for p in pts) else "review",
        "language": r.get("language"), "pub_types": pts,
        "is_review": int(any(p.lower() in ("review", "systematic review", "meta-analysis", "review-article") for p in pts)),
        "keywords": r.get("keywordList", {}).get("keyword", []),
        "mesh": [m.get("descriptorName") for m in r.get("meshHeadingList", {}).get("meshHeading", [])],
        "cited_by_count": r.get("citedByCount"),
        "first_author": fa,
        "license": r.get("license"),
        "_authors": [a.get("fullName") or a.get("collectiveName") for a in authors],
    }


def run(conn):
    conn.execute(META_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_epmc_meta_work ON epmc_meta(work_id)")
    st = load_status("europepmc")
    counts = {}
    n_loaded = 0
    for key, q in families():
        query = Q.europepmc(q)
        if query is None:
            continue
        s = st.get(key, {"cursor": "*", "page": 0, "done": False})
        page = 0
        cursor = "*"
        # Replay cached pages from the start (cheap), then continue the network walk.
        while True:
            data = {"query": query, "format": "json", "pageSize": 1000, "resultType": "core",
                    "cursorMark": cursor, "synonym": "false"}
            d = post(data, params_key(f"search_{key}_p{page}", data))
            counts[key] = d.get("hitCount")
            res = d.get("resultList", {}).get("result", [])
            for r in res:
                rec = norm(r)
                wid = db.upsert_work(conn, rec, "europepmc", f"search_{key}_p{page}", [family_tag(key)])
                for i, name in enumerate(rec["_authors"]):
                    if name:
                        db.set_authorship(conn, wid, i, None, None, rec["year"], name)
                conn.execute("""INSERT OR REPLACE INTO epmc_meta VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                    wid, r.get("id"), r.get("source"), r.get("pmcid"), int(r.get("isOpenAccess") == "Y"),
                    int(r.get("inEPMC") == "Y"), int(r.get("inPMC") == "Y"), int(r.get("hasPDF") == "Y"),
                    r.get("license"), json.dumps(r.get("fullTextUrlList", {}).get("fullTextUrl", []))))
                n_loaded += 1
            nxt = d.get("nextCursorMark")
            page += 1
            if not res or not nxt or nxt == cursor:
                break
            cursor = nxt
            if page % 5 == 0:
                conn.commit()
        st[key] = {"pages": page, "done": True, "hits": counts[key]}
        save_status("europepmc", st)
        conn.commit()
        log("europepmc", f"{key}: {counts[key]} hits, {page} pages")
    return {"family_counts": counts, "records_loaded": n_loaded}
