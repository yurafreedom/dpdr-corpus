"""YouTube Data API v3 (spec section 9). Needs YOUTUBE_API_KEY.

search.list costs 100 units per call, commentThreads.list 1 unit. A daily
counter in status/youtube_quota.json stops at YOUTUBE_QUOTA_CAP (default 9000
of the free 10,000). Comments are collected only for videos of channels
listed in harvest/youtube_channels.txt (clinicians/researchers).

Ethics: comment authors are stored as their opaque channel id only.
"""
import json
import os
import time
from pathlib import Path

import config
import db
from harvest.common import SkipModule, fetch_json, log, raw_path, load_status, save_status

API = "https://www.googleapis.com/youtube/v3"
QUERIES = ["depersonalization", "depersonalisation", "derealization", "depersonalization disorder",
           "DPDR", "depersonalisation derealisation disorder", "time perception psychology"]
CAP = int(os.environ.get("YOUTUBE_QUOTA_CAP", "9000"))
PAGES = int(os.environ.get("YOUTUBE_SEARCH_PAGES", "4"))
CHANNELS_FILE = Path(__file__).with_name("youtube_channels.txt")


class _Quota:
    def __init__(self):
        self.day = time.strftime("%Y-%m-%d")
        st = load_status("youtube_quota")
        self.used = st.get("used", 0) if st.get("day") == self.day else 0

    def spend(self, units):
        if self.used + units > CAP:
            return False
        self.used += units
        save_status("youtube_quota", {"day": self.day, "used": self.used, "cap": CAP})
        return True


def _channels():
    if not CHANNELS_FILE.exists():
        return []
    return [l.split("#")[0].strip() for l in CHANNELS_FILE.read_text().splitlines() if l.split("#")[0].strip()]


def run(conn):
    key = config.KEYS["youtube"]
    if not key:
        raise SkipModule("needs YOUTUBE_API_KEY")
    q = _Quota()
    videos = 0
    for query in QUERIES:
        token = None
        for _ in range(PAGES):
            ck = f"search_{query}_{token or 'first_' + time.strftime('%Y%m%d')}"
            if not raw_path("youtube", ck).exists() and not q.spend(100):
                return {"videos": videos, "stopped": "quota cap", "used": q.used}
            params = {"part": "snippet", "q": query, "type": "video", "maxResults": 50, "key": key}
            if token:
                params["pageToken"] = token
            d = fetch_json("youtube", ck, f"{API}/search", params)
            rp = str(raw_path("youtube", ck).relative_to(config.ROOT))
            for it in d.get("items", []):
                sn = it["snippet"]
                db.upsert_record(conn, {
                    "id": f"youtube:{it['id']['videoId']}", "kind": "youtube_video", "source": "youtube",
                    "title": sn.get("title"), "year": int(sn["publishedAt"][:4]),
                    "url": f"https://www.youtube.com/watch?v={it['id']['videoId']}",
                    "meta_json": json.dumps({"channelId": sn.get("channelId"), "channelTitle": sn.get("channelTitle"),
                                             "description": sn.get("description"), "publishedAt": sn.get("publishedAt"),
                                             "query": query}, ensure_ascii=False),
                    "raw_path": rp})
                videos += 1
            token = d.get("nextPageToken")
            if not token:
                break
        conn.commit()
    comments = 0
    chans = set(_channels())
    if chans:
        rows = conn.execute("SELECT id, meta_json FROM records WHERE kind='youtube_video'").fetchall()
        for rid, meta in rows:
            if json.loads(meta).get("channelId") not in chans:
                continue
            vid = rid.split(":", 1)[1]
            ck = f"comments_{vid}"
            if not raw_path("youtube", ck).exists() and not q.spend(1):
                break
            d = fetch_json("youtube", ck, f"{API}/commentThreads",
                           {"part": "snippet", "videoId": vid, "maxResults": 100, "textFormat": "plainText",
                            "key": key}, ok_statuses=(200, 403))
            for it in d.get("items", []):
                top = it["snippet"]["topLevelComment"]["snippet"]
                db.upsert_post(conn, {
                    "id": f"youtube:c:{it['id']}", "platform": "youtube",
                    "author_id": (top.get("authorChannelId") or {}).get("value"),
                    "created": top.get("publishedAt"), "text": top.get("textDisplay"),
                    "metrics_json": json.dumps({"likeCount": top.get("likeCount"),
                                                "replies": it["snippet"].get("totalReplyCount")}),
                    "url": f"https://www.youtube.com/watch?v={vid}&lc={it['id']}",
                    "raw_path": str(raw_path("youtube", ck).relative_to(config.ROOT)), "community": vid,
                }, tags=["youtube:comments"])
                comments += 1
        conn.commit()
    log("youtube", f"videos={videos} comments={comments} quota_used={q.used}")
    return {"videos": videos, "comments": comments, "quota_used_today": q.used}
