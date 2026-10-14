"""Query families shared by the literature harvesters: (tag, query DSL, priority).

Tags travel with every record (works.query_tags) so scope decisions stay
reversible: `dpdr` is the core union; `dissoc_mesh` and `ketamine_dissoc` are
broad families the spec asks to post-filter; `time` is section 1.2; `ru`/`de`
section 1.3.
"""
import config

FAMILIES = [("dpdr", config.DPDR_CORE_UNION)]
FAMILIES += [(f"dpdr_term:{label}", q) for label, q, core in config.DPDR_TERMS if not core]
FAMILIES += [("time", q) for _, q in config.TIME_TERMS]
FAMILIES += [(lang, q) for lang, q in config.FOREIGN_TERMS]

TAG_ALIASES = {
    'dpdr_term:"Dissociative Disorders"[MeSH]': "dissoc_mesh",
    "dpdr_term:ketamine AND dissociation": "ketamine_dissoc",
}


def families():
    """Yield (tag, query) with stable short tags."""
    seen = {}
    for tag, q in FAMILIES:
        tag = TAG_ALIASES.get(tag, tag)
        n = seen.get(tag, 0)
        seen[tag] = n + 1
        yield (tag if tag != "time" else f"time{n}"), q


def family_tag(key):
    """time3 -> time; everything else unchanged."""
    return "time" if key.startswith("time") and key[4:].isdigit() else key
