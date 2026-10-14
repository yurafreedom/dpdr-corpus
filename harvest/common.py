"""Shared HTTP layer: raw-first caching, per-host pacing, backoff, checkpoints.

Every response body is written verbatim to raw/<source>/<key>.<ext> before the
caller sees it. A second call with the same key reads the cache instead of the
network, which is what makes every harvester idempotent and resumable.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

import config

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT
_last_call = {}

# Minimum seconds between calls per source (documented limits, with margin).
PACE = {
    "pubmed": 0.12 if config.KEYS["ncbi"] else 0.4,
    "europepmc": 0.15,
    "openalex": 0.12,
    "crossref": 0.25,
    "unpaywall": 0.12,
    "opencitations": 0.5,
    "s2": 1.05,
    "clinicaltrials": 0.3,
    "isrctn": 1.0,
    "nih_reporter": 1.05,
    "ukri": 0.5,
    "orcid": 0.1,
    "bluesky": 0.3,
    "mastodon": 1.0,
    "biorxiv": 0.5,
    "osf": 0.5,
    "arxiv": 3.1,
    "itunes": 3.1,
    "doaj": 0.6,
    "openaire": 0.5,
    "wayback": 1.0,
    "openneuro": 0.5,
    "neurovault": 0.5,
    "openlibrary": 0.5,
    "googlebooks": 0.5,
    "internetarchive": 0.5,
    "thesesfr": 1.0,
    "dnb": 1.0,
    "neuroquery": 1.0,
    "neurosynth": 1.0,
    "cyberleninka": 2.0,
}


class BudgetExhausted(RuntimeError):
    pass


class SkipModule(Exception):
    """Raised by a module that needs a key or an explicit opt-in that is missing."""


def safe_key(key):
    key = re.sub(r"[^A-Za-z0-9._=-]+", "_", key)
    if len(key) > 120:
        key = key[:80] + "_" + hashlib.sha1(key.encode()).hexdigest()[:16]
    return key


def raw_path(source, key, ext="json"):
    return config.RAW / source / f"{safe_key(key)}.{ext}"


def _pace(source):
    gap = PACE.get(source, 0.5)
    wait = _last_call.get(source, 0) + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[source] = time.monotonic()


# OpenAlex meters keyless usage in credits ($0.10/day = 1000 credits; a search
# request costs 10, a filter list 1, a single-entity GET 0). Track the
# remaining budget from response headers and refuse to go below a reserve.
OPENALEX_RESERVE_USD = 0.005


def _openalex_budget(resp):
    rem = resp.headers.get("x-ratelimit-remaining-usd")
    if rem is None:
        return
    state = {
        "remaining_usd": float(rem),
        "limit_usd": float(resp.headers.get("x-ratelimit-limit-usd", 0) or 0),
        "reset_in_s": int(resp.headers.get("x-ratelimit-reset", 0) or 0),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "keyed": bool(config.KEYS["openalex"]),
    }
    state["resets_at_epoch"] = time.time() + state["reset_in_s"]
    save_status("openalex_budget", state)


def openalex_check_budget(cost_usd):
    st = load_status("openalex_budget")
    if not st:
        return
    if time.time() > st.get("resets_at_epoch", 0):
        return
    if st["remaining_usd"] - cost_usd < OPENALEX_RESERVE_USD:
        raise BudgetExhausted(
            f"OpenAlex daily budget nearly spent (${st['remaining_usd']:.4f} left, resets in "
            f"{int(st['resets_at_epoch'] - time.time())}s). Set OPENALEX_API_KEY or resume later.")


def fetch(source, key, url, params=None, *, ext="json", method="GET", json_body=None,
          headers=None, refresh=False, max_tries=6, ok_statuses=(200,), cost_usd=0.0, transform=None):
    """Return the response text for (url, params), cached at raw/<source>/<key>.<ext>.

    Non-OK statuses listed in ok_statuses are cached too (e.g. 404 for "no OA copy").
    `transform(text) -> text` runs before the body is cached or returned (e.g. pseudonymization),
    so the unmodified body never reaches disk.
    """
    path = raw_path(source, key, ext)
    if path.exists() and not refresh:
        return path.read_text()
    if source == "openalex" and cost_usd > 0:  # single-entity GETs are free
        openalex_check_budget(cost_usd)
    delay = 2.0
    for attempt in range(max_tries):
        _pace(source)
        try:
            resp = _session.request(method, url, params=params, json=json_body,
                                    headers=headers, timeout=90)
        except requests.RequestException as e:
            if attempt == max_tries - 1:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if source == "openalex":
            _openalex_budget(resp)
        if resp.status_code in ok_statuses:
            text = transform(resp.text) if transform else resp.text
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            return text
        if resp.status_code == 429 and source == "openalex" and "x-ratelimit-remaining-usd" in resp.headers \
                and float(resp.headers["x-ratelimit-remaining-usd"]) <= 0:
            raise BudgetExhausted("OpenAlex daily budget exhausted (HTTP 429)")
        if resp.status_code in (429, 500, 502, 503, 504):
            ra = resp.headers.get("Retry-After")
            sleep = float(ra) if ra and ra.isdigit() else delay
            time.sleep(min(sleep, 120))
            delay = min(delay * 2, 120)
            continue
        raise requests.HTTPError(f"{source} {resp.status_code} for {resp.url}: {resp.text[:300]}")
    raise requests.HTTPError(f"{source}: gave up after {max_tries} tries: {url}")


def fetch_json(source, key, url, params=None, **kw):
    return json.loads(fetch(source, key, url, params, **kw))


def params_key(prefix, params):
    """Stable cache key for a query: readable prefix + hash of the full params."""
    blob = urlencode(sorted((k, str(v)) for k, v in (params or {}).items()))
    return f"{prefix}_{hashlib.sha1(blob.encode()).hexdigest()[:12]}"


def load_status(name):
    p = config.STATUS / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_status(name, data):
    config.STATUS.mkdir(exist_ok=True)
    p = config.STATUS / f"{name}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


def log(source, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {source}: {msg}", flush=True)
