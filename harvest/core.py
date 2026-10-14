"""CORE API v3 search (spec section 2): aggregated repository records with download URLs.

Needs CORE_API_KEY (free registration). Full-text PDFs are fetched later by the
fulltext stage; this module stores metadata and downloadUrl only.
"""
import config
import db
from harvest.common import SkipModule, fetch_json, params_key, log
from harvest.textmatch import core_phrases, families, time_phrase_groups

API = "https://api.core.ac.uk/v3/search/works"
PAGE = 100
MAX_OFFSET = 10000


def _q(groups):
    def term(p):
        return f'"{p}"' if " " in p or "-" in p else p
    return " AND ".join("(" + " OR ".join(f"title:{term(p)} OR abstract:{term(p)}" for p in g) + ")" for g in groups)


def run(conn):
    key = config.KEYS["core"]
    if not key:
        raise SkipModule("CORE_API_KEY not set (free at https://core.ac.uk/services/api)")
    headers = {"Authorization": f"Bearer {key}"}
    queries = [("dpdr", _q([core_phrases()]))] + [("time", _q(g)) for _, g in time_phrase_groups()]
    out = {"dpdr": 0, "time": 0}
    for family, q in queries:
        offset = 0
        while offset < MAX_OFFSET:
            p = {"q": q, "limit": PAGE, "offset": offset}
            ck = params_key("search", p)
            d = fetch_json("core", ck, API, p, headers=headers)
            res = d.get("results") or []
            for r in res:
                rec = {"doi": r.get("doi"), "title": r.get("title"), "abstract": r.get("abstract"),
                       "year": r.get("yearPublished"), "venue": r.get("publisher"),
                       "type": (r.get("documentType") or "article"),
                       "language": (r.get("language") or {}).get("code"),
                       "oa_status": "green" if r.get("downloadUrl") else None, "oa_url": r.get("downloadUrl"),
                       "first_author": ((r.get("authors") or [{}])[0] or {}).get("name")}
                db.upsert_work(conn, rec, "core", ck, families(rec["title"], rec["abstract"]) or [family])
                out[family] += 1
            offset += len(res)
            if not res or offset >= d.get("totalHits", 0):
                break
    log("core", str(out))
    return out
