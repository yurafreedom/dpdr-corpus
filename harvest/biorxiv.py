"""bioRxiv / medRxiv: walk the last 90 days by date and keep DPDR/time matches (spec section 4).

OpenAlex already indexes older preprints; this pass only catches the newest
weeks. Windows end yesterday so a cached window is complete.
"""
import datetime as dt
import json

import db
from harvest.common import fetch_json, load_status, save_status, log
from harvest.textmatch import families

API = "https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
DAYS = 90
WINDOW = 7


def _windows():
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=DAYS)
    d = start
    while d <= end:
        e = min(d + dt.timedelta(days=WINDOW - 1), end)
        yield d.isoformat(), e.isoformat()
        d = e + dt.timedelta(days=1)


def run(conn):
    st = load_status("biorxiv")
    done = set(st.get("windows_done", []))
    out = {"scanned": 0, "matched": 0, "by_server": {}}
    for server in ("biorxiv", "medrxiv"):
        latest = {}  # doi -> newest version record
        for start, end in _windows():
            wkey = f"{server}:{start}:{end}"
            cursor = 0
            while True:
                url = API.format(server=server, start=start, end=end, cursor=cursor)
                d = fetch_json("biorxiv", f"{server}_{start}_{end}_{cursor}", url)
                coll = d.get("collection") or []
                msg = (d.get("messages") or [{}])[0]
                for r in coll:
                    out["scanned"] += 1
                    tags = families(r.get("title"), r.get("abstract"))
                    if not tags:
                        continue
                    prev = latest.get(r["doi"])
                    if prev is None or int(r.get("version", 1)) > int(prev[0].get("version", 1)):
                        latest[r["doi"]] = (r, tags, f"{server}_{start}_{end}_{cursor}")
                total = int(msg.get("total", msg.get("count", 0)) or 0)
                cursor += len(coll)
                if not coll or cursor >= total:
                    break
            done.add(wkey)
        for doi, (r, tags, ref) in latest.items():
            authors = [a.strip() for a in (r.get("authors") or "").split(";") if a.strip()]
            rec = {
                "doi": doi, "title": r.get("title"), "abstract": r.get("abstract"),
                "year": int(r["date"][:4]) if r.get("date") else None,
                "venue": "bioRxiv" if server == "biorxiv" else "medRxiv", "type": "preprint",
                "language": "en", "oa_status": "green",
                "oa_url": f"https://www.{server}.org/content/{doi}v{r.get('version', 1)}.full.pdf",
                "first_author": authors[0] if authors else None,
                "keywords": [r["category"]] if r.get("category") else None,
                "license": r.get("license"),
            }
            db.upsert_work(conn, rec, "biorxiv", ref, tags)
        out["by_server"][server] = len(latest)
        out["matched"] += len(latest)
        log("biorxiv", f"{server}: {len(latest)} matches")
    save_status("biorxiv", {"windows_done": sorted(done), "last_run": dt.date.today().isoformat(),
                            "days": DAYS, **out})
    return out
