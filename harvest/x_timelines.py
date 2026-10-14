"""X/Twitter user timelines under the one-time $10 voucher (spec section 10).

Runs only with X_BEARER_TOKEN set AND X_ENABLE=1. Never calls search
endpoints (7-day window, covered free by Bluesky/Mastodon). Reads only
GET /2/users/:id/tweets for the handles in harvest/x_accounts.txt.

Budget: $0.005 per post returned (pay-per-use, verified in spec as of 2026).
User-ID lookups are charged conservatively at $0.01 per user. The running
spend is in status/x_budget.json; the module refuses any request whose
worst-case cost would push spend past 90% of X_BUDGET_USD (default 10).
Reruns use since_id, so only new posts are paid for.
"""
import json
import os
import time
from pathlib import Path

import config
import db
from harvest.common import BudgetExhausted, SkipModule, fetch_json, log, raw_path, load_status, save_status

API = "https://api.x.com/2"
PER_POST, PER_USER = 0.005, 0.01
BUDGET = float(os.environ.get("X_BUDGET_USD", "10"))
STOP_AT = 0.9 * BUDGET
MAX_PER_ACCOUNT = int(os.environ.get("X_MAX_POSTS_PER_ACCOUNT", "60"))
ACCOUNTS_FILE = Path(__file__).with_name("x_accounts.txt")
FIELDS = {"tweet.fields": "created_at,public_metrics,entities,referenced_tweets",
          "expansions": "author_id", "user.fields": "description,url"}


def _accounts():
    if not ACCOUNTS_FILE.exists():
        return []
    return [l.split("#")[0].strip().lstrip("@") for l in ACCOUNTS_FILE.read_text().splitlines()
            if l.split("#")[0].strip()]


class _Budget:
    def __init__(self):
        self.st = load_status("x_budget") or {"spent_usd": 0.0, "posts_read": 0, "users_looked_up": 0,
                                              "since_id": {}, "user_ids": {}, "log": []}

    def reserve(self, worst_case):
        if self.st["spent_usd"] + worst_case > STOP_AT:
            raise BudgetExhausted(f"X budget: ${self.st['spent_usd']:.3f} spent, next call could cost "
                                  f"${worst_case:.3f}, hard stop at ${STOP_AT:.2f}")

    def charge(self, usd, what):
        self.st["spent_usd"] = round(self.st["spent_usd"] + usd, 4)
        self.st["log"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "usd": usd, "what": what})
        save_status("x_budget", self.st)


def run(conn):
    token = config.KEYS["x_bearer"]
    if not token or os.environ.get("X_ENABLE") != "1":
        raise SkipModule("needs X_BEARER_TOKEN and explicit opt-in X_ENABLE=1 (paid API)")
    handles = _accounts()
    if not handles:
        raise SkipModule(f"no accounts listed in {ACCOUNTS_FILE.name}")
    headers = {"Authorization": f"Bearer {token}"}
    b = _Budget()
    ids = b.st["user_ids"]
    todo = [h for h in handles if h not in ids]
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        b.reserve(PER_USER * len(chunk))
        key = f"users_by_{'_'.join(chunk)}"
        cached = raw_path("x", key).exists()
        d = fetch_json("x", key, f"{API}/users/by", {"usernames": ",".join(chunk)}, headers=headers)
        if not cached:
            b.charge(PER_USER * len(chunk), f"users/by x{len(chunk)}")
        for u in d.get("data", []):
            ids[u["username"]] = u["id"]
        save_status("x_budget", b.st)
    posts = 0
    for h in handles:
        uid = ids.get(h)
        if not uid:
            continue
        since = b.st["since_id"].get(uid)
        got, pt, newest = 0, None, None
        while got < MAX_PER_ACCOUNT:
            n = max(5, min(100, MAX_PER_ACCOUNT - got))
            b.reserve(PER_POST * n)
            params = dict(FIELDS, max_results=n)
            if since:
                params["since_id"] = since
            if pt:
                params["pagination_token"] = pt
            key = f"tweets_{uid}_since_{since or 0}_{pt or 'first'}_{time.strftime('%Y%m%d')}"
            cached = raw_path("x", key).exists()
            d = fetch_json("x", key, f"{API}/users/{uid}/tweets", params, headers=headers)
            data = d.get("data", [])
            if not cached:
                b.charge(PER_POST * len(data), f"{h} tweets x{len(data)}")
                b.st["posts_read"] += len(data)
            for t in data:
                db.upsert_post(conn, {
                    "id": f"x:{t['id']}", "platform": "x", "author_id": t.get("author_id", uid),
                    "created": t.get("created_at"), "text": t.get("text"),
                    "metrics_json": json.dumps({"public_metrics": t.get("public_metrics"),
                                                "urls": [u.get("expanded_url") for u in
                                                         (t.get("entities") or {}).get("urls", [])],
                                                "referenced_tweets": t.get("referenced_tweets")}),
                    "url": f"https://x.com/i/web/status/{t['id']}",
                    "raw_path": str(raw_path("x", key).relative_to(config.ROOT)), "community": None,
                }, tags=["x:timeline"])
            got += len(data)
            newest = newest or (d.get("meta") or {}).get("newest_id")
            pt = (d.get("meta") or {}).get("next_token")
            if not pt or not data:
                break
        if newest:
            b.st["since_id"][uid] = newest
        save_status("x_budget", b.st)
        conn.commit()
        posts += got
        log("x", f"{h}: {got} posts, spent ${b.st['spent_usd']:.3f}")
    return {"posts": posts, "spent_usd": b.st["spent_usd"], "stop_at_usd": STOP_AT}
