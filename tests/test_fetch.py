"""Offline test: fake publisher responses, run the pipeline, check output."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import fetch  # noqa: E402

ACS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>JMC ASAP</title>
<item><title>[ASAP] Discovery of Potent, Orally Bioavailable KRAS G12D Inhibitors</title>
<link>http://dx.doi.org/10.1021/acs.jmedchem.5c01234</link>
<description>&lt;p&gt;&lt;img alt="TOC Graphic" src="https://pubs.acs.org/cms/10.1021/acs.jmedchem.5c01234/asset/images/medium/jm5c01234_0010.gif" /&gt;&lt;/p&gt;&lt;div&gt;&lt;cite&gt;Journal of Medicinal Chemistry&lt;/cite&gt;&lt;/div&gt;&lt;div&gt;DOI: 10.1021/acs.jmedchem.5c01234&lt;/div&gt;</description>
<pubDate>Thu, 06 Nov 2026 05:00:00 GMT</pubDate>
<dc:creator>Jane Smith, John Doe, and Ann Lee</dc:creator></item>
<item><title>[ASAP] Perovskite Solar Cells with Record Stability</title>
<link>http://dx.doi.org/10.1021/jacs.5c09999</link><description>x</description><pubDate>Thu, 06 Nov 2026 05:00:00 GMT</pubDate></item>
</channel></rss>"""

WILEY = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/"
 xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
<item rdf:about="https://onlinelibrary.wiley.com/doi/10.1002/anie.202512345?af=R">
<title>Late-Stage C-H Amination of Drug Molecules via Photoredox Catalysis</title>
<link>https://onlinelibrary.wiley.com/doi/10.1002/anie.202512345?af=R</link>
<content:encoded>&lt;img src="https://onlinelibrary.wiley.com/cms/asset/abc/anie202512345-toc-0001-m.jpg" alt="Graphical Abstract"/&gt; We report a general photoredox method for late-stage amination of pharmaceutically relevant heteroarenes, enabling rapid SAR exploration of drug molecules with broad functional group tolerance.</content:encoded>
<dc:creator>A. Chemist, B. Chemist</dc:creator>
<prism:doi>10.1002/anie.202512345</prism:doi><dc:date>2026-11-05T00:00:00-08:00</dc:date>
<prism:section>Research Article</prism:section>
</item></rdf:RDF>"""

ELS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Design and synthesis of novel quinazoline EGFR inhibitors</title>
<link>https://www.sciencedirect.com/science/article/pii/S0223523426001234</link>
<description>Publication date: Available online 5 November 2026 Source: European Journal of Medicinal Chemistry Author(s): X. Y</description>
<pubDate>Wed, 05 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>"""

CHEMRXIV = json.dumps({"itemHits": [{"item": {"id": "abc", "doi": "10.26434/chemrxiv-2026-xyz",
    "title": "A PROTAC degrader of BRD4", "abstract": "We describe a cereblon-recruiting PROTAC with DC50 of 10 nM.",
    "authors": [{"firstName": "M", "lastName": "R"}], "publishedDate": "2026-11-06T10:00:00Z",
    "categories": [{"name": "Biological and Medicinal Chemistry"}], "asset": {"original": {"url": ""}}}}]}).encode()

CROSSREF = {"10.1021/acs.jmedchem.5c01234": {"message": {"abstract": "<jats:p>We report SAR of KRAS G12D inhibitors with IC50 3 nM and oral PK.</jats:p>",
                                                          "license": [{"URL": "http://creativecommons.org/licenses/by/4.0/"}]}}}


def fake_get(url, **kw):
    if "journals/0022-2623/works" in url:
        return json.dumps({"message": {"items": [{"DOI": "10.1021/acs.jmedchem.5c01234", "title": ["Discovery of Potent, Orally Bioavailable KRAS G12D Inhibitors"],
            "author": [{"given": "Jane", "family": "Smith"}, {"given": "John", "family": "Doe"}, {"given": "Ann", "family": "Lee"}],
            "created": {"date-time": "2026-11-06T05:00:00Z"}, "license": [{"URL": "http://creativecommons.org/licenses/by/4.0/"}],
            "abstract": "<jats:p>We report SAR of KRAS G12D inhibitors with IC50 3 nM and oral PK.</jats:p>"}]}}).encode()
    if "journals/1520-5126/works" in url:
        return json.dumps({"message": {"items": [{"DOI": "10.1021/jacs.5c09999", "title": ["Perovskite Solar Cells with Record Stability"],
            "created": {"date-time": "2026-11-06T05:00:00Z"}, "abstract": "<jats:p>Perovskite photovoltaic devices.</jats:p>"}]}}).encode()
    if "api.baranlab.org/api/journals" in url:
        return b'[{"id":"jmedchem"}]'
    if "api.baranlab.org/api/feed" in url:
        return json.dumps({"articles": [{"doi": "10.1021/acs.jmedchem.5c01234", "imageUrl": "https://pubs.acs.org/cms/x/toc.gif"}]}).encode()
    if "15213773" in url:
        return WILEY
    if "02235234" in url:
        return ELS
    if "prefixes/10.26434" in url:
        return json.dumps({"message": {"items": [{"DOI": "10.26434/chemrxiv-2026-xyz/v1", "title": ["A PROTAC degrader of BRD4"],
            "abstract": "<jats:p>We describe a cereblon-recruiting PROTAC with DC50 of 10 nM.</jats:p>", "author": [{"given": "M", "family": "R"}],
            "created": {"date-time": "2026-11-06T10:00:00Z"}, "group-title": "Biological and Medicinal Chemistry"},
            {"DOI": "10.26434/chemrxiv-2026-xyz/v2", "title": ["A PROTAC degrader of BRD4 (rev)"]}]}}).encode()
    if "api.crossref.org" in url:
        for k, v in CROSSREF.items():
            if k in url.replace("%2F", "/"):
                return json.dumps(v).encode()
        raise fetch.urllib.error.HTTPError(url, 404, "nf", {}, None)
    if "openalex" in url:
        raise fetch.urllib.error.HTTPError(url, 404, "nf", {}, None)
    raise fetch.urllib.error.HTTPError(url, 403, "blocked", {}, None)


def run():
    tmp = tempfile.mkdtemp()
    fetch.ROOT = tmp
    fetch.DOCS = os.path.join(tmp, "docs")
    fetch.FEED_OUT = os.path.join(tmp, "state", "items.json")
    fetch.STATE = os.path.join(tmp, "state", "state.json")
    fetch.IMG_DIR = os.path.join(tmp, "docs", "img")
    fetch.get = fake_get
    fetch.main()
    fetch.main()  # second run must not duplicate
    data = json.load(open(os.path.join(tmp, "docs", "data.json")))
    titles = {x["title"]: x for x in data["items"]}
    for t, x in titles.items():
        print(f"{x['score']:>4}  {x['type']:<10} {x['journal']:<9} img={len(x['imgs'])} oa={x['oa']} {t[:60]}")
    assert "Perovskite Solar Cells with Record Stability" not in titles
    k = titles["Discovery of Potent, Orally Bioavailable KRAS G12D Inhibitors"]
    assert k["imgs"] and k["abstract"].startswith("We report SAR") and k["oa"] and len(k["authors"]) == 3
    w = titles["Late-Stage C-H Amination of Drug Molecules via Photoredox Catalysis"]
    assert w["imgs"] and "photoredox" in w["abstract"] and w["score"] >= 4
    e = titles["Design and synthesis of novel quinazoline EGFR inhibitors"]
    assert e["imgs"][0].endswith("S0223523426001234-ga1.jpg"), e["imgs"]
    assert "A PROTAC degrader of BRD4" in titles and titles["A PROTAC degrader of BRD4"]["type"] == "Preprint"
    assert len(data["items"]) == len(titles)
    print("OK", len(data["items"]), "items")
    shutil.rmtree(tmp)


if __name__ == "__main__":
    run()
