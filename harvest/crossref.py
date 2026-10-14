"""Crossref: funders, licenses, venue/type gaps, update-to relations. 100 DOIs per request."""
import hashlib
import json
import re

import config
from harvest.common import fetch_json, load_status, save_status, log
from harvest.scope import IN_SCOPE_SQL

API = "https://api.crossref.org/works"
SELECT = "DOI,title,abstract,container-title,type,issued,funder,license,update-to,relation,is-referenced-by-count"


def run(conn):
    from harvest import normalize
    normalize.apply_filters(conn)
    st = load_status("crossref")
    done = set(st.get("done", []))
    dois = [r[0] for r in conn.execute(f"SELECT doi FROM works WHERE doi IS NOT NULL AND {IN_SCOPE_SQL}")]
    # Crossref filter values are comma-separated, so DOIs with commas go one by one.
    todo = sorted(d for d in set(dois) - done if "," not in d)
    log("crossref", f"{len(todo)} DOIs to enrich")
    n = 0
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        h = hashlib.sha1(",".join(chunk).encode()).hexdigest()[:14]
        p = {"filter": ",".join(f"doi:{d}" for d in chunk), "rows": 100, "select": SELECT}
        if config.CONTACT_EMAIL:
            p["mailto"] = config.CONTACT_EMAIL
        d = fetch_json("crossref", f"works_{h}", API, p)
        for it in d["message"]["items"]:
            doi = it["DOI"].lower()
            funders = [{"name": f.get("name"), "doi": f.get("DOI"), "award": f.get("award")} for f in it.get("funder", [])]
            lic = (it.get("license") or [{}])[0].get("URL")
            abstract = re.sub(r"<[^>]+>", " ", it["abstract"]).strip() if it.get("abstract") else None
            venue = (it.get("container-title") or [None])[0]
            row = conn.execute("SELECT id, abstract, venue, license FROM works WHERE doi=?", (doi,)).fetchone()
            if not row:
                continue
            upd = {"crossref_funders": json.dumps(funders) if funders else None}
            if lic and not row["license"]:
                upd["license"] = lic
            if venue and not row["venue"]:
                upd["venue"] = venue
            if abstract and (not row["abstract"] or len(abstract) > len(row["abstract"]) + 40):
                upd["abstract"] = abstract
            upd = {k: v for k, v in upd.items() if v is not None}
            if upd:
                conn.execute(f"UPDATE works SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", [*upd.values(), row["id"]])
            n += 1
        done |= set(chunk)
        st["done"] = sorted(done)
        save_status("crossref", st)
        conn.commit()
        if (i // 100) % 20 == 0:
            log("crossref", f"{i + len(chunk)}/{len(todo)}")
    return {"dois_enriched": n, "requested": len(done)}
