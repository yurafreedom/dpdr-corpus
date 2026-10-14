"""CORDIS (EU framework programmes) via the public EURIO SPARQL endpoint (spec section 6).

cordis.europa.eu/robots.txt disallows /search and /data/, so neither the JSON
search route nor the bulk project dumps are used. /datalab/sparql is allowed
and keyless. CORDIS does not publish PI names; `institution` is the coordinator
and `pi` stays empty.
"""
import json

import db
from harvest.common import fetch_json, log, params_key, raw_path, save_status

URL = "https://cordis.europa.eu/datalab/sparql"
HDR = {"Accept": "application/sparql-results+json"}
# XPath regex has no word boundary, so "mental" is spelled out to avoid fundamental/experimental.
PSY = ("trauma|ptsd|psychiatr|psychopath|psychotherap|mental health|mental disorder|mental illness|"
       "depersonali|psychosis|psychotic|ketamine|consciousness")

# label -> (regex on title/abstract, optional second regex that must also match)
QUERIES = {
    "depersonalization": ("depersonali|dereali", None),
    "dissociation(psych)": ("dissociat", PSY),
    "time perception": ("time perception|perception of time", None),
    "temporal processing": ("temporal processing", None),
    "interoception": ("interocepti", None),
}

TEMPLATE = """PREFIX eurio: <http://data.europa.eu/s66#>
SELECT ?p ?id ?title ?abstract ?start ?end ?status ?doi
  (SAMPLE(?acr) AS ?acronym) (SAMPLE(?coordName) AS ?coordinator) (SAMPLE(?coordCountry) AS ?country)
  (SAMPLE(?amt) AS ?ec_contribution) (SAMPLE(?cur) AS ?currency)
WHERE {
  ?p a eurio:Project ; eurio:title ?title .
  OPTIONAL { ?p eurio:abstract ?abstract }
  FILTER(%FILTER%)
  OPTIONAL { ?p eurio:identifier ?id }
  OPTIONAL { ?p eurio:startDate ?start }
  OPTIONAL { ?p eurio:endDate ?end }
  OPTIONAL { ?p eurio:projectStatus ?status }
  OPTIONAL { ?p eurio:doi ?doi }
  OPTIONAL { ?p eurio:hasAcronym/eurio:shortForm ?acr }
  OPTIONAL { ?p eurio:hasInvolvedParty ?r . ?r eurio:roleLabel "coordinator" ; eurio:isRoleOf ?o .
             ?o eurio:legalName ?coordName .
             OPTIONAL { ?o eurio:hasSite/eurio:hasAddress/eurio:addressCountry ?coordCountry } }
  OPTIONAL { ?p eurio:isFundedBy/eurio:hasFundingAmount ?m . ?m eurio:value ?amt ; eurio:currency ?cur }
}
GROUP BY ?p ?id ?title ?abstract ?start ?end ?status ?doi
LIMIT 10000"""


def _filter(rx, rx2):
    def m(r):
        return f'(REGEX(STR(?title), "{r}", "i") || REGEX(STR(?abstract), "{r}", "i"))'
    return m(rx) + (f" && {m(rx2)}" if rx2 else "")


def run(conn):
    per_query, total = {}, set()
    for label, (rx, rx2) in QUERIES.items():
        params = {"query": TEMPLATE.replace("%FILTER%", _filter(rx, rx2))}
        key = params_key(f"sparql_{label.split('(')[0].replace(' ', '_')}", params)
        d = fetch_json("cordis", key, URL, params, headers=HDR)
        path = raw_path("cordis", key)
        rows = d["results"]["bindings"]
        for b in rows:
            v = {k: x["value"] for k, x in b.items()}
            gid = v.get("id") or v["p"].rsplit("/", 1)[-1]
            total.add(gid)
            try:
                amount = float(v["ec_contribution"]) if v.get("ec_contribution") else None
            except ValueError:
                amount = None
            title = v.get("title")
            if v.get("acronym"):
                title = f"{v['acronym']}: {title}"
            db.upsert_grant(conn, {
                "id": f"CORDIS:{gid}", "funder": "European Commission (CORDIS)",
                "title": title, "pi": None,
                "institution": ", ".join(x for x in (v.get("coordinator"), v.get("country")) if x) or None,
                "start": v.get("start"), "end": v.get("end"),
                "amount": amount or None, "currency": v.get("currency") or "EUR",
                "abstract": v.get("abstract"),
                "url": f"https://cordis.europa.eu/project/id/{gid}",
                "raw_path": str(path),
            }, tags=[f"cordis:{label}"])
        per_query[label] = len(rows)
        log("cordis", f"{label}: {len(rows)}")
    conn.commit()
    summary = {"per_query": per_query, "unique_projects": len(total),
               "note": "PI names are not published by CORDIS; institution = coordinator"}
    save_status("cordis", summary)
    return summary
