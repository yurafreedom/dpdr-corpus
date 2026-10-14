"""PubMed E-utilities: esearch per query family -> PMIDs, efetch XML in batches."""
import hashlib
import xml.etree.ElementTree as ET

import config
import db
from harvest import queries as Q
from harvest.common import fetch, fetch_json, load_status, save_status, log
from harvest.families import families, family_tag

EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
BATCH = 200


def _p(**kw):
    if config.KEYS["ncbi"]:
        kw["api_key"] = config.KEYS["ncbi"]
    return kw


def esearch(term, key, mindate="1000", maxdate="3000"):
    """All PMIDs for a term, splitting the date range when a slice exceeds 9,999."""
    p = _p(db="pubmed", term=term, retmax=9999, retmode="json", datetype="pdat",
           mindate=mindate, maxdate=maxdate)
    d = fetch_json("pubmed", f"esearch_{key}_{mindate}_{maxdate}", EU + "esearch.fcgi", p)
    r = d["esearchresult"]
    count = int(r["count"])
    if count <= 9999:
        return r["idlist"], count
    lo, hi = int(mindate[:4]), int(maxdate[:4])
    if lo < 1900:
        lo = 1800
    if hi > config.YEAR_TO:
        hi = config.YEAR_TO
    if lo >= hi:
        raise RuntimeError(f"PubMed slice {mindate}-{maxdate} still has {count} records")
    mid = (lo + hi) // 2
    a, _ = esearch(term, key, str(lo), str(mid))
    b, _ = esearch(term, key, str(mid + 1), str(hi))
    return a + b, count


def efetch(pmids):
    pmids = sorted(pmids, key=int)
    h = hashlib.sha1(",".join(pmids).encode()).hexdigest()[:12]
    key = f"efetch_{pmids[0]}_{len(pmids)}_{h}"
    fetch("pubmed", key, EU + "efetch.fcgi",
          _p(db="pubmed", id=",".join(pmids), rettype="abstract", retmode="xml"), ext="xml")
    return key


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else None


def parse_article(art):
    mc = art.find("MedlineCitation")
    a = mc.find("Article")
    pmid = mc.findtext("PMID")
    ids = {i.get("IdType"): (i.text or "").strip() for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
    parts = []
    for at in a.findall("Abstract/AbstractText"):
        t = _text(at)
        if t:
            parts.append(f"{at.get('Label')}: {t}" if at.get("Label") else t)
    for ot in mc.findall("OtherAbstract/AbstractText"):
        if not parts and _text(ot):
            parts.append(_text(ot))
    year = a.findtext("Journal/JournalIssue/PubDate/Year") or (a.findtext("Journal/JournalIssue/PubDate/MedlineDate") or "")[:4]
    if not (year or "").isdigit():
        year = art.findtext("PubmedData/History/PubMedPubDate[@PubStatus='pubmed']/Year")
    authors = []
    for au in a.findall("AuthorList/Author"):
        name = au.findtext("CollectiveName") or ", ".join(x for x in (au.findtext("LastName"), au.findtext("ForeName")) if x)
        orcid = next((i.text for i in au.findall("Identifier") if i.get("Source") == "ORCID"), None)
        authors.append({"name": name, "orcid": orcid,
                        "affiliation": au.findtext("AffiliationInfo/Affiliation")})
    pts = [p.text for p in a.findall("PublicationTypeList/PublicationType")]
    return {
        "pmid": pmid, "doi": ids.get("doi"), "pmcid": ids.get("pmc"),
        "title": _text(a.find("ArticleTitle")) or _text(a.find("VernacularTitle")),
        "abstract": "\n".join(parts) or None,
        "year": int(year) if year and str(year).isdigit() else None,
        "venue": a.findtext("Journal/Title"),
        "language": a.findtext("Language"),
        "pub_types": pts,
        "is_review": int(any(p in ("Review", "Systematic Review", "Meta-Analysis") for p in pts)),
        "type": "review" if any("Review" in p for p in pts) else "article",
        "mesh": [m.findtext("DescriptorName") for m in mc.findall("MeshHeadingList/MeshHeading")],
        "keywords": [_text(k) for k in mc.findall("KeywordList/Keyword") if _text(k)],
        "first_author": authors[0]["name"] if authors else None,
        "_authors": authors,
    }


def load_batch(conn, key, tags_by_pmid):
    root = ET.fromstring((config.RAW / "pubmed" / f"{key}.xml").read_text())
    n = 0
    for art in root.findall("PubmedArticle"):
        rec = parse_article(art)
        wid = db.upsert_work(conn, rec, "pubmed", key, tags_by_pmid.get(rec["pmid"], ()))
        for i, au in enumerate(rec["_authors"]):
            db.set_authorship(conn, wid, i, None, None, rec["year"], au["name"])
        n += 1
    return n


def run(conn):
    st = load_status("pubmed")
    tags_by_pmid, counts = {}, {}
    for key, q in families():
        term = Q.pubmed(q)
        ids, count = esearch(term, key)
        counts[key] = count
        for pid in ids:
            tags_by_pmid.setdefault(pid, set()).add(family_tag(key))
        log("pubmed", f"{key}: {count} hits")
    fetched = set(st.get("fetched", []))
    todo = sorted(set(tags_by_pmid) - fetched, key=int)
    batches = st.get("batches", [])
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        batches.append(efetch(chunk))
        fetched |= set(chunk)
        save_status("pubmed", {"fetched": sorted(fetched), "batches": batches, "counts": counts})
        if (i // BATCH) % 10 == 0:
            log("pubmed", f"efetch {i + len(chunk)}/{len(todo)}")
    n = 0
    for key in batches:
        n += load_batch(conn, key, tags_by_pmid)
    conn.commit()
    save_status("pubmed", {"fetched": sorted(fetched), "batches": batches, "counts": counts, "done": True})
    return {"family_counts": counts, "unique_pmids": len(tags_by_pmid), "articles_loaded": n}
