"""Local query-family matching for sources that cannot express the section 1 queries
server-side (date walks, plain keyword APIs). Returns tags used for scope filtering."""
import re

import config

DPDR_RX = re.compile(
    r"depersonali[sz]|dereali[sz]|\bDPDR\b|feelings? of unreality|dissociative detachment|"
    r"cortico-?limbic disconnection|dépersonnalis|déréalis|деперсонализ|дереализ|Entfremdungserleben|"
    r"anaesthesia psychica", re.I)
TIME_RX = re.compile(
    r"time perception|temporal perception|perception of time|temporal experience|experience of time|"
    r"subjective time|lived time|passage of time|duration estimation|time estimation|interval timing|"
    r"temporal binding|intentional binding|sensorimotor synchroni[sz]ation|temporal disintegration|"
    r"perception du temps|temps vécu|Zeiterleben|Zeitwahrnehmung", re.I)
CLIN_RX = re.compile(
    r"psychopath|psychiatr|depress|anxi|dissociat|schizo|\bPTSD\b|posttraumatic|post-traumatic|psychedel|"
    r"psilocybin|ketamine|long COVID|\bpatients?\b|clinical|therap|interocept|sense of agency|depersonali|"
    r"dopamin|dépress|psychiatri|schizophr", re.I)
RU_RX = re.compile(r"[а-яё]", re.I)


def families(*texts):
    t = " ".join(x for x in texts if x)
    tags = []
    if DPDR_RX.search(t):
        tags.append("dpdr")
    if TIME_RX.search(t) and CLIN_RX.search(t):
        tags.append("time")
    if tags and RU_RX.search(t):
        tags.append("ru")
    return tags


def core_phrases():
    """Plain phrases of the DPDR core union (MeSH alternatives dropped)."""
    return [a for g in config.DPDR_CORE_UNION for a in g if isinstance(a, str)]


def time_phrase_groups():
    """Section 1.2 queries as lists of OR-groups of plain phrases."""
    out = []
    for label, q in config.TIME_TERMS:
        out.append((label, [[a for a in g if isinstance(a, str)] for g in q]))
    return out


def lucene(groups, field=None):
    """Render OR-groups joined by AND as a Lucene/Elasticsearch query string."""
    def term(p):
        p = f'"{p}"' if re.search(r"[\s\-]", p) else p
        return f"{field}:{p}" if field else p
    return " AND ".join("(" + " OR ".join(term(p) for p in g) + ")" for g in groups)
