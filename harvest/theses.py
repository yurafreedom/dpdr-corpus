"""Theses (spec section 8): keyless, permitted routes only.

Implemented: theses.fr public API (French doctoral theses), DNB SRU (German national
library catalogue, incl. Hochschulschriften). The other aggregators were checked
on 2026-10-13 and are manual-only; see MANUAL.
"""
import re
import xml.etree.ElementTree as ET

import db
from harvest.common import fetch, fetch_json, params_key, log
from harvest.textmatch import families

THESES_FR = "https://theses.fr/api/v1/theses/recherche/"
DNB = "https://services.dnb.de/sru/dnb"
FR_QUERIES = ["dépersonnalisation", "déréalisation", "depersonalization", "depersonalisation",
              '"perception du temps"', '"temps vécu"']
DNB_QUERIES = ["WOE=Depersonalisation", "WOE=Derealisation", "WOE=Depersonalisationsstörung",
               "WOE=Entfremdungserleben", "WOE=Depersonalisationssyndrom",
               'WOE=Zeiterleben and WOE=Psychopathologie', 'WOE=Zeitwahrnehmung and WOE=Depression']
DC = {"dc": "http://purl.org/dc/elements/1.1/", "srw": "http://www.loc.gov/zing/srw/"}

MANUAL = {
    "OATD": "https://oatd.org/oatd/search?q=depersonalization (Cloudflare challenge on every request incl. "
            "robots.txt: no automated access; search manually)",
    "DART-Europe": "Closed down; www.dart-europe.org redirects to "
                   "https://www.ucl.ac.uk/library/dart-europe-e-theses-portal-has-closed-down",
    "NDLTD": "https://search.ndltd.org/search.php?q=depersonalization (HTTP 503 on 2026-10-13; retry manually)",
    "EThOS": "https://ethos.bl.uk/ now serves the British Library Shared Research Repository (Hyku) after the "
             "2023 incident; the EThOS search API is gone. Search manually.",
    "ProQuest PQDT Open": "https://www.proquest.com/pqdtopen (no public API; manual)",
}


def _fr_year(s):
    m = re.search(r"(\d{4})", s or "")
    return int(m.group(1)) if m else None


def run(conn):
    out = {"theses_fr": 0, "dnb": 0, "manual": MANUAL}
    seen = set()
    for q in FR_QUERIES:
        start = 0
        while True:
            p = {"q": q, "debut": start, "nombre": 50}
            key = params_key("search", p)
            d = fetch_json("thesesfr", key, THESES_FR, p)
            rows = d.get("theses", [])
            for t in rows:
                if t["id"] in seen:
                    continue
                subj = [s.get("libelle", "") for s in (t.get("sujetsRameau") or [])] + \
                       [s.get("libelle", "") for s in (t.get("sujets") or []) if isinstance(s, dict)]
                tags = families(t.get("titrePrincipal"), t.get("titreEN"), " ".join(subj), t.get("discipline"))
                if not tags:
                    continue
                seen.add(t["id"])
                auth = (t.get("auteurs") or [{}])[0]
                rec = {
                    "doi": t.get("doi"), "title": t.get("titrePrincipal"),
                    "abstract": t.get("titreEN"),  # the search API has no abstract; English title kept here
                    "year": _fr_year(t.get("dateSoutenance")) or _fr_year(t.get("datePremiereInscriptionDoctorat")),
                    "venue": f"theses.fr — {t.get('etabSoutenanceN') or ''}".strip(" —"),
                    "type": "dissertation", "language": "fr",
                    "oa_url": f"https://theses.fr/{t['id']}",
                    "first_author": f"{auth.get('nom', '')}, {auth.get('prenom', '')}".strip(", ") or None,
                    "keywords": subj or None,
                }
                db.upsert_work(conn, rec, "thesesfr", key, tags + ["thesis"])
                out["theses_fr"] += 1
            start += len(rows)
            if not rows or start >= d.get("totalHits", 0):
                break

    for q in DNB_QUERIES:
        start = 1
        while True:
            p = {"version": "1.1", "operation": "searchRetrieve", "query": q, "recordSchema": "oai_dc",
                 "maximumRecords": 100, "startRecord": start}
            key = params_key("sru", p)
            root = ET.fromstring(fetch("dnb", key, DNB, p, ext="xml"))
            recs = root.findall(".//srw:record", DC)
            for r in recs:
                g = lambda tag: [e.text or "" for e in r.iter(f"{{{DC['dc']}}}{tag}")]  # noqa: E731
                idn = next((e.text for e in r.iter(f"{{{DC['dc']}}}identifier")
                            if (e.get("{http://www.w3.org/2001/XMLSchema-instance}type") or "").endswith("IDN")), None)
                if not idn:
                    continue
                title = (g("title") or [""])[0]
                years = [int(m) for d in g("date") for m in re.findall(r"\d{4}", d)]
                desc = " ".join(g("description") + g("type"))
                is_thesis = bool(re.search(r"Hochschulschrift|Dissertation|Diss\.|Habil", title + " " + desc))
                db.upsert_record(conn, {
                    "id": f"dnb:{idn}", "kind": "thesis" if is_thesis else "book", "source": "dnb",
                    "title": title, "year": years[0] if years else None,
                    "url": f"https://d-nb.info/{idn}",
                    "meta_json": {"creator": g("creator"), "publisher": g("publisher"), "language": g("language"),
                                  "subject": g("subject"), "description": g("description"), "query": q,
                                  "tags": families(title, " ".join(g("subject"))) or ["de"]},
                    "raw_path": f"raw/dnb/{key}.xml"})
                out["dnb"] += 1
            total = int(root.findtext("srw:numberOfRecords", "0", DC))
            start += len(recs)
            if not recs or start > total:
                break
    log("theses", f"theses.fr {out['theses_fr']}, dnb {out['dnb']}")
    return out
