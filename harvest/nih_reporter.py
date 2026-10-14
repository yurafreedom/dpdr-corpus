"""NIH RePORTER API v2 (spec section 6).

RePORTER returns one record per fiscal-year application; rows are aggregated
to one grant per core project number (amount = sum over fiscal years seen).
Wildcards (dissociat*) are not supported: the API returns 1 hit. "dissociation"
alone returns ~22k mostly biochemistry projects, so it is scoped with a
psychiatric-context AND clause.
"""
import hashlib
import json

import db
from harvest.common import fetch_json, log, raw_path, save_status

URL = "https://api.reporter.nih.gov/v2/projects/search"
PAGE = 500
MAX_OFFSET = 14500
PSY = ('(trauma OR PTSD OR psychiatric OR psychological OR "mental health" OR dissociative OR '
       'depersonalization OR psychosis OR ketamine)')
QUERIES = {
    "depersonalization": '"depersonalization"',
    "depersonalisation": '"depersonalisation"',
    "derealization": '"derealization"',
    "derealisation": '"derealisation"',
    "dissociative": '"dissociative"',
    "dissociation(psych)": f"dissociation AND {PSY}",
    "time perception": '"time perception"',
    "temporal processing": '"temporal processing"',
    "interoception": "interoception",
}


def _body(text, offset, fiscal_year=None):
    crit = {"advanced_text_search": {"operator": "advanced", "search_field": "projecttitle,terms,abstracttext",
                                     "search_text": text}}
    if fiscal_year:
        crit["fiscal_years"] = [fiscal_year]
    return {"criteria": crit, "offset": offset, "limit": PAGE,
            "sort_field": "appl_id", "sort_order": "asc"}


def _key(body):
    return "search_" + hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def _pages(text, fiscal_year=None):
    offset = 0
    while True:
        body = _body(text, offset, fiscal_year)
        key = _key(body)
        d = fetch_json("nih_reporter", key, URL, method="POST", json_body=body)
        yield d, raw_path("nih_reporter", key)
        total = d["meta"]["total"]
        offset += PAGE
        if offset >= total or not d["results"]:
            return
        if offset > MAX_OFFSET:
            raise RuntimeError(f"RePORTER offset cap hit for {text!r} fy={fiscal_year}")


def _search(text):
    first = next(_pages(text))[0]
    if first["meta"]["total"] <= MAX_OFFSET:
        yield from _pages(text)
        return
    for fy in range(1985, 2028):  # split by fiscal year when over the offset cap
        yield from _pages(text, fy)


def run(conn):
    projects, tags, per_query = {}, {}, {}
    for label, text in QUERIES.items():
        seen = set()
        for d, path in _search(text):
            for r in d["results"]:
                core = r.get("core_project_num") or r.get("project_num")
                seen.add(core)
                p = projects.setdefault(core, {"appls": {}, "path": str(path)})
                p["appls"][r["appl_id"]] = r
                tags.setdefault(core, set()).add(f"nih:{label}")
        per_query[label] = len(seen)
        log("nih_reporter", f"{label}: {len(seen)} core projects")
    for core, p in projects.items():
        appls = sorted(p["appls"].values(), key=lambda r: (r.get("fiscal_year") or 0, r["appl_id"]))
        last = appls[-1]
        pis = [" ".join(pi["full_name"].split()) for pi in last.get("principal_investigators") or [] if pi.get("full_name")]
        org = last.get("organization") or {}
        starts = [r.get("project_start_date") for r in appls if r.get("project_start_date")]
        ends = [r.get("project_end_date") for r in appls if r.get("project_end_date")]
        amount = sum(r.get("award_amount") or 0 for r in appls)
        abstract = next((r.get("abstract_text") for r in reversed(appls) if r.get("abstract_text")), None)
        db.upsert_grant(conn, {
            "id": f"NIH:{core}", "funder": "NIH" + (f" {last.get('agency_ic_admin', {}).get('abbreviation')}"
                                                    if last.get("agency_ic_admin") else ""),
            "title": last.get("project_title"),
            "pi": "; ".join(pis) or " ".join((last.get("contact_pi_name") or "").split()) or None,
            "institution": ", ".join(x for x in (org.get("org_name"), org.get("org_city"), org.get("org_country")) if x),
            "start": (min(starts) or "")[:10] if starts else None,
            "end": (max(ends) or "")[:10] if ends else None,
            "amount": amount or None, "currency": "USD",
            "abstract": abstract,
            "url": f"https://reporter.nih.gov/project-details/{last['appl_id']}",
            "raw_path": p["path"],
        }, tags=sorted(tags[core]))
    conn.commit()
    summary = {"per_query_core_projects": per_query, "unique_core_projects": len(projects),
               "fiscal_year_records": sum(len(p["appls"]) for p in projects.values())}
    save_status("nih_reporter", summary)
    return summary
