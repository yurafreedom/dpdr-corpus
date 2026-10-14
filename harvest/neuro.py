"""Neuro data and meta-maps (spec section 11). Near-empty results are the expected finding.

- OpenNeuro: GraphQL advancedSearch(keywords) (the plain `search(q)` resolver returns null).
- NeuroVault: the API ignores ?name= filters, so all collections are walked (18 pages
  of 1000) and filtered locally on name/description/journal.
- NeuroQuery / NeuroSynth: whether the term exists in each vocabulary.
"""
import json
import re

import requests

import db
from harvest.common import fetch, fetch_json, log
from harvest.textmatch import DPDR_RX, TIME_RX

OPENNEURO = "https://openneuro.org/crn/graphql"
NEUROVAULT = "https://neurovault.org/api/collections/"
KEYWORDS = ["depersonalization", "depersonalisation", "derealization", "dissociation", "dissociative",
            "time perception", "interoception"]
TERMS = ["depersonalization", "derealization", "dissociation", "dissociative", "time perception",
         "interoception", "unreality"]
# Clinical dissociation only; bare "dissociat" mostly hits cognitive "double dissociation" designs.
DISSOC_RX = re.compile(r"\bdissociative\b|dissociation (scale|symptoms?|disorders?)|depersonali", re.I)

GQL = """{ advancedSearch(query:{keywords:%s}, first: 100, allDatasets: false) {
  pageInfo { count } edges { node { id latestSnapshot { tag description { Name DatasetDOI }
  summary { subjects modalities tasks } } } } } }"""


def run(conn):
    out = {"openneuro": {}, "neurovault_matches": 0, "neurovault_scanned": 0,
           "neuroquery": {}, "neurosynth": {}}
    for kw in KEYWORDS:
        body = {"query": GQL % json.dumps([kw])}
        d = fetch_json("openneuro", f"advsearch_{kw}", OPENNEURO, method="POST", json_body=body)
        edges = ((d.get("data") or {}).get("advancedSearch") or {}).get("edges") or []
        kept = 0
        for e in edges:
            node = (e or {}).get("node") or {}
            snap = node.get("latestSnapshot") or {}
            name = (snap.get("description") or {}).get("Name") or ""
            if not node.get("id"):
                continue
            # Keyword search is loose; record whether the dataset name itself mentions the term.
            relevant = bool(DPDR_RX.search(name) or TIME_RX.search(name) or DISSOC_RX.search(name))
            db.upsert_record(conn, {
                "id": f"openneuro:{node['id']}", "kind": "dataset", "source": "openneuro", "title": name,
                "year": None, "url": f"https://openneuro.org/datasets/{node['id']}",
                "meta_json": {"keyword": kw, "name_mentions_term": relevant, "snapshot": snap},
                "raw_path": f"raw/openneuro/advsearch_{kw}.json"})
            kept += relevant
        out["openneuro"][kw] = {"returned": len(edges), "name_mentions_term": kept}

    offset = 0
    while True:
        d = fetch_json("neurovault", f"collections_{offset}", NEUROVAULT, {"limit": 1000, "offset": offset})
        res = d.get("results", [])
        for c in res:
            out["neurovault_scanned"] += 1
            text = " ".join(str(c.get(k) or "") for k in ("name", "description", "journal_name", "group_description"))
            if DPDR_RX.search(text) or DISSOC_RX.search(text) or TIME_RX.search(text):
                db.upsert_record(conn, {
                    "id": f"neurovault:{c['id']}", "kind": "dataset", "source": "neurovault",
                    "title": c.get("name"), "year": int(c["add_date"][:4]) if c.get("add_date") else None,
                    "url": f"https://neurovault.org/collections/{c['id']}/",
                    "meta_json": {"doi": c.get("DOI"), "paper_url": c.get("paper_url"),
                                  "n_images": c.get("number_of_images"),
                                  "matches": sorted({m.group(0).lower() for rx in (DPDR_RX, DISSOC_RX, TIME_RX)
                                                     for m in rx.finditer(text)})},
                    "raw_path": f"raw/neurovault/collections_{offset}.json"})
                out["neurovault_matches"] += 1
        offset += len(res)
        if not res or not d.get("next"):
            break

    for t in TERMS:
        try:
            html = fetch("neuroquery", f"query_{t}", "https://neuroquery.org/query", {"text": t},
                         ext="html", max_tries=2)
            m = re.search(r'%s"[^>]*data-in_model="(true|false)"' % re.escape(t), html)
            out["neuroquery"][t] = (m.group(1) == "true") if m else None
        except requests.RequestException as e:
            # neuroquery.org serves an incomplete certificate chain that certifi rejects (2026-10-13);
            # verification is not disabled, the term is recorded as unchecked.
            out["neuroquery"][t] = f"error: {type(e).__name__}"
        slug = t.replace(" ", "%20")
        try:
            html = fetch("neurosynth", f"term_{t}", f"https://neurosynth.org/analyses/terms/{slug}/",
                         ext="html", ok_statuses=(200, 404), max_tries=2)
            out["neurosynth"][t] = "not found" not in html.lower()
        except requests.HTTPError as e:
            out["neurosynth"][t] = f"error: {str(e)[:80]}"
        db.upsert_record(conn, {"id": f"neuroterm:{t}", "kind": "neuro_term", "source": "neuroquery+neurosynth",
                                "title": t, "year": None, "url": f"https://neuroquery.org/query?text={slug}",
                                "meta_json": {"neuroquery_in_model": out["neuroquery"][t],
                                              "neurosynth_term_exists": out["neurosynth"][t]},
                                "raw_path": f"raw/neuroquery/query_{t}.html"})
    log("neuro", json.dumps(out)[:400])
    return out
