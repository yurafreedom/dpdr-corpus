"""Reddit history via the Arctic Shift API (spec section 9). Keyless.

Base: https://arctic-shift.photon-reddit.com/api (verified 2026-10-13).
Submissions are walked oldest -> newest with `after=<epoch>&sort=asc&limit=auto`.
Every run replays the walk from the first post: pages already in raw/ are read
from disk (no network, no pacing), so whichever database the run writes to gets
the full history, and only new pages count toward ARCTIC_MAX_CALLS network
requests. When the cap is hit the module is marked "partial" and the next run
continues. status/arctic_shift.json is progress reporting only. Comments only
when ARCTIC_COMMENTS=1. Full r/dpdr + neighbours is ~150k posts / ~1,100 calls.

The API signals rate limiting as HTTP 422 or as HTTP 200 with {"error": "Timeout. Maybe slow down a bit"};
those bodies are deleted from raw/ and retried, never parsed or kept.

Ethics: responses pass through harvest.pseudonym.scrub_item before they are
cached, so pages fetched from now on carry author_hash (HMAC-SHA256 with
REDDIT_HASH_SALT) and no account fields. Pages cached by earlier versions are
read as they are and hashed at parse time; posts.author_id is always the hash.
Needs REDDIT_HASH_SALT. Ids in the `purged` table (Reddit deletion purge, see
reddit_api) are never re-inserted.
"""
import json
import os
import time

import requests

import config
import db
from harvest import common
from harvest.common import BudgetExhausted, SkipModule, fetch, log, raw_path, load_status, save_status
from harvest.pseudonym import author_hash, redact, salt, scrub_item

BASE = "https://arctic-shift.photon-reddit.com/api"
# Spec list plus r/Depersonalization (16k posts, found during verification).
SUBREDDITS = ["dpdr", "Dissociation", "DPDRecoveryStories", "DPDRecovery", "Depersonalization", "derealization"]
MAX_CALLS = int(os.environ.get("ARCTIC_MAX_CALLS", "1500"))
common.PACE.setdefault("arctic_shift", 2.0)


def _scrub(text):
    return json.dumps(scrub_item(json.loads(text)), ensure_ascii=False)


def _author(it):
    return it["author_hash"] if "author_hash" in it else author_hash(it.get("author_fullname"), it.get("author"))


def _get(key, path, params):
    for attempt in range(8):
        try:
            txt = fetch("arctic_shift", key, f"{BASE}/{path}", params, transform=_scrub)
        except requests.HTTPError as e:  # rate limiting also arrives as HTTP 422 "slow down"
            if "slow down" not in str(e).lower():
                raise
            time.sleep(5 * (attempt + 1))
            continue
        d = json.loads(txt)
        if d.get("error") and not d.get("data"):
            raw_path("arctic_shift", key).unlink(missing_ok=True)
            time.sleep(5 * (attempt + 1))
            continue
        return d
    raise RuntimeError(f"arctic_shift kept erroring for {path} {params}")


def _sub_info(name):
    d = _get(f"subinfo_{name}_{time.strftime('%Y%m%d')}", "subreddits/search", {"subreddit": name, "limit": 1})
    for s in d.get("data") or []:
        if s.get("display_name", "").lower() == name.lower():
            return {"name": s["display_name"], "meta": s.get("_meta"), "subscribers": s.get("subscribers")}
    return None


def _walk(conn, sub, kind, st, budget, start):
    """kind: 'posts' or 'comments'. Returns calls used. `start` is an epoch just before the first item
    (the API rejects after=0)."""
    ck = st[f"{sub}:{kind}"] = {"after": start, "count": 0, "caught_up": False}
    calls = 0
    while True:
        after = ck["after"]
        key = f"{sub}_{kind}_after_{after}"
        cached = raw_path("arctic_shift", key).exists()
        if not cached and calls >= budget:
            break
        d = _get(key, f"{kind}/search", {"subreddit": sub, "after": after, "sort": "asc", "limit": "auto"})
        calls += 0 if cached else 1
        items = d.get("data") or []
        if not items:
            # Do not cache the empty tail page, so a later run picks up new posts.
            raw_path("arctic_shift", key).unlink(missing_ok=True)
            ck["caught_up"] = True
            break
        rp = str(raw_path("arctic_shift", key).relative_to(config.ROOT))
        for it in items:
            created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(it["created_utc"]))
            if kind == "posts":
                row = {"id": f"reddit:{it['id']}", "title": redact(it.get("title")), "text": redact(it.get("selftext")),
                       "metrics_json": json.dumps({k: it.get(k) for k in ("score", "num_comments", "upvote_ratio")}),
                       "url": "https://www.reddit.com" + it.get("permalink", "")}
            else:
                row = {"id": f"reddit:c:{it['id']}", "title": None, "text": redact(it.get("body")),
                       "metrics_json": json.dumps({"score": it.get("score"), "link_id": it.get("link_id"),
                                                   "parent_id": it.get("parent_id")}),
                       "url": "https://www.reddit.com" + it["permalink"] if it.get("permalink") else None}
            row.update({"platform": "reddit", "author_id": _author(it), "created": created,
                        "raw_path": rp, "community": it.get("subreddit", sub)})
            db.upsert_post(conn, row, tags=[f"reddit:r/{sub}"])
        last = max(it["created_utc"] for it in items)
        # Step back one second so posts sharing the boundary second are not lost
        # (duplicates are absorbed by the upsert), unless that would not advance.
        ck["after"] = last - 1 if last - 1 > after else last
        ck["count"] += len(items)
        ck["caught_up"] = False
        if not cached:
            conn.commit()
            save_status("arctic_shift", st)
    conn.commit()
    save_status("arctic_shift", st)
    return calls


def run(conn):
    if not salt():
        raise SkipModule("needs REDDIT_HASH_SALT (authors are stored only as salted hashes)")
    st = load_status("arctic_shift")
    if "subreddits" not in st:
        info = {}
        for s in SUBREDDITS:
            info[s] = _sub_info(s)
        st["subreddits"] = info
        save_status("arctic_shift", st)
    kinds = ["posts"] + (["comments"] if os.environ.get("ARCTIC_COMMENTS") == "1" else [])
    calls = 0
    for sub, info in st["subreddits"].items():
        if not info:
            continue
        for kind in kinds:
            meta = info.get("meta") or {}
            first = meta.get("earliest_post" if kind == "posts" else "earliest_comment") or 1104537600
            used = _walk(conn, info["name"], kind, st, MAX_CALLS - calls, int(first) - 1)
            calls += used
            ck = st[f"{info['name']}:{kind}"]
            log("arctic_shift", f"r/{info['name']} {kind}: {ck['count']} total, caught_up={ck['caught_up']}")
    save_status("arctic_shift", st)
    pending = [k for k, v in st.items() if ":" in k and not v["caught_up"]]
    if pending:
        # Marks the module "partial" so the next pipeline run continues (cached pages replay for free).
        raise BudgetExhausted(f"network call cap {MAX_CALLS} reached; not caught up: {pending}")
    return {"calls": calls,
            "missing_subreddits": [s for s, i in st["subreddits"].items() if not i],
            "progress": {k: v for k, v in st.items() if ":" in k}}
