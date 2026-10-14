"""Mastodon hashtag timelines (spec section 9).

Verified 2026-10-13: /api/v1/timelines/tag is public on mastodon.social and
fediscience.org; scholar.social requires an authenticated user. /api/v2/search
returns no statuses without auth on all three, so it is not used. Optional
tokens: MASTODON_TOKENS="host=token,host=token".

A tag timeline only contains posts that reached that instance (federation), so
the three instances overlap but are not exhaustive.

Ethics: posts.author_id is "<instance>:<account id>"; post id/url use the
instance-local status id because public URLs embed handles. No profiles.
The same status seen on two instances gets two rows (different local ids).
"""
import html
import json
import os
import re
import time

import requests

import config
import db
from harvest.common import fetch_json, log, raw_path, save_status

INSTANCES = ["mastodon.social", "fediscience.org", "scholar.social"]
TAGS = ["depersonalization", "depersonalisation", "derealization", "dpdr", "dissociation", "timeperception"]
MAX_PAGES = int(os.environ.get("MASTODON_MAX_PAGES", "25"))  # 40 statuses/page


def _tokens():
    out = {}
    for pair in os.environ.get("MASTODON_TOKENS", "").split(","):
        if "=" in pair:
            h, t = pair.split("=", 1)
            out[h.strip()] = t.strip()
    return out


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def run(conn):
    tokens = _tokens()
    summary, unavailable = {}, {}
    for host in INSTANCES:
        headers = {"Authorization": f"Bearer {tokens[host]}"} if host in tokens else None
        for tag in TAGS:
            max_id, n = None, 0
            for _ in range(MAX_PAGES):
                params = {"limit": 40}
                if max_id:
                    params["max_id"] = max_id
                key = f"{host}_{tag}_{max_id or 'first_' + time.strftime('%Y%m%d')}"
                try:
                    page = fetch_json("mastodon", key, f"https://{host}/api/v1/timelines/tag/{tag}", params,
                                      headers=headers, max_tries=3)
                except requests.HTTPError as e:
                    unavailable[f"{host}#{tag}"] = str(e)[:120]
                    break
                for s in page:
                    db.upsert_post(conn, {
                        # Status URLs/URIs embed the author's handle, so id and url use the
                        # instance-local status id instead (/web/statuses/<id> resolves on that instance).
                        "id": f"mastodon:{host}:{s['id']}", "platform": "mastodon",
                        "author_id": f"{host}:{s['account']['id']}", "created": s["created_at"],
                        "text": _strip(s.get("content")),
                        "metrics_json": json.dumps({k: s.get(k) for k in
                                                    ("favourites_count", "reblogs_count", "replies_count")}),
                        "url": f"https://{host}/web/statuses/{s['id']}",
                        "raw_path": str(raw_path("mastodon", key).relative_to(config.ROOT)),
                        "community": host, "title": None,
                    }, tags=[f"mastodon:#{tag}"])
                n += len(page)
                if len(page) < 40:
                    break
                max_id = page[-1]["id"]
            conn.commit()
            summary[f"{host}#{tag}"] = n
            if f"{host}#{tag}" not in unavailable:
                log("mastodon", f"{host} #{tag}: {n}")
            if f"{host}#{tag}" in unavailable and "authenticated" in unavailable[f"{host}#{tag}"]:
                break  # instance needs auth for every tag; skip the rest
    save_status("mastodon", {"counts": summary, "unavailable": unavailable})
    return {"counts": summary, "unavailable": unavailable}
