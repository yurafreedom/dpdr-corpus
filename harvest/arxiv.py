"""arXiv API: time-perception models and DPDR mentions (spec section 4). 3 s pacing."""
import re
import xml.etree.ElementTree as ET

import db
from harvest.common import fetch, params_key, log
from harvest.textmatch import families

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/"}
QUERIES = ['all:"time perception"', 'all:"temporal perception"', 'all:"interval timing"',
           'all:"subjective time"', 'all:depersonalization', 'all:depersonalisation', 'all:derealization']
PAGE = 200


def run(conn):
    out = {"by_query": {}, "works": 0}
    seen = set()
    for q in QUERIES:
        start, n = 0, 0
        while True:
            p = {"search_query": q, "start": start, "max_results": PAGE,
                 "sortBy": "submittedDate", "sortOrder": "descending"}
            key = params_key("query", p)
            root = ET.fromstring(fetch("arxiv", key, API, p, ext="xml"))
            total = int(root.findtext("os:totalResults", "0", NS))
            entries = root.findall("a:entry", NS)
            for e in entries:
                aid = e.findtext("a:id", "", NS).rsplit("/abs/", 1)[-1]
                aid_base = re.sub(r"v\d+$", "", aid)
                title = " ".join(e.findtext("a:title", "", NS).split())
                abstract = " ".join(e.findtext("a:summary", "", NS).split())
                cats = [c.get("term") for c in e.findall("a:category", NS)]
                tags = families(title, abstract) or ["time_model"]
                authors = [a.findtext("a:name", "", NS) for a in e.findall("a:author", NS)]
                pdf = next((l.get("href") for l in e.findall("a:link", NS) if l.get("title") == "pdf"), None)
                rec = {
                    "doi": e.findtext("arxiv:doi", None, NS) or f"10.48550/arXiv.{aid_base}",
                    "title": title, "abstract": abstract,
                    "year": int(e.findtext("a:published", "0000", NS)[:4]) or None,
                    "venue": "arXiv", "type": "preprint", "language": "en", "oa_status": "green",
                    "oa_url": pdf, "first_author": authors[0] if authors else None, "keywords": cats,
                }
                db.upsert_work(conn, rec, "arxiv", key, tags)
                seen.add(aid_base)
                n += 1
            start += len(entries)
            if not entries or start >= total:
                break
        out["by_query"][q] = n
        log("arxiv", f"{q}: {n}")
    out["works"] = len(seen)
    return out
