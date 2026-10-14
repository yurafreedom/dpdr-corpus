"""Podcast Index API (spec section 9). Needs PODCASTINDEX_KEY and PODCASTINDEX_SECRET.

Auth: X-Auth-Key, X-Auth-Date (epoch seconds) and
Authorization = sha1(key + secret + date). Searches feeds by term, then pulls
episodes per matching feed; episodes carry a `transcripts` field when the
feed publishes one.
"""
import hashlib
import json
import time

import config
import db
from harvest.common import SkipModule, fetch_json, log, raw_path

API = "https://api.podcastindex.org/api/1.0"
TERMS = ["depersonalization", "depersonalisation", "derealization", "dpdr"]


def _headers():
    k, s = config.KEYS["podcastindex_key"], config.KEYS["podcastindex_secret"]
    now = str(int(time.time()))
    return {"X-Auth-Key": k, "X-Auth-Date": now,
            "Authorization": hashlib.sha1((k + s + now).encode()).hexdigest(),
            "User-Agent": config.USER_AGENT}


def run(conn):
    if not (config.KEYS["podcastindex_key"] and config.KEYS["podcastindex_secret"]):
        raise SkipModule("needs PODCASTINDEX_KEY and PODCASTINDEX_SECRET")
    feeds = {}
    for term in TERMS:
        key = f"byterm_{term}"
        d = fetch_json("podcastindex", key, f"{API}/search/byterm", {"q": term, "max": 100}, headers=_headers())
        for f in d.get("feeds", []):
            feeds[f["id"]] = f
            db.upsert_record(conn, {
                "id": f"podcastindex:feed:{f['id']}", "kind": "podcast", "source": "podcastindex",
                "title": f.get("title"), "year": None, "url": f.get("link") or f.get("url"),
                "meta_json": json.dumps({k: f.get(k) for k in ("url", "author", "description", "itunesId",
                                                               "episodeCount", "language")} | {"term": term},
                                        ensure_ascii=False),
                "raw_path": str(raw_path("podcastindex", key).relative_to(config.ROOT))})
    episodes = 0
    for fid in feeds:
        key = f"episodes_{fid}"
        d = fetch_json("podcastindex", key, f"{API}/episodes/byfeedid", {"id": fid, "max": 1000},
                       headers=_headers())
        for e in d.get("items", []):
            db.upsert_record(conn, {
                "id": f"podcastindex:episode:{e['id']}", "kind": "podcast_episode", "source": "podcastindex",
                "title": e.get("title"),
                "year": int(time.strftime("%Y", time.gmtime(e["datePublished"]))) if e.get("datePublished") else None,
                "url": e.get("link") or e.get("enclosureUrl"),
                "meta_json": json.dumps({k: e.get(k) for k in ("feedId", "description", "enclosureUrl",
                                                               "duration", "transcripts", "transcriptUrl")},
                                        ensure_ascii=False),
                "raw_path": str(raw_path("podcastindex", key).relative_to(config.ROOT))})
            episodes += 1
        conn.commit()
    log("podcastindex", f"feeds={len(feeds)} episodes={episodes}")
    return {"feeds": len(feeds), "episodes": episodes}
