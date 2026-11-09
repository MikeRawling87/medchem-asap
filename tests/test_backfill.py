import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch, backfill, archive
CR = {"message": {"items": [
  {"DOI": "10.1039/d6md00111a", "type": "journal-article", "title": ["Design of potent PROTAC degraders of BRD4"],
   "created": {"date-time": "2026-03-02T10:00:00Z"}, "published-online": {"date-parts": [[2026, 3, 1]]},
   "author": [{"given": "A", "family": "B"}]},
  {"DOI": "10.1016/j.ejmech.2026.100001", "type": "journal-article", "title": ["Quinazoline kinase inhibitors"],
   "created": {"date-time": "2026-01-10T10:00:00Z"}, "alternative-id": ["S0223523426000011"]},
  {"DOI": "10.1002/anie.2026", "type": "journal-article", "title": ["Perovskite solar cell"], "created": {"date-time": "2026-02-01T00:00:00Z"}}]}}
OA = {"results": [{"doi": "https://doi.org/10.1016/j.ejmech.2026.100001", "abstract_inverted_index": {"Potent": [0], "inhibitors": [1]}}]}
def fake(url, **kw):
    if "api.crossref.org" in url and ("2632-8682" in url):
        return json.dumps(CR).encode()
    if "api.crossref.org" in url:
        return json.dumps({"message": {"items": []}}).encode()
    if "openalex" in url:
        return json.dumps(OA).encode()
    raise fetch.urllib.error.HTTPError(url, 403, "x", {}, None)
fetch.get = fake
tmp = tempfile.mkdtemp(); fetch.DOCS = tmp; fetch.FEED_OUT = os.path.join(tmp, "none.json")
sys.argv = ["backfill", "12"]; backfill.main()
idx = json.load(open(os.path.join(tmp, "archive", "index.json"))); print(idx)
m = json.load(open(os.path.join(tmp, "archive", "2026-03.json")))["items"][0]
print(m["title"], m["date"], m["imgs"]); assert m["imgs"][0].endswith("d6md00111a-ga.gif") and m["date"].startswith("2026-03-01")
e = json.load(open(os.path.join(tmp, "archive", "2026-01.json")))["items"][0]
assert e["abstract"] == "Potent inhibitors" and e["imgs"][0].endswith("S0223523426000011-ga1.jpg"), e
# (perovskite record sits in a core journal in this fixture, so it is kept)
print("OK")
