"""Monographs and historical texts (spec section 12): metadata and links only.

Open Library (keyless), Google Books (keyless quota is shared and often spent;
GOOGLE_BOOKS_API_KEY raises it), Internet Archive advancedsearch for the
public-domain historical foundation (Krishaber, Dugas, Janet, Schilder, Mayer-Gross).
"""
import os

import requests

import db
from harvest.common import fetch_json, params_key, log
from harvest.textmatch import families

OL = "https://openlibrary.org/search.json"
GB = "https://www.googleapis.com/books/v1/volumes"
IA = "https://archive.org/advancedsearch.php"

OL_QUERIES = [
    {"q": "depersonalization"}, {"q": "depersonalisation"}, {"q": "derealization"},
    {"q": "derealisation"}, {"q": "feeling unreal"}, {"q": "деперсонализация"},
    {"q": "Depersonalisation Derealisation"}, {"q": "dépersonnalisation"},
    {"title": "Depersonalization: A New Look at a Neglected Syndrome"},
    {"title": "Feeling Unreal", "author": "Simeon"},
    {"title": "Overcoming Depersonalization and Feelings of Unreality"},
    {"author": "Mauricio Sierra"}, {"author": "Нуллер"}, {"author": "Nuller"},
]
GB_QUERIES = ["depersonalization", "depersonalisation", "derealization", "intitle:depersonalization",
              'intitle:"feeling unreal"', "inauthor:Nuller", "деперсонализация"]
IA_QUERIES = [
    "(title:(depersonalization OR depersonalisation OR dépersonnalisation OR Depersonalisation) "
    "OR subject:(depersonalization OR depersonalisation)) AND mediatype:texts",
    "creator:(Krishaber) AND mediatype:texts",
    "creator:(Dugas) AND (title:(dépersonnalisation OR déjà) OR subject:psychologie) AND mediatype:texts",
    "creator:(Janet, Pierre) AND mediatype:texts",
    "creator:(Schilder, Paul) AND mediatype:texts",
    "creator:(Mayer-Gross) AND mediatype:texts",
    "creator:(Oesterreich) AND mediatype:texts",
]


def _book_tags(*texts):
    return families(*texts) or ["dpdr_book_candidate"]


def run(conn):
    out = {"openlibrary": 0, "googlebooks": 0, "internetarchive": 0, "notes": []}
    for q in OL_QUERIES:
        p = dict(q, limit=100, fields="key,title,subtitle,author_name,first_publish_year,isbn,publisher,"
                                      "language,subject,ebook_access,ia,edition_count")
        key = params_key("search", p)
        d = fetch_json("openlibrary", key, OL, p)
        for doc in d.get("docs", []):
            title = doc.get("title", "") + (": " + doc["subtitle"] if doc.get("subtitle") else "")
            db.upsert_record(conn, {
                "id": f"openlibrary:{doc['key']}", "kind": "book", "source": "openlibrary",
                "title": title, "year": doc.get("first_publish_year"),
                "url": f"https://openlibrary.org{doc['key']}",
                "meta_json": {"authors": doc.get("author_name"), "isbn": (doc.get("isbn") or [])[:5],
                              "publisher": (doc.get("publisher") or [])[:3], "language": doc.get("language"),
                              "subjects": (doc.get("subject") or [])[:15], "ebook_access": doc.get("ebook_access"),
                              "ia": (doc.get("ia") or [])[:3], "query": q,
                              "tags": _book_tags(title, " ".join(doc.get("subject") or []))},
                "raw_path": f"raw/openlibrary/{key}.json",
            })
            out["openlibrary"] += 1

    gb_key = os.environ.get("GOOGLE_BOOKS_API_KEY", "")
    for q in GB_QUERIES:
        p = {"q": q, "maxResults": 40, "printType": "books"}
        if gb_key:
            p["key"] = gb_key
        key = params_key("volumes", {"q": q, "maxResults": 40})
        try:
            d = fetch_json("googlebooks", key, GB, p, max_tries=2)
        except requests.HTTPError as e:
            out["notes"].append(f"Google Books stopped at {q!r}: {str(e)[:120]} (keyless daily quota is shared; "
                                "set GOOGLE_BOOKS_API_KEY)")
            break
        for it in d.get("items", []):
            v = it.get("volumeInfo", {})
            db.upsert_record(conn, {
                "id": f"googlebooks:{it['id']}", "kind": "book", "source": "googlebooks",
                "title": v.get("title", "") + (": " + v["subtitle"] if v.get("subtitle") else ""),
                "year": int(v["publishedDate"][:4]) if (v.get("publishedDate") or "")[:4].isdigit() else None,
                "url": v.get("infoLink"),
                "meta_json": {"authors": v.get("authors"), "publisher": v.get("publisher"),
                              "isbn": v.get("industryIdentifiers"), "language": v.get("language"),
                              "categories": v.get("categories"), "description": v.get("description"),
                              "access": it.get("accessInfo", {}).get("accessViewStatus"), "query": q,
                              "tags": _book_tags(v.get("title"), v.get("description"))},
                "raw_path": f"raw/googlebooks/{key}.json",
            })
            out["googlebooks"] += 1

    for q in IA_QUERIES:
        p = {"q": q, "fl[]": ["identifier", "title", "creator", "year", "date", "language", "licenseurl",
                              "rights", "subject", "description", "possible-copyright-status"],
             "rows": 300, "page": 1, "output": "json"}
        key = params_key("advsearch", {"q": q, "rows": 300})
        d = fetch_json("internetarchive", key, IA, p)
        for doc in d.get("response", {}).get("docs", []):
            year = doc.get("year")
            try:
                year = int(str(year)[:4]) if year else None
            except ValueError:
                year = None
            db.upsert_record(conn, {
                "id": f"ia:{doc['identifier']}", "kind": "archive_text", "source": "internetarchive",
                "title": doc.get("title") if isinstance(doc.get("title"), str) else str(doc.get("title")),
                "year": year, "url": f"https://archive.org/details/{doc['identifier']}",
                "meta_json": {"creator": doc.get("creator"), "language": doc.get("language"),
                              "licenseurl": doc.get("licenseurl"), "rights": doc.get("rights"),
                              "copyright_status": doc.get("possible-copyright-status"),
                              "public_domain_likely": bool(year and year <= 1930), "query": q,
                              "subject": doc.get("subject")},
                "raw_path": f"raw/internetarchive/{key}.json",
            })
            out["internetarchive"] += 1
    log("books", str({k: v for k, v in out.items() if k != "notes"}))
    return out
