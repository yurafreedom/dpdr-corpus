"""Recent Reddit posts and comments via the official Data API (spec section 9).

A "script"-type app with PRAW, OAuth password grant, one account. Idle unless
all of REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD
and REDDIT_HASH_SALT are set. Meant to run once a day:

    .venv/bin/python pipeline.py run --module reddit_api

Each run
  1. fetches new posts and new comments from r/dpdr and r/Dissociation (names
     verified 2026-10-14), plus a Reddit-wide search for SEARCH_TERMS (past week,
     newest first);
  2. purges: re-checks stored Reddit ids via /api/info and deletes the row and
     its cached data for anything [deleted] or [removed]. Items fetched by this
     module are re-checked every day for their first RECENT_DAYS days; after
     that, and for the Arctic Shift historical set, on a rolling CYCLE_DAYS
     cycle (a stable 1/7 of ids per day), which keeps the purge to a few
     hundred requests a day. The id goes to the `purged` table so
     no cache replay re-inserts it.

Read-only by construction: the token requests only the "read" scope, and every
HTTP request passes through GetOnlyRequestor, which refuses anything other than
GET (and the POST that obtains the token) before it leaves the process.
Requests are paced at MIN_GAP seconds, under the free tier's 100 per minute.

Stored per item: id, created_utc, subreddit, title / body text, score and the
author as HMAC-SHA256(REDDIT_HASH_SALT, author_fullname). The record is built
from PRAW's object without touching any other attribute, and is pseudonymized
before it is written to raw/reddit_api/ or the database.
"""
import json
import os
import threading
import time
import zlib

import praw
import prawcore
from prawcore.auth import ScriptAuthorizer, TrustedAuthenticator
from prawcore.const import ACCESS_TOKEN_PATH

import config
import db
from harvest.common import SkipModule, log, raw_path
from harvest.pseudonym import author_hash, redact

ENV_VARS = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD", "REDDIT_HASH_SALT")
USER_AGENT = "script:dpdr-corpus:v1 (by /u/Known-Scale-7565)"
SCOPES = ("read",)
SUBREDDITS = ("dpdr", "Dissociation")
SEARCH_TERMS = ("depersonalization", "depersonalisation", "derealization", "DPDR")
SEARCH_WINDOW = "week"  # overlaps between daily runs; duplicates are absorbed by the upsert
MIN_GAP = 1.0  # seconds between requests: at most 60/min against the free tier's 100/min
SOURCE = "reddit_api"
CYCLE_DAYS = 7  # older items (Arctic Shift history, API items past RECENT_DAYS) are re-checked once per cycle
RECENT_DAYS = 30  # API-fetched items younger than this are re-checked daily


class WriteBlocked(RuntimeError):
    """A request other than a GET (or the token POST) was attempted."""


class GetOnlyRequestor(prawcore.Requestor):
    """The single path for every HTTP request PRAW makes. Refuses writes and paces calls."""

    _lock = threading.Lock()
    _last = 0.0

    def request(self, method, url, *args, **kwargs):
        if not (method.upper() == "GET"
                or (method.upper() == "POST" and url == self.reddit_url + ACCESS_TOKEN_PATH)):
            raise WriteBlocked(f"refused {method.upper()} {url}: this client is read-only")
        with GetOnlyRequestor._lock:
            wait = GetOnlyRequestor._last + MIN_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            GetOnlyRequestor._last = time.monotonic()
        return super().request(method, url, *args, **kwargs)


class ReadOnlyReddit(praw.Reddit):
    """praw.Reddit whose only session is a script authorizer limited to SCOPES."""

    def _prepare_trusted_prawcore(self, requestor):
        authenticator = TrustedAuthenticator(client_id=self.config.client_id,
                                             client_secret=self.config.client_secret,
                                             redirect_uri=self.config.redirect_uri, requestor=requestor)
        authorizer = ScriptAuthorizer(authenticator=authenticator, username=self.config.username,
                                      password=self.config.password, scopes=list(SCOPES))
        self._core = self._authorized_core = self._read_only_core = prawcore.session(
            authorizer=authorizer, window_size=self.config.window_size)

    def request(self, *, method, path, **kwargs):
        if method.upper() != "GET":
            raise WriteBlocked(f"refused {method.upper()} {path}: this client is read-only")
        return super().request(method=method, path=path, **kwargs)


def client(requestor_kwargs=None):
    env = {k: os.environ.get(k, "") for k in ENV_VARS}
    return ReadOnlyReddit(client_id=env["REDDIT_CLIENT_ID"], client_secret=env["REDDIT_CLIENT_SECRET"],
                          username=env["REDDIT_USERNAME"], password=env["REDDIT_PASSWORD"],
                          user_agent=USER_AGENT, requestor_class=GetOnlyRequestor,
                          requestor_kwargs=requestor_kwargs, check_for_updates=False, check_for_async=False)


def _row_id(fullname):
    kind, rid = fullname.split("_", 1)
    return f"reddit:c:{rid}" if kind == "t1" else f"reddit:{rid}"


def _fullname(row_id):
    rest = row_id.split(":", 1)[1]
    return f"t1_{rest[2:]}" if rest.startswith("c:") else f"t3_{rest}"


def is_gone(item):
    """True for an item that is [deleted] or [removed], by its author or by moderators."""
    v = vars(item)
    text = v.get("body") if "body" in v else v.get("selftext")
    return (v.get("author") is None or str(v.get("author")) == "[deleted]"
            or text in ("[deleted]", "[removed]") or v.get("title") in ("[deleted]", "[removed]", "[deleted by user]")
            or bool(v.get("removed_by_category")))


def record(item):
    """The stored fields of a PRAW Submission or Comment. Reads only the object's loaded data
    (vars), so no lazy fetch is triggered, and never keeps the username."""
    v = vars(item)
    author = v.get("author")
    return {
        "id": item.fullname,
        "created_utc": int(v["created_utc"]),
        "subreddit": str(v["subreddit"]),
        "title": redact(v.get("title")),
        "body": redact(v["body"] if "body" in v else v.get("selftext")),
        "score": v.get("score"),
        "author_hash": author_hash(v.get("author_fullname"), str(author) if author is not None else None),
    }


def store(conn, rec, tag):
    rid = _row_id(rec["id"])
    if db.is_purged(conn, rid):
        return False
    path = raw_path(SOURCE, rec["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False))
    db.upsert_post(conn, {
        "id": rid, "platform": "reddit", "author_id": rec["author_hash"],
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec["created_utc"])),
        "title": rec["title"], "text": rec["body"], "metrics_json": json.dumps({"score": rec["score"]}),
        "url": None, "raw_path": str(path), "community": rec["subreddit"],
    }, tags=[tag])
    return True


def fetch(conn, reddit):
    counts = {}
    sources = []
    for sub in SUBREDDITS:
        sources.append((f"r/{sub} posts", f"reddit_api:r/{sub}", reddit.subreddit(sub).new(limit=None)))
        sources.append((f"r/{sub} comments", f"reddit_api:r/{sub}", reddit.subreddit(sub).comments(limit=None)))
    for term in SEARCH_TERMS:
        sources.append((f"search {term}", f"reddit_api:search:{term}",
                        reddit.subreddit("all").search(term, sort="new", time_filter=SEARCH_WINDOW, limit=None)))
    for label, tag, listing in sources:
        n = 0
        for item in listing:
            if not is_gone(item) and store(conn, record(item), tag):
                n += 1
        conn.commit()
        counts[label] = n
        log(SOURCE, f"{label}: {n}")
    return counts


def _drop_from_cache(rel_path, bare_ids):
    """Remove purged items from a cached file: a per-item file is deleted, a cached page
    (Arctic Shift {"data": [...]} or a listing {"data": {"children": [...]}}) is rewritten without them."""
    p = config.ROOT / rel_path
    if not p.exists():
        return
    if p.parent == config.RAW / SOURCE:
        p.unlink()
        return
    d = json.loads(p.read_text())
    data = d.get("data") if isinstance(d, dict) else None
    if isinstance(data, list):
        d["data"] = [it for it in data if it.get("id") not in bare_ids]
    elif isinstance(data, dict) and isinstance(data.get("children"), list):
        data["children"] = [c for c in data["children"] if (c.get("data") or {}).get("id") not in bare_ids]
    else:
        return
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(p)


def _due(row, day):
    """Items this module fetched within RECENT_DAYS are due every day; everything else on day crc32(id) mod 7."""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((day - RECENT_DAYS) * 86400))
    if f"{SOURCE}:" in (row["query_tags"] or "") and (row["created"] or "") >= cutoff:
        return True
    return zlib.crc32(row["id"].encode()) % CYCLE_DAYS == day % CYCLE_DAYS


def purge(conn, reddit, batch=100, day=None):
    """Re-check today's share of stored Reddit ids; delete rows and cached data for deleted or removed items."""
    day = int(time.time() // 86400) if day is None else day
    rows = conn.execute("SELECT id, raw_path, query_tags, created FROM posts WHERE platform='reddit'").fetchall()
    by_fullname = {_fullname(r["id"]): r for r in rows if _due(r, day)}
    gone = []
    names = list(by_fullname)
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        alive = {it.fullname for it in reddit.info(fullnames=chunk) if not is_gone(it)}
        gone += [n for n in chunk if n not in alive]  # missing from /api/info counts as gone
    by_file = {}
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for n in gone:
        r = by_fullname[n]
        conn.execute("DELETE FROM posts WHERE id=?", (r["id"],))
        conn.execute("INSERT OR IGNORE INTO purged(id, at) VALUES(?, ?)", (r["id"], at))
        if r["raw_path"]:
            by_file.setdefault(r["raw_path"], set()).add(n.split("_", 1)[1])
    conn.commit()
    for rel_path, ids in by_file.items():
        _drop_from_cache(rel_path, ids)
    return {"checked": len(names), "purged": len(gone), "requests": -(-len(names) // batch)}


def run(conn, reddit=None, day=None):
    missing = [k for k in ENV_VARS if not os.environ.get(k)]
    if missing:
        raise SkipModule(f"idle: needs {', '.join(missing)}")
    reddit = reddit or client()
    counts = fetch(conn, reddit)
    purged = purge(conn, reddit, day=day)
    log(SOURCE, f"purge: {purged}")
    return {"fetched": counts, "purge": purged}
