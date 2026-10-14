"""ClinicalTrials.gov API v2 (spec section 5). Keyless JSON, paginated by nextPageToken."""
import json

import db
from harvest.common import fetch_json, log, params_key, raw_path, save_status

URL = "https://clinicaltrials.gov/api/v2/studies"
QUERIES = ["depersonalization", "depersonalisation", "derealization", "derealisation", "dissociative",
           '"time perception"', '"temporal processing"', "interoception"]


def _row(study, path):
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    design = ps.get("designModule", {})
    spons = ps.get("sponsorCollaboratorsModule", {})
    cl = ps.get("contactsLocationsModule", {})
    nct = ident["nctId"]
    pis = [o.get("name") for o in cl.get("overallOfficials", [])
           if o.get("role") in ("PRINCIPAL_INVESTIGATOR", "STUDY_CHAIR", "STUDY_DIRECTOR") and o.get("name")]
    sites = [", ".join(x for x in (loc.get("facility"), loc.get("city"), loc.get("country")) if x)
             for loc in cl.get("locations", [])]
    end = (status.get("completionDateStruct") or status.get("primaryCompletionDateStruct") or {}).get("date")
    return {
        "id": nct, "registry": "ClinicalTrials.gov",
        "title": ident.get("officialTitle") or ident.get("briefTitle"),
        "conditions": ps.get("conditionsModule", {}).get("conditions", []),
        "interventions": [f"{i.get('type', '')}: {i.get('name', '')}".strip(": ")
                          for i in ps.get("armsInterventionsModule", {}).get("interventions", [])],
        "status": status.get("overallStatus"),
        "phase": ", ".join(design.get("phases", [])) or None,
        "sponsor": spons.get("leadSponsor", {}).get("name"),
        "pi": "; ".join(pis) or None,
        "sites": sites,
        "start": status.get("startDateStruct", {}).get("date"),
        "end": end,
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "has_results": int(bool(study.get("hasResults"))),
        "url": f"https://clinicaltrials.gov/study/{nct}",
        "raw_path": str(path),
    }


def run(conn):
    per_query = {}
    for q in QUERIES:
        token, n, page = None, 0, 0
        while True:
            params = {"query.term": q, "pageSize": 1000, "countTotal": "true", "format": "json"}
            if token:
                params["pageToken"] = token
            key = params_key(f"studies_p{page}", params)
            d = fetch_json("clinicaltrials", key, URL, params)
            path = raw_path("clinicaltrials", key)
            for s in d.get("studies", []):
                db.upsert_trial(conn, _row(s, path), tags=[f"ctgov:{q}"])
                n += 1
            token = d.get("nextPageToken")
            page += 1
            if not token:
                break
        per_query[q] = n
        log("clinicaltrials", f"{q}: {n}")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM trials WHERE registry='ClinicalTrials.gov'").fetchone()[0]
    summary = {"per_query": per_query, "unique_trials": total}
    save_status("clinicaltrials", summary)
    return summary
