"""Retraction Watch database (open via Crossref, mirrored on GitLab) matched by DOI and PMID."""
import csv
import io

from harvest.common import fetch, log

URL = "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv"


def run(conn):
    text = fetch("retractions", "retraction_watch", URL, ext="csv")
    dois, pmids = {}, {}
    for row in csv.DictReader(io.StringIO(text)):
        nature = row.get("RetractionNature", "")
        for d in (row.get("OriginalPaperDOI") or "").lower().split(";"):
            if d.strip() and d.strip() != "unavailable":
                dois[d.strip()] = nature
        p = (row.get("OriginalPaperPubMedID") or "").strip()
        if p and p != "0":
            pmids[p] = nature
    n = 0
    for r in conn.execute("SELECT id, doi, pmid FROM works").fetchall():
        nature = dois.get(r["doi"] or "") or pmids.get(r["pmid"] or "")
        if nature:
            conn.execute("UPDATE works SET is_retracted=? WHERE id=?",
                         (1 if nature == "Retraction" else 2, r["id"]))
            n += 1
    log("retractions", f"{n} works matched ({len(dois)} DOIs in RW)")
    return {"matched": n, "rw_dois": len(dois), "note": "is_retracted: 1=retraction, 2=other notice (EoC/correction)"}
