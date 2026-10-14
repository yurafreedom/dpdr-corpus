"""Bluesky post search (spec section 9).

Verified 2026-10-13: public.api.bsky.app returns 403 for searchPosts.
api.bsky.app serves the FIRST page (100 posts) without auth, but any request
with a `cursor` gets 403 "Request forbidden by administrative rules". So
keyless runs keep only the newest 100 posts per query (recorded as truncated);
with BSKY_HANDLE + BSKY_APP_PASSWORD the module pages through bsky.social with
a session token. SkipModule only if even the first page is refused and no
credentials are set.

Ethics: posts.author_id is the author's DID; handles/display names stay only in raw/.
"""
import json
import os
import time

import requests

import config
import db
from harvest.common import SkipModule, fetch_json, log, raw_path, save_status, load_status

QUERIES = ["depersonalisation", "depersonalization", "derealisation", "derealization", "DPDR",
           '"time perception"']
KEYLESS = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
AUTHED = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
MAX_PAGES = int(os.environ.get("BSKY_MAX_PAGES", "40"))  # 100 posts/page


def _session_headers():
    h, p = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    if not (h and p):
        return None
    r = requests.post("https://bsky.social/xrpc/com.atproto.server.createSession",
                      json={"identifier": h, "password": p}, timeout=60)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['accessJwt']}"}


def _page(url, headers, q, cursor):
    params = {"q": q, "limit": 100, "sort": "latest"}
    if cursor:
        params["cursor"] = cursor
    # The first page is date-stamped so a daily rerun sees new posts; later pages are stable.
    key = f"{q}_{cursor or 'first_' + time.strftime('%Y%m%d')}"
    return fetch_json("bluesky", key, url, params, headers=headers), raw_path("bluesky", key)


def run(conn):
    url, headers = KEYLESS, None
    try:
        _page(url, headers, QUERIES[0], None)
    except requests.HTTPError as e:
        headers = _session_headers()
        if headers is None:
            raise SkipModule(f"searchPosts refused without auth ({str(e)[:80]}); set BSKY_HANDLE/BSKY_APP_PASSWORD")
        url = AUTHED
    st = load_status("bluesky")
    counts, truncated = {}, []
    auth_tried = headers is not None
    for q in QUERIES:
        cursor, n = None, 0
        for _ in range(MAX_PAGES):
            try:
                d, rp = _page(url, headers, q, cursor)
            except requests.HTTPError:
                if not cursor:
                    raise
                if not auth_tried:
                    auth_tried = True
                    h = _session_headers()
                    if h:
                        url, headers = AUTHED, h
                        continue
                truncated.append(q)
                break
            posts = d.get("posts", [])
            for p in posts:
                rec = p.get("record", {})
                did = p["author"]["did"]
                rkey = p["uri"].rsplit("/", 1)[-1]
                db.upsert_post(conn, {
                    "id": "bsky:" + p["uri"], "platform": "bluesky", "author_id": did,
                    "created": rec.get("createdAt") or p.get("indexedAt"), "text": rec.get("text"),
                    "metrics_json": json.dumps({k: p.get(k) for k in
                                                ("likeCount", "repostCount", "replyCount", "quoteCount")}),
                    "url": f"https://bsky.app/profile/{did}/post/{rkey}",
                    "raw_path": str(rp.relative_to(config.ROOT)), "community": None, "title": None,
                }, tags=[f"bsky:{q}"])
            n += len(posts)
            cursor = d.get("cursor")
            if not posts or not cursor:
                break
        conn.commit()
        counts[q] = n
        log("bluesky", f"{q}: {n}")
    st.update({"counts": counts, "authed": headers is not None, "truncated_no_auth": truncated,
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    save_status("bluesky", st)
    return {"posts_seen": counts, "endpoint": url,
            "truncated_to_first_page": truncated,
            "note": "keyless paging is refused; set BSKY_HANDLE/BSKY_APP_PASSWORD for full history" if truncated else ""}
