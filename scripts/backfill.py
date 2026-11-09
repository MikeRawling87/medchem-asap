#!/usr/bin/env python3
"""One-off (re-runnable) backfill of the last N months into docs/archive/.

Metadata from Crossref (every journal deposits there), abstracts from Crossref or OpenAlex, graphics where a
predictable or fetchable source exists. Only journals switched on by default are backfilled.
Run:  python scripts/backfill.py [months=12]
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import archive  # noqa: E402
import classify  # noqa: E402
import fetch  # noqa: E402

ISSN = {
    "acie": "1521-3773", "cmc": "1860-7187", "nchem": "1755-4349", "ncat": "2520-1158", "nsyn": "2731-0582",
    "chem": "2451-9294", "nature": "1476-4687", "science": "1095-9203", "ncomms": "2041-1723", "sciadv": "2375-2548",
    "nrc": "2397-3358", "ccr": "0010-8545", "trchem": "2589-5974", "nmat": "1476-4660", "nnano": "1748-3395",
    "nenergy": "2058-7546", "nce": "2948-1198", "rscmc": "2632-8682", "ejmc": "0223-5234", "bmcl": "0960-894X",
    "bmc": "0968-0896",
}
SELECT = "DOI,title,author,abstract,created,published-online,posted,license,type,alternative-id,subject,group-title"
log = fetch.log


def crossref_all(path, since, until, extra=""):
    out, cursor, pages = [], "*", 0
    while cursor and pages < 60:
        url = (f"https://api.crossref.org/{path}/works?rows=1000&cursor={urllib.parse.quote(cursor)}"
               f"&filter=from-created-date:{since},until-created-date:{until}{extra}&select={SELECT}")
        for attempt in range(4):
            try:
                msg = json.loads(fetch.get(url, ua=fetch.API_UA, accept="application/json", timeout=120))["message"]
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    log(f"  ! {path}: page {pages} failed: {e}")
                    return out
                time.sleep(5 * (attempt + 1))
        items = msg.get("items", [])
        out += items
        pages += 1
        if len(items) < 1000:
            break
        cursor = msg.get("next-cursor")
    return out


def to_item(m, j):
    doi = (m.get("DOI") or "").lower()
    lic = " ".join(l.get("URL", "") for l in m.get("license", []))
    xdate = fetch.online_date(m)
    it = {
        "title": fetch.clean_title((m.get("title") or [""])[0]), "doi": doi, "url": f"https://doi.org/{doi}",
        "abstract": fetch.strip_html(m.get("abstract", "")),
        "authors": [f"{a.get('given','')} {a.get('family','')}".strip() or a.get("name", "") for a in m.get("author", [])],
        "date": xdate, "xdate": xdate, "journal": j["id"], "oa": "creativecommons" in lic, "imgs": [],
        "types": [j.get("kind")] if j.get("kind") == "Preprint" else [],
        "subjects": ([m.get("group-title")] if m.get("group-title") else []) + (m.get("subject") or []),
        "tries": 99, "img_tries": 0,
    }
    pii = next((a for a in m.get("alternative-id", []) if re.match(r"S\d{4}", a)), None)
    if pii:
        it["_link"] = f"https://www.sciencedirect.com/science/article/pii/{pii}"
    rm = re.match(r"10\.1039/([a-z]\d([a-z]{2})\d+[a-z]?)$", doi)
    if rm and xdate:
        it["_link"] = f"https://pubs.rsc.org/en/Content/ArticleLanding/{xdate[:4]}/{rm.group(2).upper()}/{rm.group(1)}"
    it["id"] = doi or fetch.make_id(it)
    it["first_seen"] = xdate or fetch.iso(fetch.NOW)
    return it


def journal_items(j, since, until):
    f = j.get("feed", "")
    if f == "chemrxiv":
        raw = crossref_all("prefixes/10.26434", since, until, ",type:posted-content")
        raw = [m for m in raw if re.search(r"/v1$", m.get("DOI", ""))]
    elif f.startswith("acs:"):
        raw = crossref_all(f"journals/{fetch.ACS_ISSN[f[4:]]}", since, until)
    elif j["id"] in ISSN:
        raw = crossref_all(f"journals/{ISSN[j['id']]}", since, until)
    else:
        log(f"  - {j['short']}: no ISSN, skipped")
        return []
    raw = [m for m in raw if m.get("type") in ("journal-article", "posted-content")]
    log(f"  {j['short']}: {len(raw)} records")
    return [to_item(m, j) for m in raw if m.get("DOI")]


def openalex_abstracts(items):
    need = [x for x in items if not x["abstract"] and x["doi"]]
    log(f"OpenAlex abstracts for {len(need)} papers…")
    def batch(chunk):
        q = "|".join(x["doi"] for x in chunk)
        url = (f"https://api.openalex.org/works?filter=doi:{urllib.parse.quote(q, safe='|/:')}"
               f"&per-page=50&select=doi,abstract_inverted_index")
        for attempt in range(3):
            try:
                res = json.loads(fetch.get(url, ua=fetch.API_UA, accept="application/json", timeout=60))["results"]
                break
            except Exception:  # noqa: BLE001
                time.sleep(3 * (attempt + 1))
        else:
            return
        by = {(r.get("doi") or "").lower().replace("https://doi.org/", ""): r for r in res}
        for x in chunk:
            inv = (by.get(x["doi"]) or {}).get("abstract_inverted_index")
            if inv:
                pos = {i: w for w, idx in inv.items() for i in idx}
                x["abstract"] = " ".join(pos[i] for i in sorted(pos))
    chunks = [need[i:i + 40] for i in range(0, len(need), 40)]
    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(batch, chunks))


def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    archive.ADIR = os.path.join(fetch.DOCS, "archive")
    until = fetch.NOW.date()
    since = (fetch.NOW - dt.timedelta(days=round(months * 30.5))).date()
    jmap = {j["id"]: j for j in fetch.JOURNALS}
    targets = [j for j in fetch.JOURNALS if j.get("on")]
    log(f"Backfilling {len(targets)} journals from {since} to {until}")

    live = set()
    if os.path.exists(fetch.FEED_OUT):
        live = {x.get("doi") for x in json.load(open(fetch.FEED_OUT))["items"] if x.get("doi")}

    items = []
    with cf.ThreadPoolExecutor(3) as ex:
        for got in ex.map(lambda j: journal_items(j, since.isoformat(), until.isoformat()), targets):
            items += got
    seen = set()
    items = [x for x in items if not (x["doi"] in live or x["doi"] in seen or seen.add(x["doi"]))]
    log(f"{len(items)} papers to consider")

    openalex_abstracts(items)
    finals = [fetch.finalize(x, jmap) for x in items]
    finals = [x for x in finals if x["date"][:10] >= since.isoformat()]   # drop re-deposited old papers
    keep = [x for x in finals if x["score"] >= classify.BORDERLINE]
    log(f"{len(keep)} kept ({sum(x['score'] >= classify.RELEVANT for x in keep)} relevant)")

    ca = fetch.chemasap_images()
    for x in keep:
        if x["doi"] in ca:
            x["imgs"] = [ca[x["doi"]]]
    fetch.add_graphics(keep)
    archive.add(keep)

    cov = {}
    for x in keep:
        c = cov.setdefault(x["journal"], [0, 0, 0])
        c[0] += 1
        c[1] += bool(x["imgs"])
        c[2] += bool(x["abstract"])
    log("Archive coverage (papers/graphics/abstracts): " +
        ", ".join(f"{jmap[k]['short']} {v[0]}/{v[1]}/{v[2]}" for k, v in sorted(cov.items())))


if __name__ == "__main__":
    main()
