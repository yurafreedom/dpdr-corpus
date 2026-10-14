"""ISRCTN registry (spec section 5).

The JSON route in the spec (/api/query/format/json) returns HTTP 400; the live
API is /api/query/format/default, which returns XML (namespace
http://www.67bricks.com/isrctn). `offset` is ignored by the API, so each query
is fetched in one request with a large `limit`.
"""
import xml.etree.ElementTree as ET
from datetime import date

import db
from harvest.common import fetch, log, params_key, raw_path, save_status

URL = "https://www.isrctn.com/api/query/format/default"
QUERIES = ["depersonalization", "depersonalisation", "derealization", "derealisation", "dissociative",
           '"time perception"', '"temporal processing"', "interoception"]
NS = {"i": "http://www.67bricks.com/isrctn"}


def _t(el, path):
    x = el.find(path, NS)
    return x.text.strip() if x is not None and x.text else None


def _date(el, path):
    v = _t(el, path) if el is not None else None
    return v[:10] if v else None


def _derived_status(rec_start, rec_end, overall_end, stage):
    """ISRCTN rarely sets recruitmentStatusOverride; derive a status from dates (marked as derived)."""
    today = date.today().isoformat()
    if stage == "Results":
        return "Completed (derived: results published)"
    if overall_end and overall_end < today:
        return "Completed (derived: past overall end date)"
    if rec_start and rec_start > today:
        return "Not yet recruiting (derived)"
    if rec_end and rec_end < today:
        return "Recruitment ended (derived)"
    if rec_start:
        return "Recruiting (derived)"
    return None


def _all(el, path):
    return [x.text.strip() for x in el.findall(path, NS) if x.text and x.text.strip()]


def _row(full, path):
    trial = full.find("i:trial", NS)
    num = _t(trial, "i:isrctn")
    tid = f"ISRCTN{num}"
    desc = trial.find("i:trialDescription", NS)
    parts = trial.find("i:participants", NS)
    design = trial.find("i:trialDesign", NS)
    interventions, phases = [], set()
    for iv in trial.findall("i:interventions/i:intervention", NS):
        name = _t(iv, "i:drugNames") or (_t(iv, "i:description") or "")[:200]
        interventions.append(f"{_t(iv, 'i:interventionType') or ''}: {name}".strip(": "))
        if _t(iv, "i:phase"):
            phases.add(_t(iv, "i:phase"))
    conditions = _all(trial, "i:conditions/i:condition/i:description")
    # Contacts and sponsors are sibling elements of <trial>, linked by id from <parties>.
    contacts = {c.get("id"): c for c in full.findall("i:contact", NS)}
    pis = []
    for cid in _all(trial, "i:parties/i:contactId"):
        c = contacts.get(cid)
        if c is not None and ("Scientific" in _all(c, "i:contactTypes/i:contactType")
                              or "Principal investigator" in _all(c, "i:contactTypes/i:contactType")):
            pis.append(" ".join(x for x in (_t(c, "i:forename"), _t(c, "i:surname")) if x))
    sponsors = [_t(s, "i:organisation") for s in full.findall("i:sponsor", NS) if _t(s, "i:organisation")]
    sites = []
    for tc in parts.findall("i:trialCentres/i:trialCentre", NS) if parts is not None else []:
        sites.append(", ".join(x for x in (_t(tc, "i:name"), _t(tc, "i:city"), _t(tc, "i:country")) if x))
    status = _t(parts, "i:recruitmentStatusOverride") if parts is not None else None
    stage = _t(trial, "i:results/i:publicationStage")
    enrol = None
    for f in ("i:totalFinalEnrolment", "i:targetEnrolment"):
        v = _t(parts, f) if parts is not None else None
        if v and v.isdigit() and int(v) > 0:
            enrol = int(v)
            break
    if not status:
        status = _derived_status(_date(parts, "i:recruitmentStart"), _date(parts, "i:recruitmentEnd"),
                                 _date(design, "i:overallEndDate"), stage)
    return {
        "id": tid, "registry": "ISRCTN",
        "title": _t(desc, "i:scientificTitle") or _t(desc, "i:title"),
        "conditions": conditions,
        "interventions": interventions,
        "status": status,
        "phase": ", ".join(sorted(phases)) or None,
        "sponsor": "; ".join(sponsors) or None,
        "pi": "; ".join(p for p in pis if p) or None,
        "sites": sites,
        "start": _date(parts, "i:recruitmentStart"),
        "end": _date(design, "i:overallEndDate"),
        "enrollment": enrol,
        "has_results": int(stage == "Results"),
        "url": f"https://www.isrctn.com/{tid}",
        "raw_path": str(path),
    }


def run(conn):
    per_query = {}
    for q in QUERIES:
        params = {"q": q, "limit": 1000}
        key = params_key("query", params)
        root = ET.fromstring(fetch("isrctn", key, URL, params, ext="xml"))
        path = raw_path("isrctn", key, "xml")
        total = int(root.get("totalCount", 0))
        fulls = root.findall("i:fullTrial", NS)
        if total > len(fulls):
            log("isrctn", f"WARNING {q}: totalCount {total} > returned {len(fulls)}")
        for full in fulls:
            db.upsert_trial(conn, _row(full, path), tags=[f"isrctn:{q}"])
        per_query[q] = len(fulls)
        log("isrctn", f"{q}: {len(fulls)}")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM trials WHERE registry='ISRCTN'").fetchone()[0]
    summary = {"per_query": per_query, "unique_trials": total}
    save_status("isrctn", summary)
    return summary
