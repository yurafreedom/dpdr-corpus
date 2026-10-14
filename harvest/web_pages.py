"""Patient-organisation pages and forum boards (spec section 9).

Unreal: the spec's dpdr.org.uk does not resolve; unrealuk.org redirects to
www.unrealcharity.com (verified 2026-10-13). Its robots.txt (Squarespace)
allows everything except config/search/account/api/static and ?format= views.
This module fetches the home, blog (news, webinars and recruitment calls are
posted there), research and resources pages, plus blog posts linked from the
blog index, after checking each URL with urllib.robotparser.

General forums (Psych Forums, Mental Health Forum, HealthUnlocked): only
robots.txt and the existence of a DPDR board are recorded. Board content is
not harvested, since it consists of patients' personal stories (spec 0.5).
"""
import html
import json
import os
import re
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests

import config
import db
from harvest.common import fetch, log, raw_path

UNREAL = "https://www.unrealcharity.com"
UNREAL_PAGES = ["/", "/blog", "/research", "/resources", "/what-is-dpdr", "/treatment-care"]
MAX_POSTS = int(os.environ.get("UNREAL_MAX_POSTS", "60"))
FORUM_BOARDS = {
    "psychforums": ("https://www.psychforums.com", "https://www.psychforums.com/depersonalization/"),
    "mentalhealthforum": ("https://www.mentalhealthforum.net", "https://www.mentalhealthforum.net/"),
    "healthunlocked": ("https://healthunlocked.com", "https://healthunlocked.com/search/communities?query=depersonalisation"),
}


def _robots(base):
    key = f"robots_{urlparse(base).netloc}_{time.strftime('%Y%m%d')}"
    try:
        txt = fetch("web_pages", key, base + "/robots.txt", ext="txt", ok_statuses=(200, 404, 403), max_tries=3)
    except requests.RequestException as e:
        return None, str(e)[:120]
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(txt.splitlines())
    return rp, None


def _text(page):
    page = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    body = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return (html.unescape(title.group(1)).strip() if title else None), re.sub(r"\s+", " ", body).strip()


def _links(base, page):
    out = set()
    for h in re.findall(r'href="([^"#]+)"', page):
        u = urljoin(base, h)
        if u.startswith("http") and "squarespace" not in u and "/universal/" not in u:
            out.add(u)
    return sorted(out)


def _store_page(conn, url, page, key):
    title, text = _text(page)
    links = _links(url, page)
    db.upsert_record(conn, {
        "id": f"web:{url}", "kind": "web_page", "source": "web_pages", "title": title,
        "year": int(time.strftime("%Y")), "url": url,
        "meta_json": json.dumps({"fetched": time.strftime("%Y-%m-%d"), "text": text[:20000],
                                 "links": links}, ensure_ascii=False),
        "raw_path": str(raw_path("web_pages", key, "html").relative_to(config.ROOT))})
    return links


def run(conn):
    day = time.strftime("%Y%m%d")
    rp, err = _robots(UNREAL)
    fetched, blocked = [], []
    blog_posts = set()
    for path in UNREAL_PAGES:
        url = UNREAL + path
        if rp and not rp.can_fetch(config.USER_AGENT, url):
            blocked.append(url)
            continue
        key = f"unreal_{path.strip('/') or 'home'}_{day}"
        page = fetch("web_pages", key, url, ext="html", max_tries=3)
        links = _store_page(conn, url, page, key)
        fetched.append(url)
        if path == "/blog":
            blog_posts |= {l for l in links if urlparse(l).netloc == urlparse(UNREAL).netloc
                           and urlparse(l).path.startswith("/blog/") and "?" not in l}
    for url in sorted(blog_posts)[:MAX_POSTS]:
        if rp and not rp.can_fetch(config.USER_AGENT, url):
            blocked.append(url)
            continue
        key = "unreal_post_" + urlparse(url).path.strip("/").replace("/", "_")
        try:
            page = fetch("web_pages", key, url, ext="html", max_tries=3)
        except requests.RequestException as e:
            log("web_pages", f"{url}: {str(e)[:80]}")
            continue
        _store_page(conn, url, page, key)
        fetched.append(url)
    conn.commit()

    forums = {}
    for name, (base, board) in FORUM_BOARDS.items():
        frp, ferr = _robots(base)
        allowed = frp.can_fetch(config.USER_AGENT, board) if frp else None
        status = None
        if allowed:
            try:
                r = requests.head(board, headers={"User-Agent": config.USER_AGENT}, timeout=30, allow_redirects=True)
                status = r.status_code
            except requests.RequestException as e:
                status = str(e)[:80]
        forums[name] = {"board_url": board, "robots_allows": allowed, "robots_error": ferr, "head_status": status,
                        "content_harvested": False}
        db.upsert_record(conn, {"id": f"forum:{name}", "kind": "forum_check", "source": "web_pages",
                                "title": name, "year": int(time.strftime("%Y")), "url": board,
                                "meta_json": json.dumps(forums[name] | {"checked": time.strftime("%Y-%m-%d")}),
                                "raw_path": None})
    conn.commit()
    log("web_pages", f"unreal pages={len(fetched)} blocked={len(blocked)}; forums={forums}")
    return {"unreal_fetched": len(fetched), "unreal_blocked_by_robots": blocked, "robots_error": err,
            "forums": forums}
