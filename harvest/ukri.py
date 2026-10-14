"""UKRI Gateway to Research API (spec section 6). JSON via the vendor Accept header.

GtR search stems aggressively ("depersonalisation" returns ~2,200 projects of
which ~1 contains the word), so every result page is cached but only projects
whose title/abstract text actually matches the term are kept. PI, lead
organisation and fund (amount, dates) are fetched only for kept projects.
"""
import html
import re

import db
from harvest.common import fetch_json, log, params_key, raw_path, save_status

BASE = "https://gtr.ukri.org/gtr/api"
HDR = {"Accept": "application/vnd.rcuk.gtr.json-v7"}
PSY = re.compile(r"trauma|ptsd|psychiatr|psychopath|psychotherap|\bmental (health|disorder|illness)|depersonali|"
                 r"psychosis|psychotic|ketamine|consciousness", re.I)
QUERIES = {
    "depersonalization": re.compile(r"depersonali[sz]", re.I),
    "depersonalisation": re.compile(r"depersonali[sz]", re.I),
    "derealization": re.compile(r"dereali[sz]", re.I),
    "derealisation": re.compile(r"dereali[sz]", re.I),
    "dissociation": re.compile(r"dissociat", re.I),
    '"time perception"': re.compile(r"time perception|perception of time", re.I),
    '"temporal processing"': re.compile(r"temporal processing", re.I),
    "interoception": re.compile(r"interocepti", re.I),
}


def _text(p):
    return " ".join(html.unescape(p.get(k) or "") for k in ("title", "abstractText", "techAbstractText"))


def _link(p, rel):
    return [l["href"] for l in (p.get("links") or {}).get("link", []) if l.get("rel") == rel]


def _get(url):
    url = url.replace("http://", "https://")
    return fetch_json("ukri", "entity_" + url.rsplit("/api/", 1)[-1], url, headers=HDR)


def _ms_date(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).date().isoformat() if ms else None


def run(conn):
    kept, per_query, scanned = {}, {}, {}
    for q, rx in QUERIES.items():
        page, n_kept, n_seen = 1, 0, 0
        while True:
            params = {"q": q, "s": 100, "p": page}
            key = params_key(f"projects_p{page}", params)
            d = fetch_json("ukri", key, f"{BASE}/projects", params, headers=HDR)
            for p in d.get("project") or []:
                n_seen += 1
                t = _text(p)
                if not rx.search(t):
                    continue
                if q == "dissociation" and not PSY.search(t):
                    continue
                entry = kept.setdefault(p["id"], {"p": p, "tags": set(), "path": str(raw_path("ukri", key))})
                entry["tags"].add(f"ukri:{q}")
                n_kept += 1
            if page >= (d.get("totalPages") or 1):
                break
            page += 1
        per_query[q] = n_kept
        scanned[q] = n_seen
        log("ukri", f"{q}: kept {n_kept} of {n_seen} returned")
    for pid, e in kept.items():
        p = e["p"]
        pis = []
        # Studentships expose no person link; fellowships use FELLOW_PER, industry grants PM_PER.
        people = _link(p, "PI_PER") or _link(p, "FELLOW_PER") or _link(p, "PM_PER")
        for href in people:
            person = _get(href)
            pis.append(" ".join(x for x in (person.get("firstName"), person.get("surname")) if x))
        orgs = [_get(h).get("name") for h in _link(p, "LEAD_ORG")]
        amount, currency, start, end = None, None, None, None
        for h in _link(p, "FUND"):
            f = _get(h)
            v = (f.get("valuePounds") or {})
            amount = (amount or 0) + (v.get("amount") or 0)
            currency = v.get("currencyCode") or "GBP"
            start = start or _ms_date(f.get("start"))
            end = _ms_date(f.get("end")) or end
        ident = next((i["value"] for i in (p.get("identifiers") or {}).get("identifier", [])), None)
        db.upsert_grant(conn, {
            "id": f"UKRI:{pid}", "funder": f"UKRI {p.get('leadFunder') or ''}".strip(),
            "title": p.get("title"),
            "pi": "; ".join(x for x in pis if x) or None,
            "institution": "; ".join(o for o in orgs if o) or None,
            "start": start, "end": end, "amount": amount or None, "currency": currency,
            "abstract": html.unescape(p.get("abstractText") or "") or None,
            "url": f"https://gtr.ukri.org/projects?ref={ident}" if ident else p.get("href"),
            "raw_path": e["path"],
        }, tags=sorted(e["tags"]))
    conn.commit()
    summary = {"per_query_kept": per_query, "per_query_returned": scanned, "unique_projects": len(kept)}
    save_status("ukri", summary)
    return summary
