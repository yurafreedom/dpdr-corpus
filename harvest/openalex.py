"""OpenAlex helpers shared by openalex_seed / _lookup / _snowball and people.

Keyless OpenAlex is metered: about $0.10/day = 1,000 credits; search = 10
credits per request, filter list = 1, single entity = 0. Everything below is
written to be cheap: batch id lookups (100 ids per 1-credit request), search
only where no cheaper route exists, and the common.fetch budget guard stops a
module cleanly (BudgetExhausted -> status "partial", resumed next run).
"""
import hashlib
import json

import config
import db
from harvest.common import fetch_json

API = "https://api.openalex.org/"
SELECT = ("id,doi,ids,title,publication_year,type,language,primary_location,open_access,"
          "best_oa_location,authorships,cited_by_count,abstract_inverted_index,referenced_works,"
          "keywords,primary_topic,is_retracted,counts_by_year")


def params(**kw):
    if config.KEYS["openalex"]:
        kw["api_key"] = config.KEYS["openalex"]
    if config.CONTACT_EMAIL:
        kw["mailto"] = config.CONTACT_EMAIL
    return kw


def get(endpoint, key, cost_usd, **kw):
    return fetch_json("openalex", key, API + endpoint, params(**kw), cost_usd=cost_usd)


def abstract(inv):
    if not inv:
        return None
    pos = {}
    for word, idx in inv.items():
        for i in idx:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _strip(url, prefix):
    return url.rsplit("/", 1)[-1] if url else None


def norm(w):
    ids = w.get("ids") or {}
    pmcid = _strip(ids.get("pmcid"), "")
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = "PMC" + pmcid
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    auths = w.get("authorships") or []
    return {
        "openalex_id": db.short_oa(w["id"]),
        "doi": w.get("doi"),
        "pmid": _strip(ids.get("pmid"), ""),
        "pmcid": pmcid,
        "title": w.get("title"),
        "abstract": abstract(w.get("abstract_inverted_index")),
        "year": w.get("publication_year"),
        "venue": ((loc.get("source") or {}).get("display_name")),
        "type": w.get("type"),
        "language": w.get("language"),
        "oa_status": (w.get("open_access") or {}).get("oa_status"),
        "oa_url": best.get("pdf_url") or best.get("landing_page_url") or (w.get("open_access") or {}).get("oa_url"),
        "license": best.get("license"),
        "cited_by_count": w.get("cited_by_count"),
        "referenced_works": [db.short_oa(r) for r in w.get("referenced_works") or []],
        "keywords": [k.get("display_name") for k in w.get("keywords") or []],
        "first_author": (auths[0].get("author") or {}).get("display_name") if auths else None,
        "is_review": int(w.get("type") == "review"),
    }


def load_work(conn, w, raw_ref, tags=(), snowball_from=None):
    rec = norm(w)
    if snowball_from:
        rec["snowball_from"] = snowball_from
    wid = db.upsert_work(conn, rec, "openalex", raw_ref, tags)
    if w.get("is_retracted"):
        conn.execute("UPDATE works SET is_retracted=1 WHERE id=?", (wid,))
    for i, a in enumerate(w.get("authorships") or []):
        au = a.get("author") or {}
        aid = db.upsert_author(conn, {"openalex_id": au.get("id"), "display_name": au.get("display_name"),
                                      "orcid": (au.get("orcid") or "").rsplit("/", 1)[-1] or None})
        insts = a.get("institutions") or []
        iid = db.upsert_institution(conn, insts[0]) if insts else None
        db.set_authorship(conn, wid, i, aid, iid, rec["year"], a.get("raw_author_name") or au.get("display_name"))
    return wid


def batch_lookup(conn, field, values, tags=(), snowball_from=None, key_prefix="lookup"):
    """Fetch works whose <field> is one of values (<=100), 1 credit. Returns works loaded."""
    values = sorted(set(values))
    h = hashlib.sha1(json.dumps(values).encode()).hexdigest()[:14]
    key = f"{key_prefix}_{field.replace('.', '_')}_{h}"
    d = get("works", key, 0.0001, filter=f"{field}:{'|'.join(values)}", **{"per-page": 100}, select=SELECT)
    for w in d.get("results", []):
        load_work(conn, w, key, tags, snowball_from)
    return d.get("results", [])


def cursor_walk(conn, filt, key_prefix, cost_usd, tags=(), snowball_from=None, on_page=None, max_pages=None):
    """Walk a filter with cursor paging; cached pages are replayed for free."""
    cursor, page, total = "*", 0, 0
    while True:
        d = get("works", f"{key_prefix}_p{page}", cost_usd, filter=filt, cursor=cursor,
                **{"per-page": 200}, select=SELECT)
        res = d.get("results", [])
        for w in res:
            load_work(conn, w, f"{key_prefix}_p{page}", tags, snowball_from)
        total += len(res)
        page += 1
        if on_page:
            on_page(page, d["meta"].get("count"))
        nxt = d["meta"].get("next_cursor")
        if not res or not nxt or (max_pages and page >= max_pages):
            return total, d["meta"].get("count")
        cursor = nxt


def replay_raw(conn):
    """Re-normalize every cached OpenAlex works page in raw/openalex (no network)."""
    n = 0
    for path in sorted((config.RAW / "openalex").glob("*.json")):
        stem = path.stem
        if stem.startswith(("recon", "author")):
            continue
        d = json.loads(path.read_text())
        tags = ["dpdr"] if stem.startswith("seed_dpdr") else []
        if stem.startswith("seed_"):
            from harvest.families import family_tag
            key = stem[5:].rsplit("_p", 1)[0]
            tags = ["dpdr"] if key.startswith("dpdr") else [family_tag(key)]
        elif stem.startswith("snowball_ref"):
            tags = ["snowball_ref"]
        elif stem.startswith("snowball_cites"):
            tags = ["snowball_cite"]
        for w in d.get("results", []):
            load_work(conn, w, stem, tags)
            n += 1
    conn.commit()
    return n
