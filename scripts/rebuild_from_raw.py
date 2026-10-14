"""Rebuild authorships (and refresh works) from the raw/ cache without network calls.

Used after the authorship column-order bug of 2026-10-13; safe to run any time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
from harvest import openalex, pubmed  # noqa: E402
from harvest.common import load_status  # noqa: E402

conn = db.connect()
conn.execute("DELETE FROM authorships")
st = load_status("pubmed")
tags = {}  # tags are already on the works; authorship rebuild does not need them
n = sum(pubmed.load_batch(conn, k, tags) for k in st.get("batches", []))
conn.commit()
print("pubmed articles replayed", n)
print("openalex works replayed", openalex.replay_raw(conn))
print(conn.execute("SELECT COUNT(*), SUM(author_id IS NOT NULL) FROM authorships").fetchone())
