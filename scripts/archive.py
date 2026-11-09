"""Monthly archive files: docs/archive/YYYY-MM.json + docs/archive/index.json.

The live feed (docs/data.json) holds the last KEEP_DAYS days. Older papers live here, one file per month of
online publication, so the app only downloads months you actually browse.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADIR = os.path.join(ROOT, "docs", "archive")


def slim(x):
    return {k: v for k, v in x.items() if not k.startswith("_")}


def load_month(m):
    p = os.path.join(ADIR, f"{m}.json")
    if os.path.exists(p):
        return {x["id"]: x for x in json.load(open(p))["items"]}
    return {}


def add(items, adir=None):
    """Merge papers into their month files (by online date). Existing entries are updated in place."""
    global ADIR
    if adir:
        ADIR = adir
    os.makedirs(ADIR, exist_ok=True)
    by_month = {}
    for x in items:
        by_month.setdefault(x["date"][:7], []).append(slim(x))
    for m, xs in by_month.items():
        cur = load_month(m)
        dois = {v["doi"]: k for k, v in cur.items() if v.get("doi")}
        for x in xs:
            k = dois.get(x.get("doi")) if x.get("doi") else None
            cur[k or x["id"]] = x
        items_sorted = sorted(cur.values(), key=lambda v: v["date"], reverse=True)
        with open(os.path.join(ADIR, f"{m}.json.tmp"), "w") as f:
            json.dump({"month": m, "items": items_sorted}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(os.path.join(ADIR, f"{m}.json.tmp"), os.path.join(ADIR, f"{m}.json"))
    write_index()


def write_index():
    months = {}
    for f in sorted(os.listdir(ADIR)):
        if f.endswith(".json") and f[:4].isdigit():
            n = len(json.load(open(os.path.join(ADIR, f)))["items"])
            months[f[:7]] = {"n": n, "bytes": os.path.getsize(os.path.join(ADIR, f))}
    with open(os.path.join(ADIR, "index.json"), "w") as fh:
        json.dump({"months": months}, fh, separators=(",", ":"))


def prune(before_month):
    """Drop month files older than before_month (YYYY-MM)."""
    if not os.path.isdir(ADIR):
        return
    for f in os.listdir(ADIR):
        if f[:4].isdigit() and f[:7] < before_month:
            os.remove(os.path.join(ADIR, f))
    write_index()
