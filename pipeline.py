"""Stage runner (spec section 15).

    .venv/bin/python pipeline.py run              # all stages, resume where left off
    .venv/bin/python pipeline.py run --stage enrich
    .venv/bin/python pipeline.py run --module pubmed --force
    .venv/bin/python pipeline.py status

Module contract: harvest/<name>.py exposes `run(conn) -> dict` (a JSON-able
summary). It raises SkipModule when a key or explicit opt-in is missing, and
BudgetExhausted when a metered API is spent; the module is then marked
"partial" and retried on the next run. Every module must be idempotent: raw
responses are cached by harvest.common.fetch, so a rerun only redoes parsing.
"""
import argparse
import importlib
import sys
import time
import traceback

import db
from harvest.common import BudgetExhausted, SkipModule, load_status, save_status, log


STAGES = [
    ("seed", ["pubmed", "europepmc", "openalex_lookup", "openalex_seed", "cyberleninka", "doaj", "openaire", "core", "base"]),
    ("snowball", ["openalex_snowball"]),
    ("enrich", ["openalex_lookup", "crossref", "semanticscholar", "opencitations", "unpaywall", "retractions"]),
    ("fulltext", ["fulltext"]),
    ("preprints", ["biorxiv", "osf", "arxiv"]),
    ("trials_grants", ["clinicaltrials", "isrctn", "nih_reporter", "ukri", "cordis"]),
    ("people", ["people"]),
    ("other_sources", ["books", "neuro", "theses"]),
    ("social", ["bluesky", "mastodon", "arctic_shift", "reddit_api", "wayback", "podcasts", "youtube",
                "podcastindex", "x_timelines", "web_pages"]),
    ("analysis", ["normalize", "relevance", "embeddings_clusters", "exports"]),
]


def run_module(conn, stage, name, force=False):
    st = load_status(f"stage_{stage}")
    # "skipped" is retried every run: it is instant when a key is still missing,
    # and picks the module up once a key is added or a network failure clears.
    if not force and st.get(name, {}).get("status") == "done":
        return st[name]["status"]
    t0 = time.time()
    try:
        mod = importlib.import_module(f"harvest.{name}")
        log(name, "start")
        summary = mod.run(conn)
        conn.commit()
        res = {"status": "done", "summary": summary}
    except SkipModule as e:
        conn.commit()
        res = {"status": "skipped", "reason": str(e)}
    except BudgetExhausted as e:
        conn.commit()
        res = {"status": "partial", "reason": str(e)}
    except Exception as e:  # keep going; the error is recorded and the module retried next run
        conn.commit()
        res = {"status": "error", "reason": f"{type(e).__name__}: {e}",
               "trace": traceback.format_exc()[-2000:]}
    res["seconds"] = round(time.time() - t0, 1)
    res["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st = load_status(f"stage_{stage}")
    st[name] = res
    save_status(f"stage_{stage}", st)
    log(name, f"{res['status']} ({res['seconds']}s) {res.get('reason', '')[:200]}")
    return res["status"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "status"])
    ap.add_argument("--stage")
    ap.add_argument("--module")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="rerun every module, including finished ones (cheap: raw responses are cached), "
                         "so incremental modules pick up records added by later stages")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        for stage, mods in STAGES:
            st = load_status(f"stage_{stage}")
            print(f"{stage}:")
            for m in mods:
                s = st.get(m, {})
                print(f"  {m:22s} {s.get('status', '-'):8s} {s.get('reason', '')[:110]}")
        return
    conn = db.connect()
    for stage, mods in STAGES:
        if a.stage and stage != a.stage:
            continue
        for m in mods:
            if a.module and m != a.module:
                continue
            run_module(conn, stage, m, force=a.force or a.all or bool(a.module))
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
