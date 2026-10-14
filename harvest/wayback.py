"""dpselfhelp.com history: Wayback CDX URL index only (spec section 9).

Finding 2026-10-13: dpselfhelp.com/robots.txt disallows "/" for AI agents by
name (ClaudeBot, Claude-Web, anthropic-ai, GPTBot, CCBot, ...) and allows "*"
apart from account/search paths. Because this harvest is run by an AI agent,
this module never fetches dpselfhelp.com pages or archived page bodies. It
stores only the Internet Archive's capture index (URL, first capture
timestamp, mime type, status), which comes from archive.org, not the forum.
The robots finding is stored as a record so it shows up in exports.
"""
import json
import os
import time
import urllib.robotparser

import requests

import config
import db
from harvest.common import BudgetExhausted, fetch, log, raw_path, save_status

CDX = "http://web.archive.org/cdx/search/cdx"
SITE = "dpselfhelp.com"
PAGE = 5000
MAX_CALLS = int(os.environ.get("WAYBACK_MAX_CALLS", "200"))


def _robots(conn):
    # The site sometimes answers 409 to non-browser clients; then the check is
    # recorded as unavailable (no content is fetched either way).
    try:
        txt = fetch("wayback", f"robots_{SITE}_{time.strftime('%Y%m%d')}", f"https://www.{SITE}/robots.txt",
                    ext="txt", max_tries=2)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(txt.splitlines())
        agents = ["ClaudeBot", "Claude-Web", "anthropic-ai", "GPTBot", "*", config.USER_AGENT]
        allowed = {a: rp.can_fetch(a, f"https://www.{SITE}/threads/") for a in agents}
    except requests.HTTPError as e:
        allowed = {"error": str(e)[:120],
                   "manual_check_2026-10-13": "ClaudeBot, Claude-Web, anthropic-ai, GPTBot and other AI "
                                              "agents: Disallow /; '*': allowed except account/search paths"}
    db.upsert_record(conn, {
        "id": f"robots:{SITE}", "kind": "robots_check", "source": "wayback", "title": f"{SITE} robots.txt",
        "year": int(time.strftime("%Y")), "url": f"https://www.{SITE}/robots.txt",
        "meta_json": json.dumps({"checked": time.strftime("%Y-%m-%d"), "can_fetch_threads": allowed,
                                 "decision": "no page content fetched; AI agents disallowed by name"}),
        "raw_path": None})
    return allowed


def run(conn):
    robots = _robots(conn)
    # Replays from the first page every run: cached pages are free, so the target DB always gets the
    # whole index; only uncached pages count toward MAX_CALLS.
    st = {}
    resume, calls, n = None, 0, 0
    while True:
        params = [("url", f"{SITE}/*"), ("output", "json"), ("collapse", "urlkey"), ("limit", PAGE),
                  ("showResumeKey", "true"), ("filter", "statuscode:200"), ("filter", "mimetype:text/html"),
                  ("fl", "urlkey,timestamp,original,mimetype,statuscode,digest")]
        if resume:
            params.append(("resumeKey", resume))
        key = f"cdx_{SITE}_{resume or 'start'}"
        cached = raw_path("wayback", key).exists()
        if not cached and calls >= MAX_CALLS:
            break
        txt = fetch("wayback", key, CDX, params)
        calls += 0 if cached else 1
        try:
            rows = json.loads(txt) if txt.strip() else []
        except json.JSONDecodeError:  # "Temporarily Offline" HTML page served with 200
            raw_path("wayback", key).unlink(missing_ok=True)
            st.update({"resume_key": resume, "count": n, "last_error": "archive offline"})
            save_status("wayback", st)
            raise RuntimeError("Internet Archive returned a non-JSON page (temporarily offline); rerun later")
        if not rows:
            st["complete"] = True
            break
        header, body = rows[0], rows[1:]
        resume = None
        if len(body) >= 2 and body[-2] == []:
            resume = body[-1][0]
            body = body[:-2]
        rp = str(raw_path("wayback", key).relative_to(config.ROOT))
        for r in body:
            rec = dict(zip(header, r))
            db.upsert_record(conn, {
                "id": f"wayback:{rec['urlkey']}", "kind": "archive_capture", "source": "wayback",
                "title": None, "year": int(rec["timestamp"][:4]), "url": rec["original"],
                "meta_json": json.dumps({"first_capture": rec["timestamp"], "mimetype": rec["mimetype"],
                                         "status": rec["statuscode"], "digest": rec["digest"],
                                         "wayback_url": f"https://web.archive.org/web/{rec['timestamp']}/{rec['original']}"}),
                "raw_path": rp})
        n += len(body)
        st.update({"resume_key": resume, "count": n})
        if not cached:
            conn.commit()
            save_status("wayback", st)
            log("wayback", f"{n} captures indexed")
        if not resume:
            st["complete"] = True
            break
    conn.commit()
    save_status("wayback", st)
    if not st.get("complete"):
        raise BudgetExhausted(f"WAYBACK_MAX_CALLS={MAX_CALLS} reached at {n} captures; rerun resumes")
    return {"robots_can_fetch": robots, "captures": n, "complete": True}
