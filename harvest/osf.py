"""PsyArXiv preprints and OSF registrations via OSF API v2 (spec section 4).

OSF supports substring filters on title and description (verified 2026-10-13:
filter[title]=depersonalization returns 14 PsyArXiv preprints, filter[description]
37), so no date walk is needed. Matches are re-checked locally.
"""
from urllib.parse import parse_qs, urlparse

import db
from harvest.common import fetch_json, params_key, log
from harvest.textmatch import families

API = "https://api.osf.io/v2"
TERMS = ["depersonalization", "depersonalisation", "derealization", "derealisation",
         "feelings of unreality", "time perception", "temporal experience", "subjective time",
         "passage of time", "duration estimation", "interval timing", "temporal binding", "lived time"]


def _walk(endpoint, params, prefix):
    page = 1
    while True:
        p = dict(params, **{"page[size]": 100, "page": page})
        d = fetch_json("osf", params_key(prefix, p), f"{API}/{endpoint}/", p)
        for item in d.get("data", []):
            yield item, params_key(prefix, p)
        nxt = (d.get("links") or {}).get("next")
        if not nxt:
            break
        page = int(parse_qs(urlparse(nxt).query).get("page", [page + 1])[0])


def run(conn):
    out = {"psyarxiv": 0, "registrations": 0}
    seen = {}
    for term in TERMS:
        for field in ("title", "description"):
            params = {"filter[provider]": "psyarxiv", f"filter[{field}]": term}
            for item, ref in _walk("preprints", params, f"psyarxiv_{field}_{term}"):
                a = item["attributes"]
                tags = families(a.get("title"), a.get("description"))
                if not tags:
                    continue
                base_id = item["id"].split("_v")[0]
                vers = int(item["id"].split("_v")[1]) if "_v" in item["id"] else 1
                if base_id in seen and seen[base_id][0] >= vers:
                    continue
                seen[base_id] = (vers, item, tags, ref)
    for base_id, (_, item, tags, ref) in seen.items():
        a = item["attributes"]
        links = item.get("links", {})
        pdoi = links.get("preprint_doi")
        date = a.get("date_published") or a.get("date_created")
        rec = {
            # The peer-reviewed article DOI, if linked, would merge with the journal version;
            # keep the preprint DOI so preprint and article stay separate records.
            "doi": pdoi, "title": a.get("title"), "abstract": a.get("description"),
            "year": int(date[:4]) if date else None, "venue": "PsyArXiv", "type": "preprint",
            "oa_status": "green", "oa_url": links.get("html"),
            "keywords": a.get("tags") or None, "license": None,
        }
        db.upsert_work(conn, rec, "osf", ref, tags)
    out["psyarxiv"] = len(seen)
    log("osf", f"psyarxiv: {len(seen)}")

    regs = {}
    for term in TERMS:
        for field in ("title", "description"):
            for item, ref in _walk("registrations", {f"filter[{field}]": term}, f"reg_{field}_{term}"):
                a = item["attributes"]
                tags = families(a.get("title"), a.get("description"))
                if tags and not a.get("withdrawn"):
                    regs[item["id"]] = (item, tags, ref)
    for rid, (item, tags, ref) in regs.items():
        a = item["attributes"]
        date = a.get("date_registered") or a.get("date_created")
        db.upsert_record(conn, {
            "id": f"osf_reg:{rid}", "kind": "preregistration", "source": "osf",
            "title": a.get("title"), "year": int(date[:4]) if date else None,
            "url": item.get("links", {}).get("html") or f"https://osf.io/{rid}",
            "meta_json": {"description": a.get("description"), "tags": tags,
                          "registration_supplement": a.get("registration_supplement"),
                          "date_registered": date, "embargoed": a.get("embargoed")},
            "raw_path": f"raw/osf/{ref}.json",
        })
    out["registrations"] = len(regs)
    log("osf", f"registrations: {len(regs)}")
    return out
