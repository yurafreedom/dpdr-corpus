"""Podcasts and episodes via the iTunes Search API (spec section 9). Keyless.

The API returns at most 200 results per call and has no paging, so each
(term, entity) pair is a single request.
"""
import json

import config
import db
from harvest.common import fetch_json, log, raw_path

URL = "https://itunes.apple.com/search"
TERMS = ["depersonalization", "depersonalisation", "derealization", "derealisation", "dpdr", "time perception"]
ENTITIES = ["podcast", "podcastEpisode"]


def run(conn):
    counts = {}
    for term in TERMS:
        for ent in ENTITIES:
            params = {"media": "podcast", "entity": ent, "term": term, "limit": 200}
            key = f"{ent}_{term}"
            d = fetch_json("itunes", key, URL, params)
            rp = str(raw_path("itunes", key).relative_to(config.ROOT))
            for r in d.get("results", []):
                if ent == "podcast":
                    rid, kind = f"itunes:podcast:{r['collectionId']}", "podcast"
                    title, url = r.get("collectionName"), r.get("collectionViewUrl")
                    date = r.get("releaseDate")
                    meta = {k: r.get(k) for k in ("artistName", "feedUrl", "trackCount", "genres", "country")}
                else:
                    rid, kind = f"itunes:episode:{r['trackId']}", "podcast_episode"
                    title, url = r.get("trackName"), r.get("trackViewUrl")
                    date = r.get("releaseDate")
                    meta = {k: r.get(k) for k in ("collectionName", "collectionId", "feedUrl", "episodeUrl",
                                                  "trackTimeMillis", "description", "shortDescription")}
                meta["term"] = term
                db.upsert_record(conn, {"id": rid, "kind": kind, "source": "itunes", "title": title,
                                        "year": int(date[:4]) if date else None, "url": url,
                                        "meta_json": json.dumps(meta, ensure_ascii=False), "raw_path": rp})
            counts[key] = d.get("resultCount", 0)
        conn.commit()
        log("podcasts", f"{term}: " + ", ".join(f"{e}={counts[f'{e}_{term}']}" for e in ENTITIES))
    n = conn.execute("SELECT kind, COUNT(*) FROM records WHERE source='itunes' GROUP BY kind").fetchall()
    return {"per_query": counts, "unique": {k: c for k, c in n}}
