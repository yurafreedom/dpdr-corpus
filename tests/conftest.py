"""Test fixtures: every path (raw/, status/, the database) is redirected to a temp dir,
and Reddit is a fake HTTP session under PRAW, so no test touches the network or real data."""
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
from harvest import reddit_api  # noqa: E402

SALT = "test-salt"
NOW = int(time.time()) - 3600  # fixtures are recent posts, as a daily fetch would see them
ENV = {"REDDIT_CLIENT_ID": "cid", "REDDIT_CLIENT_SECRET": "csecret", "REDDIT_USERNAME": "Known-Scale-7565",
       "REDDIT_PASSWORD": "pw", "REDDIT_HASH_SALT": SALT}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "RAW", tmp_path / "raw")
    monkeypatch.setattr(config, "STATUS", tmp_path / "status")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "corpus.db")
    monkeypatch.setattr(reddit_api, "MIN_GAP", 0.0)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    conn = db.connect()
    yield tmp_path, conn
    conn.close()


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.headers = {"content-type": "application/json", "x-ratelimit-remaining": "99",
                        "x-ratelimit-used": "1", "x-ratelimit-reset": "60"}

    def json(self):
        return json.loads(self.text)


def submission(id, author="SecretUserName1", sub="dpdr", **kw):
    d = {"id": id, "name": f"t3_{id}", "title": f"title {id}", "selftext": f"post {id} thanks u/OtherSecretUser",
         "author": author, "author_fullname": "t2_secretfull", "author_flair_text": "SecretFlair",
         "subreddit": sub, "created_utc": NOW, "score": 5, "num_comments": 1,
         "permalink": f"/r/{sub}/comments/{id}/x/"}
    d.update(kw)
    return {"kind": "t3", "data": d}


def comment(id, author="SecretUserName1", sub="dpdr", **kw):
    d = {"id": id, "name": f"t1_{id}", "body": f"comment {id}", "author": author,
         "author_fullname": "t2_secretfull", "subreddit": sub, "created_utc": NOW + 100, "score": 2,
         "link_id": "t3_p1", "parent_id": "t3_p1", "permalink": f"/r/{sub}/comments/p1/x/{id}/"}
    d.update(kw)
    return {"kind": "t1", "data": d}


def listing(children):
    return {"kind": "Listing", "data": {"after": None, "before": None, "children": children}}


class FakeReddit:
    """Plays reddit.com: `items` maps fullname -> thing; listings return what they are given."""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.items = {}
        self.new = {s: [] for s in reddit_api.SUBREDDITS}
        self.comments = {s: [] for s in reddit_api.SUBREDDITS}
        self.search = []

    def add(self, thing, where):
        self.items[thing["data"]["name"]] = thing
        if where == "search":
            self.search.append(thing)
        else:
            kind, sub = where.split(":")
            (self.new if kind == "new" else self.comments)[sub].append(thing)

    def request(self, method, url, **kw):
        self.calls.append((method.upper(), url, kw))
        u = urlparse(url)
        if method.upper() == "POST" and u.path == "/api/v1/access_token":
            return FakeResponse({"access_token": "tok", "token_type": "bearer", "expires_in": 3600,
                                 "scope": dict(kw["data"]).get("scope", "*")})
        assert method.upper() == "GET", f"fake reddit got a {method} to {url}"
        parts = u.path.strip("/").split("/")
        if parts[:2] == ["api", "info"]:
            ids = (kw.get("params") or {}).get("id") or parse_qs(u.query)["id"][0]
            return FakeResponse(listing([self.items[n] for n in ids.split(",") if n in self.items]))
        if parts[0] == "r" and parts[2] == "new":
            return FakeResponse(listing(self.new.get(parts[1], [])))
        if parts[0] == "r" and parts[2] == "comments":
            return FakeResponse(listing(self.comments.get(parts[1], [])))
        if parts[0] == "r" and parts[2] == "search":
            return FakeResponse(listing(self.search))
        raise AssertionError(f"unexpected request {method} {url}")


@pytest.fixture
def fake():
    return FakeReddit()


@pytest.fixture
def reddit(fake, env):
    return reddit_api.client(requestor_kwargs={"session": fake})
