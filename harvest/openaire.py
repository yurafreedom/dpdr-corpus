"""OpenAIRE Graph API v1 (spec section 2): core 1.1 union terms, title and description.

Verified 2026-10-13: the legacy api.openaire.eu/search/publications still answers,
but the Graph API v1 (api.openaire.eu/graph/v1/researchProducts) is the current
interface; it supports mainTitle= / description= field search and cursor paging.
Funding links stay in the raw JSON.
"""
import db
from harvest.common import fetch_json, params_key, load_status, save_status, log
from harvest.textmatch import core_phrases, families

API = "https://api.openaire.eu/graph/v1/researchProducts"
PAGE = 100


def _rec(r):
    doi = next((p["value"] for p in r.get("pids") or [] if (p.get("scheme") or "").lower() == "doi"), None)
    urls = [u for inst in r.get("instances") or [] for u in inst.get("urls") or []]
    desc = (r.get("descriptions") or [None])[0]
    authors = sorted(r.get("authors") or [], key=lambda a: a.get("rank") or 99)
    date = r.get("publicationDate") or ""
    return {
        "doi": doi, "title": r.get("mainTitle"), "abstract": desc,
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "venue": (r.get("container") or {}).get("name") or r.get("publisher"),
        "type": (r.get("instances") or [{}])[0].get("type") or r.get("type"),
        "language": (r.get("language") or {}).get("code"),
        "oa_status": r.get("openAccessColor") or ((r.get("bestAccessRight") or {}).get("label") or "").lower() or None,
        "oa_url": urls[0] if urls else None,
        "first_author": authors[0].get("fullName") if authors else None,
        "keywords": [s.get("subject", {}).get("value") for s in r.get("subjects") or [] if s.get("subject")][:20] or None,
    }


def run(conn):
    st = load_status("openaire")
    done = set(st.get("done", []))
    out = {"by_query": {}, "upserts": 0}
    for term in core_phrases():
        for field in ("mainTitle", "description"):
            qid = f"{field}:{term}"
            cursor, n = "*", 0
            while cursor:
                p = {field: f'"{term}"' if " " in term else term, "type": "publication",
                     "pageSize": PAGE, "cursor": cursor}
                key = params_key(f"{field}_{term}", p)
                d = fetch_json("openaire", key, API, p)
                res = d.get("results") or []
                for r in res:
                    rec = _rec(r)
                    if not rec["title"]:
                        continue
                    db.upsert_work(conn, rec, "openaire", key, families(rec["title"], rec["abstract"]) or ["dpdr"])
                    n += 1
                nxt = (d.get("header") or {}).get("nextCursor")
                cursor = nxt if res and nxt and nxt != cursor else None
            conn.commit()
            done.add(qid)
            save_status("openaire", {"done": sorted(done)})
            out["by_query"][qid] = n
            out["upserts"] += n
            log("openaire", f"{qid}: {n}")
    return out
