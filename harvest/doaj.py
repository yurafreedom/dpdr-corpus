"""DOAJ article search (spec section 2): core 1.1 union and the 1.2 time queries.

DOAJ caps a result window at 1000 hits, so oversized queries are split by year.
"""
from urllib.parse import quote

import db
from harvest.common import fetch_json, params_key, log
from harvest.textmatch import core_phrases, families, lucene, time_phrase_groups

API = "https://doaj.org/api/search/articles/"
PAGE = 100
WINDOW = 1000
YEAR_SPLITS = [(1900, 2009), (2010, 2015), (2016, 2019), (2020, 2021), (2022, 2023), (2024, 2025), (2026, 2030)]


def _fields(groups):
    q = lucene(groups)
    return f"(bibjson.title:({q}) OR bibjson.abstract:({q}) OR bibjson.keywords:({q}))" if len(groups) == 1 else \
        " AND ".join(f"(bibjson.title:({lucene([g])}) OR bibjson.abstract:({lucene([g])}))" for g in groups)


def _page(q, page):
    key = params_key("search", {"q": q, "page": page})
    return fetch_json("doaj", key, API + quote(q, safe=""), {"page": page, "pageSize": PAGE}), key


def _harvest(conn, q, family, out, split=False):
    first, _ = _page(q, 1)
    total = first.get("total", 0)
    if total > WINDOW and not split:
        for lo, hi in YEAR_SPLITS:
            _harvest(conn, f"({q}) AND bibjson.year:[{lo} TO {hi}]", family, out, split=True)
        return
    if total > WINDOW:
        out["truncated"].append([q[-60:], total])
        total = WINDOW
    for page in range(1, (total + PAGE - 1) // PAGE + 1):
        d, key = _page(q, page)
        for r in d.get("results", []):
            b = r.get("bibjson", {})
            doi = next((i.get("id") for i in b.get("identifier", []) if i.get("type", "").lower() == "doi"), None)
            link = next((l.get("url") for l in b.get("link", []) if l.get("type") == "fulltext"), None)
            tags = families(b.get("title"), b.get("abstract")) or [family]
            rec = {"doi": doi, "title": b.get("title"), "abstract": b.get("abstract"),
                   "year": int(b["year"]) if str(b.get("year", "")).isdigit() else None,
                   "venue": (b.get("journal") or {}).get("title"), "type": "article",
                   "language": ((b.get("journal") or {}).get("language") or [None])[0],
                   "oa_status": "gold", "oa_url": link,
                   "first_author": (b.get("author") or [{}])[0].get("name"), "keywords": b.get("keywords")}
            db.upsert_work(conn, rec, "doaj", key, tags)
            out[family] += 1


def run(conn):
    out = {"dpdr": 0, "time": 0, "truncated": []}
    _harvest(conn, _fields([core_phrases()]), "dpdr", out)
    for label, groups in time_phrase_groups():
        _harvest(conn, _fields(groups), "time", out)
    log("doaj", str(out))
    return out
