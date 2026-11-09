#!/usr/bin/env python3
"""Industry news, clinical & regulatory updates and blogs -> docs/news.json, docs/clinical.json, docs/blogs.json.

Each file uses the same shape as docs/data.json so the app can show it with the same cards and filters:
  journals = sources, items[].journal = source id, items[].type = main category, items[].topics = all tags.
Free sources only: publisher RSS feeds, ClinicalTrials.gov API, openFDA, EMA news.
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import fetch  # noqa: E402  (reuses its HTTP, feed parsing and text helpers)

DOCS = fetch.DOCS
NOW = fetch.NOW
log = fetch.log

# ---------------------------------------------------------------- sources
NEWS_SOURCES = [
    {"id": "fierce", "short": "Fierce Biotech", "name": "Fierce Biotech", "pub": "Fierce", "color": "#c8102e",
     "feed": "https://www.fiercebiotech.com/rss/xml"},
    {"id": "fiercepharma", "short": "Fierce Pharma", "name": "Fierce Pharma", "pub": "Fierce", "color": "#9e1b32",
     "feed": "https://www.fiercepharma.com/rss/xml"},
    {"id": "endpoints", "short": "Endpoints", "name": "Endpoints News", "pub": "Endpoints", "color": "#1d3557",
     "feed": "https://endpts.com/feed/"},
    {"id": "biospace", "short": "BioSpace", "name": "BioSpace", "pub": "BioSpace", "color": "#0077b6",
     "feed": "https://www.biospace.com/all-news.rss"},
    {"id": "biopharmadive", "short": "BioPharma Dive", "name": "BioPharma Dive", "pub": "Industry Dive",
     "color": "#2a9d8f", "feed": "https://www.biopharmadive.com/feeds/news/"},
    {"id": "stat", "short": "STAT", "name": "STAT — biotech", "pub": "STAT", "color": "#d62828",
     "feed": "https://www.statnews.com/category/biotech/feed/"},
    {"id": "cen", "short": "C&EN", "name": "C&EN — pharma & biotech stories", "pub": "ACS", "color": "#00507a",
     "feed": "https://cen.acs.org/feeds/rss/latestnews.xml",
     "only": r"drug|pharma|biotech|clinical|\bFDA\b|medicin|therap|cancer|antibod|vaccine|molecul|inhibitor|disease"},
    {"id": "prn", "short": "Press releases", "name": "PR Newswire — biotechnology", "pub": "PR Newswire",
     "color": "#6c757d", "feed": "https://www.prnewswire.com/rss/health-latest-news/biotechnology-list.rss"},
]
REG_SOURCES = [
    {"id": "fda", "short": "FDA", "name": "FDA approvals (openFDA)", "pub": "FDA", "color": "#005ea2"},
    {"id": "ema", "short": "EMA", "name": "EMA news & CHMP opinions", "pub": "EMA", "color": "#003399",
     "feed": "https://www.ema.europa.eu/en/news.xml"},
    {"id": "ctgov", "short": "ClinicalTrials.gov", "name": "ClinicalTrials.gov — industry trials", "pub": "NIH",
     "color": "#205493"},
]
BLOG_SOURCES = [
    {"id": "pipeline", "short": "In the Pipeline", "name": "Derek Lowe — In the Pipeline", "pub": "Science",
     "color": "#9b1b30", "feed": "https://www.science.org/blogs/pipeline/feed", "author": "Derek Lowe"},
    {"id": "practchem", "short": "Practical Cheminformatics", "name": "Pat Walters — Practical Cheminformatics",
     "pub": "Blog", "color": "#1f6f8b", "feed": "https://practicalcheminformatics.blogspot.com/feeds/posts/default",
     "author": "Pat Walters"},
    {"id": "practfrag", "short": "Practical Fragments", "name": "Dan Erlanson et al. — Practical Fragments",
     "pub": "Blog", "color": "#6a4c93", "feed": "https://practicalfragments.blogspot.com/feeds/posts/default"},
    {"id": "wavefunction", "short": "Curious Wavefunction", "name": "Ash Jogalekar — The Curious Wavefunction",
     "pub": "Blog", "color": "#386641", "feed": "http://wavefunction.fieldofscience.com/feeds/posts/default",
     "author": "Ash Jogalekar"},
    {"id": "drughunter", "short": "Drug Hunter", "name": "Drug Hunter", "pub": "Drug Hunter", "color": "#e76f51",
     "feed": "https://drughunter.com/rss.xml"},
    {"id": "moleculeinsight", "short": "MoleculeInsight", "name": "Santosh Adhikari — MoleculeInsight",
     "pub": "Blog", "color": "#bc6c25", "feed": "https://moleculeinsight.com/author/santosh/feed/",
     "author": "Santosh Adhikari"},
    {"id": "lifescivc", "short": "LifeSciVC", "name": "Bruce Booth et al. — LifeSciVC", "pub": "Blog",
     "color": "#264653", "feed": "https://lifescivc.com/feed/"},
    {"id": "owlposting", "short": "Owl Posting", "name": "Abhishaike Mahajan — Owl Posting (AI × bio)",
     "pub": "Substack", "color": "#7f5539", "feed": "https://www.owlposting.com/feed", "author": "Abhishaike Mahajan"},
]
LINKS = {"blogs": [{"label": "Gilles Ouvry on LinkedIn", "url":
                    "https://www.linkedin.com/search/results/content/?keywords=%22Gilles%20Ouvry%22&sortBy=%22date_posted%22"}]}

# ---------------------------------------------------------------- tagging rules (edit freely)
NEWS_TAGS = {
    "M&A": r"\bacquir|\bacquisition|buyout|\bmerger|takeover|\bto buy\b|snaps up|tender offer|\bbuys\b",
    "Partnership": r"collaborat|partner|licens|alliance|\bdeal\b|option to|co-develop",
    "Funding": r"\braises?\b|series [a-f]\b|financing|\bIPO\b|seed round|private placement|venture|funding|\bSPAC\b",
    "Layoffs": r"layoff|lay off|lays off|job cuts|cuts? \d+%? ?(?:of )?(?:jobs|staff|workers|employees|workforce)|"
               r"workforce|redundanc|restructur|downsiz|reduction in force|shutter|winds? down|closes? (?:site|facility)",
    "Leadership": r"\bCEO\b|\bCSO\b|\bCFO\b|chief .{0,20}officer|appoint|steps down|resign|names .{0,30} as|hires",
    "Strategy": r"strateg|reorgani|prioriti|pipeline (?:cut|review|overhaul)|deprioriti|spin(?:s)? ?off|divest",
    "R&D": r"discover|preclinical|platform|\bAI\b|machine learning|research|\bR&D\b",
    "Clinical": r"phase (?:1|2|3|i|ii|iii)\b|\btrial|topline|readout|endpoint|interim|\bdata\b|study",
    "Regulatory": r"\bFDA\b|\bEMA\b|approv|\bCRL\b|complete response|PDUFA|\bCHMP\b|designation|accelerated|"
                  r"marketing authori[sz]ation|regulator",
    "Milestone": r"milestone|first patient|dosed|\bIND\b|clears?|cleared|enrol",
    "Policy & pricing": r"tariff|pricing|medicare|\bIRA\b|policy|congress|legislat|most favou?red",
}
CLIN_TAGS = {
    "Negative readout": r"fail|did not meet|didn't meet|miss(?:es|ed)?\b|disappoint|futility|halt|discontinu|"
                        r"scrap|drops?\b|setback|negative|no benefit|zero benefit|no significant|flop|tank|"
                        r"safety (?:issue|concern|signal)|deaths?\b",
    "Positive readout": r"\bmet\b (?:its|the|both|all)?.{0,20}(?:primary|secondary)? ?endpoint|positive|"
                        r"statistically significant|achiev|improv|succeed|superior|benefit|reduc(?:ed|es|tion)|shed|"
                        r"response rate|survival|efficacy|promising|encouraging|\bbeats?\b",
    "First-in-human": r"first[- ]in[- ]human|\bFIH\b|first patient(?:s)? dosed|first participant|phase 1 (?:initiat|start)",
    "Trial stopped": r"terminat|withdrawn|suspend|clinical hold|pause",
    "FDA decision": r"\bFDA\b.{0,40}(?:approv|reject|decision|clear)|approv.{0,40}\bFDA\b|\bCRL\b|complete response",
    "EMA / EU": r"\bEMA\b|\bCHMP\b|European Commission|marketing authori[sz]ation|\bEU\b approval",
    "PDUFA / upcoming": r"PDUFA|target action date|decision (?:date|expected)|under review|accepted for (?:priority )?review",
}
CLIN_ROUTE = r"phase (?:1|2|3|i|ii|iii)\b|trial|topline|readout|endpoint|interim|\bFDA\b|\bEMA\b|\bCHMP\b|approv|\bCRL\b|" \
             r"complete response|PDUFA|clinical hold|first[- ]in[- ]human|first patient"

_rx = lambda d: {k: re.compile(v, re.I) for k, v in d.items()}
NEWS_RX, CLIN_RX, ROUTE_RX = _rx(NEWS_TAGS), _rx(CLIN_TAGS), re.compile(CLIN_ROUTE, re.I)


def tags_for(text, rules):
    return [k for k, rx in rules.items() if rx.search(text)]


def summary(html_text, n=320):
    t = fetch.strip_html(html_text)
    t = re.sub(r"^(?:The post .*? appeared first on .*?\.)", "", t).strip()
    t = re.sub(r"\s*(?:The post|Continue reading|Read more|\[…\]|\[\.\.\.\]).*$", "", t)
    if len(t) <= n:
        return t
    cut = t[:n]
    m = re.search(r"^(.{120,}?[.!?])\s", cut)
    return m.group(1) if m else cut.rsplit(" ", 1)[0] + "…"


def sid(prefix, url, title):
    return prefix + hashlib.sha1((url or title).encode()).hexdigest()[:16]


def base_item(prefix, src, x):
    d = fetch.parse_date(x.get("date")) or NOW
    return {
        "id": sid(prefix, x.get("url"), x.get("title")), "doi": "", "title": x["title"],
        "authors": x.get("authors") or ([src["author"]] if src.get("author") else []),
        "journal": src["id"], "date": fetch.iso(d), "first_seen": fetch.iso(NOW),
        "abstract": summary(x.get("desc_html", "")), "imgs": [u for u in x.get("imgs", []) if fetch.good_img(u)][:1],
        "url": x.get("url", ""), "type": "", "oa": False, "score": 99, "topics": [],
    }


def read_feed(src):
    try:
        raw = fetch.get(src["feed"], accept="application/rss+xml, application/atom+xml, application/xml, */*")
        return fetch.parse_feed(raw)
    except Exception as e:  # noqa: BLE001
        log(f"  ! {src['short']}: {e}")
        return []


# ---------------------------------------------------------------- news
def build_news():
    news, clin = [], []
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(read_feed, NEWS_SOURCES))
    for src, xs in zip(NEWS_SOURCES, results):
        only = re.compile(src["only"], re.I) if src.get("only") else None
        n = 0
        for x in xs:
            if not x.get("title"):
                continue
            it = base_item("n:", src, x)
            text = f"{it['title']} {it['abstract']} {' '.join(x.get('types', []))}"
            if only and not only.search(text):
                continue
            tags = tags_for(text, NEWS_RX)
            it["topics"] = tags
            it["type"] = next((t for t in ("M&A", "Layoffs", "Funding", "Partnership", "Regulatory", "Clinical",
                                           "Leadership", "Strategy", "Milestone", "Policy & pricing", "R&D")
                               if t in tags), "Other")
            news.append(it)
            n += 1
            if ROUTE_RX.search(it["title"]):         # trial / regulatory stories also go to Clinical
                ct = tags_for(text, CLIN_RX)
                if "Negative readout" in ct and "Positive readout" in ct:
                    ct.remove("Positive readout")          # "zero benefit" etc. — negative wins
                c = dict(it, id="c" + it["id"], topics=ct or ["Trial news"])
                c["type"] = next((t for t in ("Negative readout", "Positive readout", "FDA decision", "EMA / EU",
                                              "First-in-human", "Trial stopped", "PDUFA / upcoming") if t in ct),
                                 "Trial news")
                clin.append(c)
        log(f"  {src['short']}: {n} stories")
    return news, clin


# ---------------------------------------------------------------- clinical & regulatory
def ctgov():
    out = []
    since = (NOW - dt.timedelta(days=3)).date().isoformat()
    queries = [
        ("Trial stopped", f"AREA[LeadSponsorClass]INDUSTRY AND AREA[LastUpdatePostDate]RANGE[{since},MAX] AND "
                          "AREA[OverallStatus](TERMINATED OR WITHDRAWN OR SUSPENDED)"),
        ("New phase 1", f"AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyFirstPostDate]RANGE[{since},MAX] AND "
                        "AREA[Phase]PHASE1 AND AREA[InterventionType](DRUG OR BIOLOGICAL)"),
    ]
    skip = re.compile(r"bioequivalen|food effect|relative bioavailab|drug[- ]drug interaction|"
                      r"healthy (?:adult )?(?:participants|volunteers|subjects).{0,40}(?:tablet|formulation)", re.I)
    for kind, q in queries:
        url = f"https://clinicaltrials.gov/api/v2/studies?pageSize=200&filter.advanced={urllib.parse.quote(q)}"
        try:
            studies = json.loads(fetch.get(url, ua=fetch.API_UA, accept="application/json", timeout=60)).get("studies", [])
        except Exception as e:  # noqa: BLE001
            log(f"  ! ClinicalTrials.gov ({kind}): {e}")
            continue
        for s in studies:
            p = s.get("protocolSection", {})
            idm, st = p.get("identificationModule", {}), p.get("statusModule", {})
            nct, title = idm.get("nctId"), idm.get("briefTitle", "")
            if not nct or (kind == "New phase 1" and skip.search(title)):
                continue
            sponsor = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
            conds = p.get("conditionsModule", {}).get("conditions", [])[:4]
            ints = [i.get("name") for i in p.get("armsInterventionsModule", {}).get("interventions", [])][:4]
            phases = ", ".join(x.replace("PHASE", "Phase ") for x in p.get("designModule", {}).get("phases", []))
            status = st.get("overallStatus", "").replace("_", " ").title()
            why = st.get("whyStopped", "")
            dkey = "lastUpdatePostDateStruct" if kind == "Trial stopped" else "studyFirstPostDateStruct"
            date = (st.get(dkey) or {}).get("date", "")
            fih = bool(re.search(r"first[- ]in[- ]human|\bFIH\b", title + " " + idm.get("officialTitle", ""), re.I))
            tags = ["Trial stopped"] if kind == "Trial stopped" else (["First-in-human"] if fih else []) + ["New phase 1"]
            bits = [f"{sponsor}", phases, f"{status}" + (f": {why}" if why else ""),
                    "Conditions: " + "; ".join(conds) if conds else "", "Interventions: " + "; ".join(ints) if ints else ""]
            out.append({
                "id": f"ct:{nct}:{kind[:3]}", "doi": "", "title": title, "authors": [sponsor] if sponsor else [],
                "journal": "ctgov", "date": fetch.iso(fetch.parse_date(date) or NOW), "first_seen": fetch.iso(NOW),
                "abstract": " · ".join(b for b in bits if b), "imgs": [], "url": f"https://clinicaltrials.gov/study/{nct}",
                "type": tags[0], "oa": False, "score": 99, "topics": tags,
            })
    log(f"  ClinicalTrials.gov: {len(out)} studies")
    return out


def openfda():
    out = []
    a = (NOW - dt.timedelta(days=21)).strftime("%Y%m%d")
    b = NOW.strftime("%Y%m%d")
    url = ("https://api.fda.gov/drug/drugsfda.json?search=submissions.submission_type:ORIG+AND+"
           f"submissions.submission_status:AP+AND+submissions.submission_status_date:[{a}+TO+{b}]&limit=100")
    try:
        res = json.loads(fetch.get(url, ua=fetch.API_UA, accept="application/json", timeout=60)).get("results", [])
    except Exception as e:  # noqa: BLE001
        log(f"  ! openFDA: {e}")
        return out
    for r in res:
        appl = r.get("application_number", "")
        if appl.startswith("ANDA"):
            continue                                  # generics
        subs = [s for s in r.get("submissions", []) if s.get("submission_type") == "ORIG"
                and s.get("submission_status") == "AP" and a <= s.get("submission_status_date", "") <= b]
        if not subs:
            continue
        s = subs[0]
        prods = r.get("products", [])
        brand = (r.get("openfda", {}).get("brand_name") or [p.get("brand_name") for p in prods] or [""])[0]
        ingred = sorted({(i.get("name") or "").title() for p in prods for i in (p.get("active_ingredients") or [])})
        form = ", ".join(sorted({f"{(p.get('dosage_form') or '').title()} ({(p.get('route') or '').title()})" for p in prods}))
        cls = s.get("submission_class_code_description", "")
        nme = "new molecular entity" in cls.lower() or appl.startswith("BLA")
        d = s["submission_status_date"]
        title = f"FDA approves {brand.title()} ({', '.join(ingred)})" if brand else f"FDA approval: {', '.join(ingred)}"
        out.append({
            "id": f"fda:{appl}", "doi": "", "title": title, "authors": [(r.get("sponsor_name") or "").title()],
            "journal": "fda", "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}T12:00Z", "first_seen": fetch.iso(NOW),
            "abstract": " · ".join(x for x in [appl, cls, form] if x),
            "imgs": [], "type": "FDA decision", "oa": False, "score": 99,
            "topics": ["FDA decision"] + (["New molecular entity"] if nme else ["New formulation / other"]),
            "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={re.sub(r'\D', '', appl)}",
        })
    log(f"  openFDA: {len(out)} approvals")
    return out


def ema():
    src = REG_SOURCES[1]
    out = []
    for x in read_feed(src):
        if not x.get("title") or re.search(r"veterinary|CVMP", x["title"], re.I):
            continue
        it = base_item("e:", src, x)
        tags = tags_for(f"{it['title']} {it['abstract']}", CLIN_RX)
        it["topics"] = sorted(set(["EMA / EU"] + tags))
        it["type"] = "EMA / EU"
        out.append(it)
    log(f"  EMA: {len(out)} items")
    return out


# ---------------------------------------------------------------- blogs
def build_blogs():
    out = []
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(read_feed, BLOG_SOURCES))
    for src, xs in zip(BLOG_SOURCES, results):
        for x in xs:
            if not x.get("title"):
                continue
            it = base_item("b:", src, x)
            if src.get("author") and (not it["authors"] or it["authors"] == ["Unknown"]):
                it["authors"] = [src["author"]]
            it["type"] = "AI & computation" if re.search(r"\bAI\b|machine learning|deep learning|LLM|model|"
                                                         r"cheminformatic|docking|generative",
                                                         it["title"] + " " + it["abstract"], re.I) else "Post"
            it["topics"] = [it["type"]]
            out.append(it)
        log(f"  {src['short']}: {len(xs)} posts")
    return out


# ---------------------------------------------------------------- write
def merge_write(name, fresh, sources, keep_days, topics, extra=None):
    path = os.path.join(DOCS, f"{name}.json")
    old = {}
    if os.path.exists(path):
        old = {x["id"]: x for x in json.load(open(path)).get("items", [])}
    for x in fresh:
        if x["id"] in old:
            x["first_seen"] = old[x["id"]]["first_seen"]
        old[x["id"]] = x
    cutoff = fetch.iso(NOW - dt.timedelta(days=keep_days))
    items = sorted((x for x in old.values() if x["date"] >= cutoff), key=lambda x: x["date"], reverse=True)
    out = {
        "generated": fetch.iso(NOW), "keep_days": keep_days, "thresholds": {"relevant": 0, "borderline": 0},
        "journals": [{k: s[k] for k in ("id", "short", "name", "pub", "color")} | {"on": True, "core": False, "extra": False}
                     for s in sources],
        "topics": topics, "links": LINKS.get(name, []), "items": items,
    }
    if extra:
        out.update(extra)
    with open(path + ".tmp", "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".tmp", path)
    log(f"{name}.json: {len(items)} items")


def main():
    log("Industry news…")
    news, clin_news = build_news()
    log("Clinical & regulatory…")
    clin = list(clin_news)
    for fn in (ctgov, openfda, ema):
        try:
            clin += fn()
        except Exception as e:  # noqa: BLE001  one broken source must not stop the rest
            log(f"  ! {fn.__name__} failed: {e}")
    log("Blogs…")
    blogs = build_blogs()
    merge_write("news", news, NEWS_SOURCES, 60, list(NEWS_TAGS))
    clin_sources = REG_SOURCES + [s for s in NEWS_SOURCES]
    merge_write("clinical", clin, clin_sources, 90, list(CLIN_TAGS) + ["New phase 1", "New molecular entity", "Trial news"])
    merge_write("blogs", blogs, BLOG_SOURCES, 365, ["AI & computation", "Post"])


if __name__ == "__main__":
    main()
