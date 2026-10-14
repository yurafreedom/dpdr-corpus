"""CyberLeninka: Russian-language articles (spec sections 1.3 and 2). Metadata + PDF link only.

No official API. The site's own JSON search endpoint is used only if robots.txt
allows it, with >= 2 s between uncached calls. On 2026-10-13 cyberleninka.ru did
not resolve from this network (DNS SERVFAIL), so the module skips cleanly.
"""
import re
import time
from urllib.robotparser import RobotFileParser

import requests

import db
from harvest.common import SkipModule, fetch, fetch_json, params_key, raw_path, log
from harvest.textmatch import families

BASE = "https://cyberleninka.ru"
TERMS = ["деперсонализация", "дереализация", "синдром деперсонализации-дереализации",
         "психическая анестезия", "деперсонализации", "дереализации", "anaesthesia psychica dolorosa"]
PAGE = 100
MAX_PER_TERM = 1000


def run(conn):
    try:
        robots = fetch("cyberleninka", "robots", f"{BASE}/robots.txt", ext="txt", max_tries=2)
    except requests.RequestException as e:
        raise SkipModule(f"cyberleninka.ru unreachable from this network: {str(e)[:150]}")
    rp = RobotFileParser()
    rp.parse(robots.splitlines())
    if not rp.can_fetch("*", f"{BASE}/api/search"):
        raise SkipModule("robots.txt disallows /api/search")
    out = {"by_term": {}, "works": 0}
    for term in TERMS:
        n = 0
        for start in range(0, MAX_PER_TERM, PAGE):
            body = {"mode": "articles", "q": term, "size": PAGE, "from": start}
            key = params_key("search", body)
            if not raw_path("cyberleninka", key).exists():
                time.sleep(2.0)
            d = fetch_json("cyberleninka", key, f"{BASE}/api/search", method="POST", json_body=body)
            arts = d.get("articles", [])
            for a in arts:
                title = re.sub(r"<[^>]+>", "", a.get("name", ""))
                ann = re.sub(r"<[^>]+>", "", a.get("annotation", "") or "")
                link = a.get("link", "")
                rec = {"title": title, "abstract": ann or None, "year": a.get("year"),
                       "venue": a.get("journal"), "type": "article", "language": "ru",
                       "oa_status": "gold", "oa_url": f"{BASE}{link}/pdf" if link else None,
                       "first_author": (a.get("authors") or [None])[0]}
                db.upsert_work(conn, rec, "cyberleninka", key, families(title, ann) + ["ru"])
                n += 1
            if len(arts) < PAGE or start + PAGE >= d.get("found", 0):
                break
        out["by_term"][term] = n
        out["works"] += n
    log("cyberleninka", str(out))
    return out
