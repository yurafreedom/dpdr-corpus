"""Scope filters applied at normalization (section 1). Matches are flagged, never deleted."""
import re

import config

_BURNOUT = [(p, re.compile(p, re.I)) for p in config.EXCLUDE_BURNOUT]
_TIME_UX = [(p, re.compile(p, re.I)) for p in config.EXCLUDE_TIME_UX]
_NAIVE = ["burnout", "maslach", "mbi", "cynicism", "emotional exhaustion", "job satisfaction",
          "workload", "nurs", "teacher", "customer", "dehumaniz"]


def _text(title, abstract, keywords):
    kw = " ; ".join(k for k in keywords if k) if isinstance(keywords, (list, tuple)) else (keywords or "")
    return " \n ".join(x for x in (title or "", abstract or "", kw) if x)


def burnout_hits(title, abstract, keywords=()):
    t = _text(title, abstract, keywords)
    return [p for p, rx in _BURNOUT if rx.search(t)]


def exclusion_reason(title, abstract, keywords=(), *, time_query=False):
    """Return a list of matched patterns (empty = in scope)."""
    hits = burnout_hits(title, abstract, keywords)
    if time_query:
        t = _text(title, abstract, keywords)
        hits += [p for p, rx in _TIME_UX if rx.search(t)]
    return hits


def naive_reason(title, abstract, keywords=()):
    """The spec's literal case-insensitive substring filter, kept for comparison only."""
    t = _text(title, abstract, keywords).lower()
    return [s for s in _NAIVE if s in t]
