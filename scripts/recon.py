"""Stage 0 reconnaissance: count-only queries for the section 1.1 terms.

Counts per term and per year for PubMed, Europe PMC and OpenAlex, before and
after the server-side approximation of the burnout exclusion. Then fetches a
sample of real records (Europe PMC, with abstracts and keywords) and applies
the exact local filter so the filter can be eyeballed.

Writes exports/recon.md and status/recon.json. Costs ~100 OpenAlex credits.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from harvest import queries as Q  # noqa: E402
from harvest.common import fetch_json, params_key, save_status, log  # noqa: E402
from harvest.filters import exclusion_reason, naive_reason  # noqa: E402

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/searchPOST"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
OA = "https://api.openalex.org/works"
YEARS = list(range(config.YEAR_FROM, config.YEAR_TO + 1))


def pubmed_count(term, mindate=None, maxdate=None):
    p = {"db": "pubmed", "term": term, "rettype": "count", "retmode": "json"}
    if mindate:
        p |= {"datetype": "pdat", "mindate": mindate, "maxdate": maxdate}
    if config.KEYS["ncbi"]:
        p["api_key"] = config.KEYS["ncbi"]
    d = fetch_json("pubmed", params_key("recon_count", p), ESEARCH, p)
    return int(d["esearchresult"]["count"])


def epmc_count(query):
    if query is None:
        return None
    return _epmc_post({"query": query, "format": "json", "pageSize": 1, "resultType": "idlist"})["hitCount"]


def _epmc_post(data, prefix="recon"):
    from harvest.common import raw_path, _pace, _session
    path = raw_path("europepmc", params_key(prefix, data))
    if path.exists():
        return json.loads(path.read_text())
    _pace("europepmc")
    r = _session.post(EPMC, data=data, timeout=90)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text)
    return r.json()


def oa_count(value, group_by=None):
    p = {"filter": f"title_and_abstract.search:{value}", "per-page": 1, "select": "id"}
    if group_by:
        p["group_by"] = group_by
        p["per-page"] = 200  # per-page also caps the number of groups returned
        del p["select"]
    if config.KEYS["openalex"]:
        p["api_key"] = config.KEYS["openalex"]
    d = fetch_json("openalex", params_key("recon_count", p), OA, p, cost_usd=0.001)
    if group_by:
        return {int(g["key"]): g["count"] for g in d["group_by"] if str(g["key"]).isdigit()}
    return d["meta"]["count"]


def per_year_pubmed(term):
    out = {"<1990": pubmed_count(term, "1000", "1989")}
    for y in YEARS:
        out[y] = pubmed_count(term, str(y), str(y))
    return out


def per_year_epmc(query):
    out = {"<1990": _epmc_post({"query": f"({query}) AND PUB_YEAR:[1000 TO 1989]", "format": "json",
                                "pageSize": 1, "resultType": "idlist"})["hitCount"]}
    for y in YEARS:
        out[y] = _epmc_post({"query": f"({query}) AND PUB_YEAR:{y}", "format": "json",
                             "pageSize": 1, "resultType": "idlist"})["hitCount"]
    return out


def per_year_oa(value):
    g = oa_count(value, group_by="publication_year")
    out = {"<1990": sum(c for y, c in g.items() if y < 1990)}
    for y in YEARS:
        out[y] = g.get(y, 0)
    return out


def sample_records(n_pages=4):
    """Pull ~4000 core-union records from Europe PMC with abstracts + keywords."""
    q = Q.europepmc(config.DPDR_CORE_UNION)
    recs, cursor = [], "*"
    for _ in range(n_pages):
        d = _epmc_post({"query": q, "format": "json", "pageSize": 1000, "resultType": "core",
                        "cursorMark": cursor, "sort": "P_PDATE_D desc"}, prefix="recon_sample")
        for r in d["resultList"]["result"]:
            kws = r.get("keywordList", {}).get("keyword", [])
            recs.append({"id": r.get("pmid") or r.get("id"), "year": r.get("pubYear"),
                         "title": r.get("title", ""), "abstract": r.get("abstractText", ""),
                         "keywords": kws})
        nxt = d.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    return recs


def main():
    res = {"terms": [], "union": {}}
    log("recon", "per-term counts")
    for label, query, core in config.DPDR_TERMS:
        row = {"term": label, "core": core,
               "pubmed": pubmed_count(Q.pubmed(query)),
               "pubmed_ex": pubmed_count(Q.pubmed(query, True)),
               "epmc": epmc_count(Q.europepmc(query)),
               "epmc_ex": epmc_count(Q.europepmc(query, True))}
        oa_q = Q.openalex(query)
        row["openalex"] = oa_count(oa_q) if oa_q else None
        res["terms"].append(row)
        log("recon", f"{label}: {row}")

    log("recon", "per-year union counts")
    U = config.DPDR_CORE_UNION
    res["union"] = {
        "pubmed": per_year_pubmed(Q.pubmed(U)),
        "pubmed_ex": per_year_pubmed(Q.pubmed(U, True)),
        "epmc": per_year_epmc(Q.europepmc(U)),
        "epmc_ex": per_year_epmc(Q.europepmc(U, True)),
        "openalex": per_year_oa(Q.openalex(U)),
        "openalex_ex": per_year_oa(Q.openalex(U, True)),
    }

    log("recon", "sampling records for exact local filter")
    recs = sample_records()
    for r in recs:
        r["reason"] = exclusion_reason(r["title"], r["abstract"], r["keywords"])
        r["naive"] = naive_reason(r["title"], r["abstract"], r["keywords"])
    excl = [r for r in recs if r["reason"]]
    incl = [r for r in recs if not r["reason"]]
    naive_only = [r for r in recs if r["naive"] and not r["reason"]]
    random.seed(13)
    res["sample"] = {
        "n": len(recs), "excluded": len(excl), "naive_excluded": len(recs) - len(incl) + len(naive_only),
        "naive_only_examples": [(r["year"], r["title"], r["naive"]) for r in random.sample(naive_only, min(8, len(naive_only)))],
        "excluded_examples": [(r["year"], r["title"], r["reason"]) for r in random.sample(excl, min(20, len(excl)))],
        "included_examples": [(r["year"], r["title"]) for r in random.sample(incl, min(20, len(incl)))],
    }
    save_status("recon", res)
    write_report(res)
    log("recon", "done -> exports/recon.md")


def write_report(res):
    L = ["# Recon: section 1.1 counts", "",
         "Server-side `_ex` columns approximate the burnout exclusion with a NOT clause on title/abstract "
         "(PubMed `[tiab]`, Europe PMC TITLE/ABSTRACT/KW, OpenAlex title_and_abstract). Exact local "
         "filter results are in the sample section.", "",
         "## Per term", "",
         "| term | core | PubMed | PubMed −burnout | Europe PMC | EPMC −burnout | OpenAlex |",
         "|---|---|---:|---:|---:|---:|---:|"]
    for r in res["terms"]:
        L.append(f"| {r['term']} | {'y' if r['core'] else 'n'} | {r['pubmed']} | {r['pubmed_ex']} | "
                 f"{r['epmc'] if r['epmc'] is not None else 'n/a (MeSH)'} | {r['epmc_ex'] if r['epmc_ex'] is not None else 'n/a'} | {r['openalex'] if r['openalex'] is not None else 'n/a (MeSH)'} |")
    u = res["union"]
    cols = ["pubmed", "pubmed_ex", "epmc", "epmc_ex", "openalex", "openalex_ex"]
    L += ["", "## Core union per year", "",
          "| year | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    keys = list(u["pubmed"].keys())
    for k in keys:
        L.append(f"| {k} | " + " | ".join(str(u[c].get(k, u[c].get(str(k), 0))) for c in cols) + " |")
    L.append("| **total** | " + " | ".join(str(sum(u[c].values())) for c in cols) + " |")
    s = res["sample"]
    L += ["", f"## Exact local filter on a sample of {s['n']} Europe PMC records (newest first)", "",
          f"Excluded: {s['excluded']} ({100 * s['excluded'] / max(s['n'], 1):.1f}%). "
          f"A naive case-insensitive substring filter would exclude {s['naive_excluded']}.", "",
          "### 20 excluded", ""]
    L += [f"{i}. ({y}) {t} — `{', '.join(r)}`" for i, (y, t, r) in enumerate(s["excluded_examples"], 1)]
    L += ["", "### 20 included", ""]
    L += [f"{i}. ({y}) {t}" for i, (y, t) in enumerate(s["included_examples"], 1)]
    L += ["", "### Only the naive substring filter would exclude these", ""]
    L += [f"- ({y}) {t} — `{', '.join(r)}`" for y, t, r in s["naive_only_examples"]]
    (config.EXPORTS / "recon.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
