import hashlib
import hmac
import json

import pytest

import config
import db
from harvest import arctic_shift, common, reddit_api
from harvest.common import SkipModule
from tests.conftest import SALT, FakeResponse, comment, submission

SECRETS = (b"SecretUserName1", b"OtherSecretUser", b"t2_secretfull", b"SecretFlair")
EXPECTED_HASH = hmac.new(SALT.encode(), b"t2_secretfull", hashlib.sha256).hexdigest()


def disk_bytes(root):
    """Every byte under the sandbox: raw/ cache, the SQLite file and its WAL, status/."""
    return [(p, p.read_bytes()) for p in root.rglob("*") if p.is_file()]


def populate(fake):
    fake.add(submission("p1"), "new:dpdr")
    fake.add(submission("p2", sub="Dissociation"), "new:Dissociation")
    fake.add(comment("c1"), "comments:dpdr")
    fake.add(submission("s1", sub="psychology"), "search")


# --- no username reaches disk -------------------------------------------------------------

def test_no_username_reaches_disk(sandbox, fake, reddit):
    root, conn = sandbox
    populate(fake)
    out = reddit_api.run(conn, reddit)
    assert out["fetched"]["r/dpdr posts"] == 1 and out["purge"]["checked"] == 4 and out["purge"]["purged"] == 0
    conn.commit()

    files = disk_bytes(root)
    assert any("raw/reddit_api" in str(p) for p, _ in files)
    for path, data in files:
        for s in SECRETS:
            assert s not in data, f"{s!r} found in {path}"

    rows = conn.execute("SELECT id, author_id, text, community FROM posts ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == ["reddit:c:c1", "reddit:p1", "reddit:p2", "reddit:s1"]
    assert {r["author_id"] for r in rows} == {EXPECTED_HASH}
    assert "u/[user]" in rows[1]["text"]

    cached = json.loads((config.RAW / "reddit_api" / "t3_p1.json").read_text())
    assert set(cached) == {"id", "created_utc", "subreddit", "title", "body", "score", "author_hash"}


def test_record_never_triggers_a_lazy_fetch(sandbox, fake, reddit):
    fake.add(submission("p1"), "new:dpdr")
    item = next(iter(reddit.subreddit("dpdr").new(limit=None)))
    n = len(fake.calls)
    reddit_api.record(item)
    assert len(fake.calls) == n


def test_missing_salt_refuses_author_fields(monkeypatch):
    monkeypatch.delenv("REDDIT_HASH_SALT", raising=False)
    from harvest import pseudonym
    with pytest.raises(RuntimeError):
        pseudonym.author_hash("t2_x")


def test_arctic_shift_page_is_scrubbed_before_caching(sandbox, monkeypatch, env):
    root, conn = sandbox
    page = {"data": [dict(submission("a1")["data"], crosspost_parent_list=[submission("a0")["data"]],
                          approved_by="SecretUserName1")]}
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        r = FakeResponse(page)
        r.url = url
        return r
    monkeypatch.setattr(common._session, "request", fake_request)

    d = arctic_shift._get("dpdr_posts_after_1", "posts/search", {"subreddit": "dpdr"})
    cached = (config.RAW / "arctic_shift" / "dpdr_posts_after_1.json").read_bytes()
    for s in SECRETS:
        assert s not in cached
    assert d["data"][0]["author_hash"] == EXPECTED_HASH
    assert d["data"][0]["crosspost_parent_list"][0]["author_hash"] == EXPECTED_HASH
    # A page cached by an earlier version (unscrubbed) is hashed at parse time.
    assert arctic_shift._author(submission("old")["data"]) == EXPECTED_HASH


def test_arctic_shift_idle_without_salt(sandbox):
    with pytest.raises(SkipModule):
        arctic_shift.run(sandbox[1])


# --- deletion purge -----------------------------------------------------------------------

def test_purge_deletes_rows_and_cache(sandbox, fake, reddit):
    root, conn = sandbox
    populate(fake)
    fake.add(submission("p3"), "new:dpdr")
    reddit_api.run(conn, reddit)
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 5

    # An Arctic Shift row whose cached page also holds a post that stays.
    page = config.RAW / "arctic_shift" / "dpdr_posts_after_1.json"
    page.parent.mkdir(parents=True)
    page.write_text(json.dumps({"data": [{"id": "h1", "author_hash": "x"}, {"id": "h2", "author_hash": "y"}]}))
    for hid in ("h1", "h2"):
        db.upsert_post(conn, {"id": f"reddit:{hid}", "platform": "reddit", "raw_path": str(page)})
        fake.items[f"t3_{hid}"] = submission(hid)
    conn.commit()

    # Later: one removed by moderators, one deleted by its author, one comment's account deleted,
    # one gone from /api/info entirely, one Arctic Shift post removed.
    fake.items["t3_p1"]["data"].update(selftext="[removed]", removed_by_category="moderator")
    fake.items["t3_p2"]["data"].update(selftext="[deleted]", author="[deleted]")
    fake.items["t1_c1"]["data"].update(author="[deleted]")
    del fake.items["t3_p3"]
    fake.items["t3_h1"]["data"].update(selftext="[deleted]")
    for lst in (*fake.new.values(), *fake.comments.values()):
        lst.clear()
    fake.search.clear()

    # A full cycle reaches every Arctic Shift id once; the API-fetched ones are checked daily.
    outs = [reddit_api.run(conn, reddit, day=d)["purge"] for d in range(reddit_api.CYCLE_DAYS)]
    assert sum(o["purged"] for o in outs) == 5
    left = [r[0] for r in conn.execute("SELECT id FROM posts ORDER BY id")]
    assert left == ["reddit:h2", "reddit:s1"]
    assert {r[0] for r in conn.execute("SELECT id FROM purged")} == {
        "reddit:p1", "reddit:p2", "reddit:c:c1", "reddit:p3", "reddit:h1"}
    cache = sorted(p.name for p in (config.RAW / "reddit_api").iterdir())
    assert cache == ["t3_s1.json"]
    assert [it["id"] for it in json.loads(page.read_text())["data"]] == ["h2"]


def test_purged_ids_are_never_reinserted(sandbox, fake, reddit):
    root, conn = sandbox
    fake.add(submission("p1"), "new:dpdr")
    reddit_api.run(conn, reddit)
    fake.items["t3_p1"]["data"].update(selftext="[removed]")
    reddit_api.run(conn, reddit)  # listing still serves the old copy; purge removes it
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0

    fake.items["t3_p1"]["data"].update(selftext="back again")  # e.g. a stale listing or an Arctic replay
    reddit_api.run(conn, reddit)
    db.upsert_post(conn, {"id": "reddit:p1", "platform": "reddit"})
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
    assert not (config.RAW / "reddit_api" / "t3_p1.json").exists()


def test_deleted_items_are_not_stored_at_fetch(sandbox, fake, reddit):
    root, conn = sandbox
    fake.add(submission("p1", selftext="[removed]"), "new:dpdr")
    fake.add(comment("c1", author="[deleted]", body="[deleted]"), "comments:dpdr")
    reddit_api.run(conn, reddit)
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
    assert not (config.RAW / "reddit_api").exists()


# --- read-only ----------------------------------------------------------------------------

def test_token_requests_only_read_scope(sandbox, fake, reddit):
    fake.add(submission("p1"), "new:dpdr")
    list(reddit.subreddit("dpdr").new(limit=None))
    token = [kw for m, url, kw in fake.calls if url.endswith("/api/v1/access_token")]
    assert len(token) == 1
    data = dict(token[0]["data"])
    assert data["grant_type"] == "password" and data["scope"] == "read"
    assert {m for m, _, _ in fake.calls} == {"GET", "POST"}


@pytest.mark.parametrize("action", [
    lambda r, s, c: r.subreddit("dpdr").submit("t", selftext="x"),
    lambda r, s, c: s.reply("x"),
    lambda r, s, c: c.reply("x"),
    lambda r, s, c: s.upvote(),
    lambda r, s, c: c.downvote(),
    lambda r, s, c: s.save(),
    lambda r, s, c: s.edit("x"),
    lambda r, s, c: c.delete(),
    lambda r, s, c: s.report("x"),
    lambda r, s, c: s.hide(),
    lambda r, s, c: r.subreddit("dpdr").subscribe(),
    lambda r, s, c: r.redditor("someone").message(subject="s", message="m"),
    lambda r, s, c: r.request(method="DELETE", path="/api/del"),
], ids=["submit", "reply_post", "reply_comment", "upvote", "downvote", "save", "edit", "delete",
        "report", "hide", "subscribe", "message", "raw_delete"])
def test_write_calls_are_impossible(sandbox, fake, reddit, action):
    fake.add(submission("p1"), "new:dpdr")
    fake.add(comment("c1"), "comments:dpdr")
    s = next(iter(reddit.subreddit("dpdr").new(limit=None)))
    c = next(iter(reddit.subreddit("dpdr").comments(limit=None)))
    with pytest.raises(reddit_api.WriteBlocked):
        action(reddit, s, c)
    assert [(m, u) for m, u, _ in fake.calls if m != "GET" and not u.endswith("/api/v1/access_token")] == []


def test_requestor_refuses_writes_that_bypass_praw(sandbox, fake, reddit):
    """PRAW's media upload calls the requestor directly; the requestor is the last gate."""
    requestor = reddit._core.requestor
    for method, url in [("POST", "https://oauth.reddit.com/api/submit"),
                        ("POST", "https://reddit-uploaded-media.s3-accelerate.amazonaws.com"),
                        ("PUT", "https://oauth.reddit.com/api/v1/me/friends/x"),
                        ("PATCH", "https://oauth.reddit.com/api/v1/me/prefs"),
                        ("POST", "https://www.reddit.com/api/v1/revoke_token")]:
        with pytest.raises(reddit_api.WriteBlocked):
            requestor.request(method, url)
    assert fake.calls == []


def test_every_core_is_the_read_scoped_script_session(sandbox, reddit):
    assert reddit._core is reddit._authorized_core is reddit._read_only_core
    assert reddit._core._authorizer._scopes == ["read"]


# --- idle ---------------------------------------------------------------------------------

@pytest.mark.parametrize("missing", reddit_api.ENV_VARS)
def test_idle_unless_all_vars_set(sandbox, env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(SkipModule, match=missing):
        reddit_api.run(sandbox[1])
    assert not config.RAW.exists()


def test_user_agent_and_targets():
    assert reddit_api.USER_AGENT == "script:dpdr-corpus:v1 (by /u/Known-Scale-7565)"
    assert reddit_api.SUBREDDITS == ("dpdr", "Dissociation")
    assert reddit_api.SEARCH_TERMS == ("depersonalization", "depersonalisation", "derealization", "DPDR")
    assert reddit_api.MIN_GAP >= 0.6  # 100 requests/min free-tier limit


def test_scrub_script_pseudonymizes_old_cache(sandbox, env):
    from scripts import scrub_reddit_cache
    page = config.RAW / "arctic_shift" / "dpdr_posts_after_1.json"
    page.parent.mkdir(parents=True)
    page.write_text(json.dumps({"data": [submission("a1")["data"]]}))
    scrub_reddit_cache.main([])
    assert b"SecretUserName1" in page.read_bytes()  # dry run changes nothing
    scrub_reddit_cache.main(["--apply"])
    data = page.read_bytes()
    for s in SECRETS:
        assert s not in data
    assert json.loads(data)["data"][0]["author_hash"] == EXPECTED_HASH


def test_purge_cadence_daily_recent_rolling_archive(sandbox, fake, reddit):
    root, conn = sandbox
    fake.add(submission("p1"), "new:dpdr")
    reddit_api.run(conn, reddit, day=0)
    archive = [f"h{i}" for i in range(700)]
    for hid in archive:
        db.upsert_post(conn, {"id": f"reddit:{hid}", "platform": "reddit"}, tags=["reddit:r/dpdr"])
        fake.items[f"t3_{hid}"] = submission(hid)
    conn.commit()

    seen = []
    for d in range(reddit_api.CYCLE_DAYS):
        fake.calls.clear()
        out = reddit_api.purge(conn, reddit, day=d)
        ids = [i for m, u, kw in fake.calls if "/api/info" in u for i in kw["params"]["id"].split(",")]
        assert "t3_p1" in ids  # fetched by reddit_api: every day
        seen += [i for i in ids if i != "t3_p1"]
        assert 60 <= out["checked"] - 1 <= 140  # about 1/7 of the archive
    assert sorted(seen) == sorted(f"t3_{h}" for h in archive)  # each archive id exactly once per cycle


def test_api_items_older_than_30_days_move_to_the_rolling_cycle(sandbox, fake, reddit):
    root, conn = sandbox
    fake.add(submission("p1"), "new:dpdr")
    created_day = fake.items["t3_p1"]["data"]["created_utc"] // 86400
    reddit_api.run(conn, reddit, day=created_day)

    def checked(day):
        fake.calls.clear()
        reddit_api.purge(conn, reddit, day=day)
        return [i for m, u, kw in fake.calls if "/api/info" in u for i in kw["params"]["id"].split(",")]

    young = created_day + reddit_api.RECENT_DAYS - 1
    assert all(checked(d) == ["t3_p1"] for d in range(young - 6, young + 1))  # daily while young
    old = created_day + reddit_api.RECENT_DAYS + 1
    hits = [d for d in range(old, old + reddit_api.CYCLE_DAYS) if checked(d) == ["t3_p1"]]
    assert len(hits) == 1  # then once per 7-day cycle
