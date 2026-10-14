"""BASE (Bielefeld Academic Search Engine) HTTP API (spec section 2).

Access is granted per registered IP; set BASE_API_KEY (any non-empty value once
your IP is whitelisted, see https://www.base-search.net/about/en/contact.php).
"""
import config
import db
from harvest.common import SkipModule, fetch_json, params_key, log
from harvest.textmatch import core_phrases, families

API = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"
PAGE = 125
MAX_OFFSET = 1000  # BASE caps offset per query; terms are queried separately to stay under it


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def run(conn):
    if not config.KEYS["base"]:
        raise SkipModule("BASE_API_KEY not set (BASE requires IP registration)")
    out = {"records": 0, "by_term": {}}
    for term in core_phrases():
        q = f'dctitle:"{term}" OR dcdescription:"{term}" OR dcsubject:"{term}"'
        n, offset = 0, 0
        while offset < MAX_OFFSET:
            p = {"func": "PerformSearch", "query": q, "format": "json", "hits": PAGE, "offset": offset}
            ck = params_key("search", p)
            d = fetch_json("base", ck, API, p)
            docs = (d.get("response") or {}).get("docs") or []
            for r in docs:
                title, desc = _first(r.get("dctitle")), _first(r.get("dcdescription"))
                rec = {"doi": _first(r.get("dcdoi")), "title": title, "abstract": desc,
                       "year": int(r["dcyear"]) if str(r.get("dcyear", "")).isdigit() else None,
                       "venue": _first(r.get("dcsource")) or _first(r.get("dcpublisher")),
                       "type": _first(r.get("dctypenorm")), "language": _first(r.get("dclang")),
                       "oa_status": "green" if str(r.get("dcoa")) == "1" else None,
                       "oa_url": _first(r.get("dclink")), "first_author": _first(r.get("dccreator")),
                       "keywords": r.get("dcsubject")}
                db.upsert_work(conn, rec, "base", ck, families(title, desc) or ["dpdr"])
                n += 1
            offset += len(docs)
            if not docs or offset >= (d.get("response") or {}).get("numFound", 0):
                break
        out["by_term"][term] = n
        out["records"] += n
    log("base", str(out))
    return out
