"""Legal full texts for in-scope core works (spec section 3).

Order per work: Europe PMC fullTextXML (OA/in EPMC) -> PMC OA web service PDF
-> OA location from OpenAlex/Unpaywall (works.oa_url, must really be a PDF)
-> CORE (CORE_API_KEY). Stores fulltext/<work_id>.xml|pdf plus an extracted
.txt; works.fulltext_path points at the .txt. Works with no legal copy are
listed later in exports/request_from_author.csv.
"""
import json
import re
import time
import xml.etree.ElementTree as ET

import requests

import config
from harvest.common import _pace, _session, fetch_json, load_status, save_status, log
from harvest.scope import CORE_SQL

EPMC_FT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{}/fullTextXML"
PMC_OA = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
CORE_API = "https://api.core.ac.uk/v3/search/works"
MAX_PDF = 40 * 1024 * 1024


def _get(source, url, **kw):
    _pace(source)
    try:
        r = _session.get(url, timeout=(10, 40), allow_redirects=True, **kw)
    except requests.RequestException:
        return None
    return r if r.status_code == 200 else None


def xml_text(path):
    root = ET.fromstring(path.read_bytes())
    parts = []
    for tag in ("article-title", "abstract", "body"):
        for el in root.iter(tag):
            parts.append(" ".join(" ".join(el.itertext()).split()))
    return "\n\n".join(parts)


def pdf_text(path):
    from pypdf import PdfReader
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    except Exception:
        return ""


def save_pdf(r, out):
    body = r.content
    if not body.startswith(b"%PDF") or len(body) > MAX_PDF:
        return False
    out.write_bytes(body)
    return True


def try_epmc(wid, pmcid):
    r = _get("europepmc", EPMC_FT.format(pmcid))
    if r is None or not r.content.strip().startswith(b"<"):
        return None
    out = config.FULLTEXT / f"{wid}.xml"
    out.write_bytes(r.content)
    return out, "epmc_xml"


def try_pmc_oa(wid, pmcid):
    r = _get("pubmed", PMC_OA, params={"id": pmcid})
    if r is None:
        return None
    m = re.search(r'format="pdf" [^>]*href="([^"]+)"', r.text)
    if not m:
        return None
    url = m.group(1).replace("ftp://", "https://")
    p = _get("pubmed", url)
    out = config.FULLTEXT / f"{wid}.pdf"
    if p is not None and save_pdf(p, out):
        return out, "pmc_oa_pdf"
    return None


def try_oa_url(wid, url):
    if not url:
        return None
    r = _get("oa_pdf", url, headers={"Accept": "application/pdf,*/*;q=0.5"})
    out = config.FULLTEXT / f"{wid}.pdf"
    if r is not None and save_pdf(r, out):
        return out, "oa_location_pdf"
    return None


def try_core(wid, doi):
    if not config.KEYS["core"] or not doi:
        return None
    d = fetch_json("core", f"doi_{doi}", CORE_API, {"q": f'doi:"{doi}"', "limit": 1},
                   headers={"Authorization": f"Bearer {config.KEYS['core']}"}, ok_statuses=(200, 404))
    res = d.get("results") or []
    url = res[0].get("downloadUrl") if res else None
    return try_oa_url(wid, url) if url else None


def run(conn):
    config.FULLTEXT.mkdir(exist_ok=True)
    st = load_status("fulltext")
    tried = set(st.get("tried", []))
    # URLs already attempted, so a work tried before is retried only for links it has not seen
    # (e.g. new Unpaywall PDF locations). Older runs recorded ids only; their oa_url counts as tried.
    tried_urls = set(st.get("tried_urls", []))
    has_meta = conn.execute("SELECT name FROM sqlite_master WHERE name='epmc_meta'").fetchone()
    has_upw = "unpaywall_pdfs" in [c[1] for c in conn.execute("PRAGMA table_info(works)")]
    rows = conn.execute(f"""
        SELECT w.id, w.pmcid, w.doi, w.oa_url, w.oa_status,
               {'w.unpaywall_pdfs' if has_upw else 'NULL'} AS upw,
               {'(SELECT MAX(in_epmc OR is_oa) FROM epmc_meta m WHERE m.work_id=w.id)' if has_meta else '0'} AS epmc_ok
        FROM works w WHERE {CORE_SQL} AND w.fulltext_path IS NULL""").fetchall()

    def urls(r):
        out = [r["oa_url"]] if r["oa_url"] else []
        out += [u for u in json.loads(r["upw"] or "[]") if u not in out]
        return out

    def fresh(r):
        return [u for u in urls(r) if u not in tried_urls and not (r["id"] in tried and u == r["oa_url"])]

    todo = [r for r in rows if (r["id"] not in tried and (r["pmcid"] or urls(r) or config.KEYS["core"]))
            or (r["id"] in tried and fresh(r))]
    # Cheapest, most reliable routes first: Europe PMC XML, then direct PDF links.
    todo.sort(key=lambda r: (not r["pmcid"], not (r["oa_url"] or "").lower().split("?")[0].endswith(".pdf")))
    log("fulltext", f"{len(todo)} candidate works ({len(rows)} without full text)")
    by_route = {}
    t0 = time.time()
    for n, r in enumerate(todo):
        got = None
        first_time = r["id"] not in tried
        if r["pmcid"] and first_time:
            got = try_epmc(r["id"], r["pmcid"]) or try_pmc_oa(r["id"], r["pmcid"])
        for u in fresh(r):
            if got:
                break
            got = try_oa_url(r["id"], u)
            tried_urls.add(u)
        if not got and first_time:
            got = try_core(r["id"], r["doi"])
        if got:
            path, route = got
            text = xml_text(path) if path.suffix == ".xml" else pdf_text(path)
            txt = config.FULLTEXT / f"{r['id']}.txt"
            txt.write_text(text)
            conn.execute("UPDATE works SET fulltext_path=? WHERE id=?", (str(txt.relative_to(config.ROOT)), r["id"]))
            conn.commit()  # never hold the write lock across slow downloads
            by_route[route] = by_route.get(route, 0) + 1
        tried.add(r["id"])
        if n % 50 == 0:
            conn.commit()
            st["tried"], st["tried_urls"] = sorted(tried), sorted(tried_urls)
            save_status("fulltext", st)
            log("fulltext", f"{n}/{len(todo)} {by_route} {int(time.time() - t0)}s")
    conn.commit()
    st["tried"], st["tried_urls"] = sorted(tried), sorted(tried_urls)
    save_status("fulltext", st)
    return {"attempted": len(todo), "by_route": by_route}
