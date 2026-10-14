"""Paths, credentials and the query definitions from spec section 1.

Queries are written once in a tiny DSL and rendered per source, so PubMed,
Europe PMC and OpenAlex always search for the same thing.

A query is a list of AND-groups; each group is a list of OR-alternatives.
An alternative is a phrase, or a dict {"mesh": "<descriptor name>"} which only
PubMed / Europe PMC can express.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
FULLTEXT = ROOT / "fulltext"
EXPORTS = ROOT / "exports"


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()

# DPDR_DB lets module tests write to a scratch database while sharing the raw/ cache.
DB_PATH = Path(os.environ.get("DPDR_DB", ROOT / "corpus.db"))
# Test runs against a scratch DB keep their checkpoints next to it, not in status/.
STATUS = Path(os.environ.get("DPDR_STATUS", ROOT / "status" if "DPDR_DB" not in os.environ
                             else DB_PATH.parent / f"{DB_PATH.stem}_status"))

# Contact address for polite pools. Deliberately empty unless set in .env / env.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
USER_AGENT = "dpdr-corpus-harvest/0.1 (academic literature review" + (
    f"; mailto:{CONTACT_EMAIL})" if CONTACT_EMAIL else ")")

KEYS = {
    "openalex": os.environ.get("OPENALEX_API_KEY", ""),
    "ncbi": os.environ.get("NCBI_API_KEY", ""),
    "s2": os.environ.get("S2_API_KEY", ""),
    "core": os.environ.get("CORE_API_KEY", ""),
    "youtube": os.environ.get("YOUTUBE_API_KEY", ""),
    "podcastindex_key": os.environ.get("PODCASTINDEX_KEY", ""),
    "podcastindex_secret": os.environ.get("PODCASTINDEX_SECRET", ""),
    "x_bearer": os.environ.get("X_BEARER_TOKEN", ""),
    "base": os.environ.get("BASE_API_KEY", ""),
}

YEAR_FROM, YEAR_TO = 1990, 2026

# Verified 2026-10-13 via NCBI esearch/esummary db=mesh.
MESH_IDS = {
    "Depersonalization": "D003861",
    "Dissociative Disorders": "D004213",
    "Time Perception": "D013998",
}

DP = ["depersonalization", "depersonalisation"]
DR = ["derealization", "derealisation"]


def both(phrase):
    """Return both z/s spellings of a phrase."""
    out = {phrase}
    for z, s in (("ization", "isation"), ("izing", "ising")):
        out |= {p.replace(z, s) for p in list(out)} | {p.replace(s, z) for p in list(out)}
    return sorted(out)


def q_and(term, *groups):
    return [DP, *groups] if term == "dp" else list(groups)


# Section 1.1. `core` marks terms that define the DPDR union; non-core terms
# are reported separately because they are dominated by off-scope literature.
DPDR_TERMS = [
    ("depersonali(z|s)ation", [DP], True),
    ("dereali(z|s)ation", [DR], True),
    ('"Depersonalization"[MeSH]', [[{"mesh": "Depersonalization"}]], True),
    ('"Dissociative Disorders"[MeSH]', [[{"mesh": "Dissociative Disorders"}]], False),
    ('"depersonalization-derealization disorder"', [both("depersonalization-derealization disorder")
                                                    + both("depersonalization derealization disorder")], True),
    ('"depersonali(z|s)ation disorder"', [both("depersonalization disorder")], True),
    ("DPDR", [["DPDR"]], True),
    ('"chronic depersonalization"', [both("chronic depersonalization")], True),
    ('"feelings of unreality"', [["feelings of unreality", "feeling of unreality"]], True),
    ('"dissociative detachment"', [["dissociative detachment"]], True),
    ('"Cambridge Depersonalisation Scale"', [both("Cambridge Depersonalization Scale")], True),
    ('"corticolimbic disconnection"', [["corticolimbic disconnection", "cortico-limbic disconnection"]], True),
    ("interoception AND depersonalization", q_and("dp", ["interoception", "interoceptive"]), True),
    ("insula AND depersonalization", q_and("dp", ["insula", "insular"]), True),
    ('"predictive processing" AND depersonalization', q_and("dp", ["predictive processing", "predictive coding"]), True),
    ('"minimal self" AND depersonalisation', q_and("dp", ["minimal self"]), True),
    ("ketamine AND dissociation", [["ketamine"], ["dissociation", "dissociative"]], False),
    ('"cannabis-induced depersonalization"', [both("cannabis-induced depersonalization")
                                              + both("cannabis induced depersonalization")], True),
    ("lamotrigine AND depersonalization", q_and("dp", ["lamotrigine"]), True),
    ("naltrexone AND depersonalization", q_and("dp", ["naltrexone"]), True),
    ("rTMS AND depersonalization", q_and("dp", ["rTMS", "transcranial magnetic stimulation"]), True),
    ("tDCS AND depersonalization", q_and("dp", ["tDCS", "transcranial direct current stimulation"]), True),
    ("CBT AND depersonalisation", q_and("dp", ["CBT", "cognitive behavioural therapy", "cognitive behavioral therapy"]), True),
    ("depersonalization AND COVID", q_and("dp", ["COVID", "COVID-19", "SARS-CoV-2", "pandemic"]), True),
    ("depersonalization AND adolescents", q_and("dp", ["adolescent", "adolescents", "adolescence"]), True),
    ("autonomic AND depersonalization", q_and("dp", ["autonomic"]), True),
    ("HPA AND depersonalization", q_and("dp", ["HPA axis", "hypothalamic-pituitary-adrenal", "cortisol"]), True),
]

# Union of the core 1.1 terms. Every other core term is a subset of these
# groups, so the union only needs the non-subset ones.
DPDR_CORE_UNION = [[*DP, *DR, {"mesh": "Depersonalization"}, "DPDR",
                    "feelings of unreality", "feeling of unreality", "dissociative detachment",
                    "corticolimbic disconnection", "cortico-limbic disconnection"]]

# Section 1.2.
TP_GROUP = ["time perception", "temporal perception", {"mesh": "Time Perception"}]
TIME_TERMS = [
    ("time perception x psychopathology",
     [TP_GROUP, [*DP, "dissociation", "dissociative", "depression", "depressive", "anxiety", "PTSD",
                 "posttraumatic", "schizophrenia", "psychedelic", "psilocybin", "ketamine", "long COVID"]]),
    ('"temporal experience" x dissociation/psychopathology',
     [["temporal experience", "experience of time"], ["dissociation", "psychopathology", "psychiatric"]]),
    ('"subjective time" x psychopathology', [["subjective time"], ["psychopathology", "psychiatric", "depression"]]),
    ('"temporal disintegration"', [["temporal disintegration"]]),
    ('"lived time"', [["lived time"]]),
    ('"passage of time judgment"', [["passage of time judgment", "passage of time judgement", "passage of time judgments"]]),
    ('"duration estimation" x depression/anxiety', [["duration estimation", "time estimation"], ["depression", "anxiety"]]),
    ('interoception x "time perception"', [["interoception", "interoceptive"], ["time perception"]]),
    ('"temporal binding" x "sense of agency"', [["temporal binding", "intentional binding"], ["sense of agency"]]),
    ('"time perception" x intervention', [["time perception"], ["intervention", "training", "rehabilitation", "therapy"]]),
    ('"sensorimotor synchronization" x clinical', [both("sensorimotor synchronization"), ["therapy", "clinical", "patients"]]),
    ('"interval timing" x dopamine x clinical', [["interval timing"], ["dopamine"], ["clinical", "patients"]]),
]

# Section 1.3. Europe PMC and OpenAlex index some non-English abstracts.
FOREIGN_TERMS = [
    ("ru", [["деперсонализация", "деперсонализации", "дереализация", "дереализации",
             "психическая анестезия", "anaesthesia psychica dolorosa"]]),
    ("de", [["Depersonalisation", "Derealisation", "Depersonalisationsstörung", "Entfremdungserleben"]]),
]

# Exclusion filters (section 1). Word-boundary regexes, NOT naive substrings:
# a case-insensitive substring "mbi" matches "combined", "ambiguous", "zombie".
EXCLUDE_BURNOUT = [
    r"burn-?out", r"\bmaslach", r"\bMBI\b", r"\bcynicism", r"emotional exhaustion",
    r"job satisfaction", r"workload", r"\bnurs", r"teacher", r"customer",
    r"dehumani[sz]",
]
EXCLUDE_TIME_UX = [r"\bqueue", r"waiting time", r"\bconsumers?\b", r"user interface", r"\blatency"]
