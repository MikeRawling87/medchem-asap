#!/usr/bin/env python3
"""Fetch ASAP / early-view feeds, enrich, score for med chem relevance, write docs/feed.json.

Standard library only (PyMuPDF optional, used for ChemRxiv page-1 thumbnails).
Run:  python scripts/fetch.py            (live)
      python scripts/fetch.py --offline  (re-score existing data, no network)
"""
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import archive  # noqa: E402
import classify  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
FEED_OUT = os.path.join(ROOT, "state", "items.json")
STATE = os.path.join(ROOT, "state", "state.json")
IMG_DIR = os.path.join(DOCS, "img")
JOURNALS = json.load(open(os.path.join(ROOT, "scripts", "journals.json")))
JMAP = {j["id"]: j for j in JOURNALS}

KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "45"))
PENDING_DAYS = 3          # how long to wait for an abstract before scoring on title alone
MAX_ENRICH_TRIES = 16          # ~4 days at 4 runs a day; Elsevier abstracts reach OpenAlex with a lag
BROWSER_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36")
API_UA = "medchem-asap/1.0 (personal literature reader; https://github.com/MikeRawling87/medchem-asap)"
NOW = dt.datetime.now(dt.timezone.utc)
LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


# ---------------------------------------------------------------- http
def get(url, ua=BROWSER_UA, accept="*/*", timeout=30, tries=4, method="GET"):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, method=method, headers={
                "User-Agent": ua, "Accept": accept, "Accept-Language": "en-GB,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read() if method == "GET" else r.status
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 404, 410):
                break
            if e.code == 429:                       # rate limited (Crossref): wait as asked, then retry
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(30, float(wait)) if wait and wait.replace(".", "").isdigit() else 4 * (i + 2))
                continue
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


# ---------------------------------------------------------------- parsing helpers
DOI_RX = re.compile(r"10\.\d{4,9}/[^\s\"'<>?#&]+", re.I)
TAG_RX = re.compile(r"<[^>]+>")
IMG_RX = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<jats:title>.*?</jats:title>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>", " ", s)
    s = TAG_RX.sub("", s)
    s = html.unescape(html.unescape(s))
    return re.sub(r"\s+", " ", s).strip()


def clean_title(s):
    s = strip_html(s)
    s = re.sub(r"^\[ASAP\]\s*", "", s)
    return s


def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def kids(el, name):
    return [c for c in el if local(c.tag) == name]


def text_of(el, *names):
    for n in names:
        for c in kids(el, n):
            t = (c.text or "").strip() or "".join(c.itertext()).strip()
            if t:
                return t
    return ""


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d:
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:  # noqa: BLE001
        pass
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:  # noqa: BLE001
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.datetime(*map(int, m.groups()), tzinfo=dt.timezone.utc)
    return None


def find_doi(*vals):
    for v in vals:
        if not v:
            continue
        m = DOI_RX.search(urllib.parse.unquote(v))
        if m:
            return m.group(0).rstrip(".,;)").lower()
    return ""


def good_img(u):
    if not u:
        return False
    lu = u.lower()
    bad = ("logo", "cover", "spacer", "pixel", "feedburner", "icon", "badge", "doubleclick", "1x1")
    return lu.startswith("http") and not any(b in lu for b in bad)


def parse_feed(raw):
    """Return list of dicts from RSS 2.0, RSS 1.0 (RDF) or Atom."""
    raw = raw.lstrip(b"\xef\xbb\xbf")
    root = ET.fromstring(raw)
    items = [e for e in root.iter() if local(e.tag) in ("item", "entry")]
    out = []
    for it in items:
        title = text_of(it, "title")
        link = text_of(it, "link")
        if not link:
            for l in kids(it, "link"):
                if l.get("href") and l.get("rel", "alternate") == "alternate":
                    link = l.get("href")
                    break
        if not link and it.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"):
            link = it.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about")
        desc = text_of(it, "encoded") or text_of(it, "description", "summary", "content")
        # some feeds put raw html as child elements
        if not desc:
            for n in ("description", "content", "summary"):
                for c in kids(it, n):
                    desc = ET.tostring(c, encoding="unicode")
        authors = [(c.text or "").strip() for c in kids(it, "creator") if (c.text or "").strip()]
        if not authors:
            for a in kids(it, "author"):
                nm = text_of(a, "name") or (a.text or "").strip()
                if nm:
                    authors.append(nm)
        if len(authors) == 1 and ("," in authors[0] or " and " in authors[0]):
            authors = [x.strip() for x in re.split(r",\s*|\s+and\s+", authors[0]) if x.strip()]
        date = text_of(it, "pubdate", "date", "publicationdate", "published", "updated", "coverdate")
        ident = text_of(it, "doi", "identifier", "id", "guid")
        types = [(c.text or "").strip() for c in kids(it, "type") + kids(it, "category") + kids(it, "section") if (c.text or "").strip()]
        imgs = [u for u in IMG_RX.findall(desc or "") if good_img(u)]
        for c in it.iter():
            if local(c.tag) in ("thumbnail", "content", "enclosure") and c.get("url") and good_img(c.get("url")):
                if (c.get("type") or "image").startswith("image") or local(c.tag) == "thumbnail":
                    imgs.append(c.get("url"))
        out.append({
            "title": clean_title(title), "url": link.strip(), "desc_html": desc or "",
            "authors": authors, "date": date, "doi": find_doi(ident, link, desc),
            "types": types, "imgs": imgs,
        })
    return out


# ---------------------------------------------------------------- sources
ACS_ISSN = {
    "jacsat": "1520-5126", "orlef7": "1523-7060", "joceah": "0022-3263", "oprdfk": "1083-6160", "orgnd7": "0276-7333",
    "jnprdf": "0163-3864", "jmcmar": "0022-2623", "jaaucr": "2691-3704", "accacs": "2155-5435", "acscii": "2374-7943",
    "mamobx": "0024-9297", "bcches": "1043-1802", "bomaf6": "1525-7797", "chreay": "0009-2665", "achre4": "0001-4842",
    "ancac3": "1936-0851", "amclct": "1948-5875", "acbcct": "1554-8929", "jcisd8": "1549-9596", "mpohbp": "1543-8384",
    "aptsfn": "2575-9108",
}


def online_date(m):
    """Best 'first online' date from a Crossref record."""
    parts = ((m.get("published-online") or {}).get("date-parts") or [[None]])[0]
    if parts and len(parts) == 3 and parts[0]:
        return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
    created = (m.get("created") or {}).get("date-time")
    if created:
        return created
    parts = ((m.get("posted") or m.get("published") or {}).get("date-parts") or [[None]])[0]
    if parts and parts[0]:
        p = list(parts) + [1, 1]
        return f"{p[0]:04d}-{p[1]:02d}-{p[2]:02d}"
    return ""


def crossref_items(path, j, days=4, extra_filter="", only_v1=False):
    """Recent works from Crossref (used where publishers block cloud servers: ACS, ChemRxiv)."""
    since = (NOW - dt.timedelta(days=days)).date().isoformat()
    url = (f"https://api.crossref.org/{path}/works?sort=created&order=desc&rows=400"
           f"&filter=from-created-date:{since}{extra_filter}"
           "&select=DOI,title,author,abstract,created,posted,published-online,URL,subject,group-title,license,type")
    msg = json.loads(get(url, ua=API_UA, accept="application/json", timeout=60))["message"]
    out = []
    for m in msg.get("items", []):
        doi = (m.get("DOI") or "").lower()
        if not doi or (only_v1 and not re.search(r"/v1$", doi)):
            continue
        date = (m.get("created") or {}).get("date-time", "")
        lic = " ".join(l.get("URL", "") for l in m.get("license", []))
        out.append({
            "title": clean_title((m.get("title") or [""])[0]), "url": f"https://doi.org/{doi}", "desc_html": "",
            "abstract": strip_html(m.get("abstract", "")),
            "authors": [f"{a.get('given','')} {a.get('family','')}".strip() or a.get("name", "")
                        for a in m.get("author", [])],
            "date": date, "xdate": online_date(m), "doi": doi, "types": [], "imgs": [], "pdf": "",
            "journal": j["id"], "oa": "creativecommons" in lic,
            "subjects": ([m.get("group-title")] if m.get("group-title") else []) + (m.get("subject") or []),
        })
    return out


def fetch_journal(j):
    feeds = j.get("feeds") or [j["feed"]]
    items = []
    for f in feeds:
        try:
            if f == "chemrxiv":
                got = crossref_items("prefixes/10.26434", j, extra_filter=",type:posted-content", only_v1=True)
                for x in got:
                    x["types"] = ["Preprint"]
                items += got
            elif f.startswith("acs:"):
                items += crossref_items(f"journals/{ACS_ISSN[f[4:]]}", j)
            else:
                items += parse_feed(get(f, accept="application/rss+xml, application/xml, text/xml, */*"))
        except Exception as e:  # noqa: BLE001
            log(f"  ! {j['short']}: feed failed ({f}): {e}")
    for x in items:
        x["journal"] = j["id"]
    return items


# ---------------------------------------------------------------- ChemASAP (graphics fallback)
CHEMASAP = "https://api.baranlab.org"


def chemasap_images():
    """DOI -> graphical abstract URL from ChemASAP's public feed. One request per run."""
    if os.environ.get("USE_CHEMASAP", "1") != "1":
        return {}
    try:
        js = json.loads(get(f"{CHEMASAP}/api/journals", accept="application/json"))
        ids = ",".join(x["id"] for x in js)
        feed = json.loads(get(f"{CHEMASAP}/api/feed?journals={urllib.parse.quote(ids)}&limit=0",
                              accept="application/json", timeout=90))
    except Exception as e:  # noqa: BLE001
        log(f"  ! ChemASAP feed unavailable: {e}")
        return {}
    out = {}
    for a in feed.get("articles", []):
        u, doi = a.get("imageUrl"), (a.get("doi") or "").lower()
        if u and doi:
            out[doi] = u if u.startswith("http") else CHEMASAP + u
    log(f"ChemASAP: {len(out)} graphics available")
    return out


def rsc_ga(url):
    m = re.search(r"/ArticleLanding/(\d{4})/(\w+)/(\w+)", url or "", re.I)
    if not m:
        return []
    y, jc, aid = m.group(1), m.group(2).upper(), m.group(3).lower()
    base = f"https://pubs.rsc.org/image/article/{y}/{jc}/{aid}/{aid}"
    return [f"{base}-ga.gif", f"{base}-ga.jpg", f"{base}-ga.png"]


# ---------------------------------------------------------------- enrichment
def crossref(doi):
    raw = get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", ua=API_UA, accept="application/json",
              timeout=20)
    return json.loads(raw)["message"]


def openalex_abstract(doi):
    raw = get(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}", ua=API_UA, accept="application/json",
              timeout=20)
    inv = json.loads(raw).get("abstract_inverted_index")
    if not inv:
        return ""
    pos = {}
    for w, idx in inv.items():
        for i in idx:
            pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))


def enrich(it):
    """Fill abstract / authors / OA / type from Crossref then OpenAlex. Mutates item."""
    it["tries"] = it.get("tries", 0) + 1
    doi = it.get("doi")
    if not doi:
        return it
    try:
        m = crossref(doi)
        if not it.get("abstract") and m.get("abstract"):
            it["abstract"] = strip_html(m["abstract"])
        if not it.get("authors") and m.get("author"):
            it["authors"] = [f"{a.get('given','')} {a.get('family','')}".strip() or a.get("name", "")
                             for a in m["author"]]
        lic = " ".join(l.get("URL", "") for l in m.get("license", []))
        if "creativecommons" in lic:
            it["oa"] = True
        it["xdate"] = online_date(m) or it.get("xdate", "")
    except Exception:  # noqa: BLE001
        pass
    if not it.get("abstract"):
        try:
            it["abstract"] = openalex_abstract(doi)
        except Exception:  # noqa: BLE001
            pass
    return it


ELS_ISSN = {"ejmc": "0223-5234", "bmcl": "0960-894X", "bmc": "0968-0896", "ccr": "0010-8545",
            "chem": "2451-9294", "trchem": "2589-5974"}
ELS_RX = re.compile(r"^\s*Publication date:.*?(?:Author\(s\):\s*(.*))?$", re.S | re.I)


def fix_elsevier(it):
    """ScienceDirect feeds put boilerplate where the abstract should be; pull authors out of it."""
    for field in ("abstract", "desc_html"):
        txt = strip_html(it.get(field, ""))
        m = ELS_RX.match(txt) if txt else None
        if m:
            if m.group(1) and not it.get("authors"):
                it["authors"] = [a.strip() for a in m.group(1).split(",") if a.strip()][:40]
            if field == "abstract":
                it["abstract"] = ""
            else:
                it["desc_html"] = ""


def resolve_doi(it):
    """Find a DOI by title for feeds that don't carry one (ScienceDirect, Cell)."""
    issn = ELS_ISSN.get(it["journal"])
    if it.get("doi") or not issn or it.get("doi_tries", 0) >= 4:
        return it
    it["doi_tries"] = it.get("doi_tries", 0) + 1
    try:
        q = urllib.parse.quote(it["title"][:250])
        msg = json.loads(get(f"https://api.crossref.org/journals/{issn}/works?query.bibliographic={q}&rows=3"
                             "&select=DOI,title,abstract,author,created,published-online", ua=API_UA, accept="application/json", timeout=30))["message"]
        norm = lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())
        for m in msg.get("items", []):
            if norm((m.get("title") or [""])[0])[:80] == norm(it["title"])[:80]:
                it["doi"] = m["DOI"].lower()
                it["xdate"] = online_date(m)
                if not it.get("abstract") and m.get("abstract"):
                    it["abstract"] = strip_html(m["abstract"])
                break
    except Exception:  # noqa: BLE001
        pass
    return it


# ---------------------------------------------------------------- graphics
def og_image(url):
    raw = get(url, timeout=20, tries=1).decode("utf-8", "ignore")[:400000]
    for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
                r'<meta[^>]+name=["\']citation_image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, raw, re.I)
        if m and good_img(m.group(1)):
            return html.unescape(m.group(1))
    return ""


def elsevier_ga(url):
    """Elsevier/Cell graphical abstracts live at a predictable CDN path keyed by PII."""
    m = re.search(r"/pii/(S[0-9X()\-]+)", url or "", re.I) or re.search(r"(S\d{4}-?\d{3}[\dX]\(\d\d\)\d{5}-[\dX])",
                                                                          url or "")
    if not m:
        return []
    pii = re.sub(r"[^0-9A-Z]", "", m.group(1).upper())
    base = f"https://ars.els-cdn.com/content/image/1-s2.0-{pii}"
    return [f"{base}-ga1.jpg", f"{base}-fx1.jpg", f"{base}-gr1.jpg"]


ACS_PREFIX = {"jmcmar": "jm", "jacsat": "ja", "orlef7": "ol", "joceah": "jo", "oprdfk": "op", "jnprdf": "np",
              "acscii": "oc", "chreay": "cr", "achre4": "ar", "amclct": "ml", "acbcct": "cb", "jcisd8": "ci",
              "mpohbp": "mp", "aptsfn": "pt", "jaaucr": "au", "accacs": "cs", "ancac3": "nn", "mamobx": "ma",
              "bcches": "bc", "bomaf6": "bm", "orgnd7": "om"}


def acs_guess(doi, code):
    """ACS abstract graphics sit on Silverchair's CDN at a path derived from the DOI (ASAP versions)."""
    m = re.match(r"10\.1021/(?:acs\.)?[a-z]+\.(\d[a-z]\d{5})$", doi or "")
    pre = ACS_PREFIX.get(code)
    if not m or not pre:
        return []
    key, base = m.group(1), f"https://acs.silverchair-cdn.com/acs/content_public/journal/{code}/pap/{doi.replace('/', '_')}"
    return [f"{base}/1/{pre}{key}_abs.png", f"{base}/2/{pre}{key}_abs.png"]


def chemrxiv_thumb(it):
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""
    pdf = it.get("pdf")
    if not pdf:
        return ""
    name = hashlib.sha1(it["id"].encode()).hexdigest()[:16] + ".jpg"
    path = os.path.join(IMG_DIR, "chemrxiv", name)
    if not os.path.exists(path):
        try:
            data = get(pdf, ua=API_UA, timeout=40, tries=1)
            doc = fitz.open(stream=data, filetype="pdf")
            page = doc[0]
            r = page.rect
            clip = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.55)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), clip=clip)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pix.save(path, jpg_quality=62) if hasattr(pix, "save") else open(path, "wb").write(pix.tobytes("jpg"))
        except Exception as e:  # noqa: BLE001
            log(f"  ! ChemRxiv thumb failed {it['doi']}: {e}")
            return ""
    return f"img/chemrxiv/{name}"


def add_graphics(items):
    """Make sure relevant items have at least one image candidate."""
    need = [it for it in items if not it.get("imgs")]
    def work(it):
        j = it["journal"]
        url = it.get("_link") or it.get("url") or (f"https://doi.org/{it['doi']}" if it.get("doi") else "")
        cand = []
        if j == "chemrxiv":
            t = chemrxiv_thumb(it)
            if t:
                cand.append(t)
        elif "sciencedirect" in url or "cell.com" in url or "/pii/" in url:
            cand += elsevier_ga(url)
        elif "rsc.org" in url:
            cand += rsc_ga(url)
        blocked = ("acs.org", "science.org", "rsc.org", "chemrxiv", "doi.org/10.1021", "doi.org/10.1126")
        if not cand and url and j != "chemrxiv" and not any(b in url for b in blocked):
            try:
                u = og_image(url)
                if u:
                    cand.append(u)
            except Exception:  # noqa: BLE001
                pass
        it["imgs"] = cand
        it["img_tries"] = it.get("img_tries", 0) + 1
        return it
    need = [it for it in need if it.get("img_tries", 0) < 3]
    with cf.ThreadPoolExecutor(6) as ex:
        list(ex.map(work, need))
    # Drop "images" that are really per-journal generic artwork (same URL on several papers)
    counts = {}
    for it in items:
        for u in it.get("imgs", []):
            counts[u] = counts.get(u, 0) + 1
    for it in items:
        it["imgs"] = [u for u in it.get("imgs", []) if counts[u] <= 2]


# ---------------------------------------------------------------- type inference
def infer_type(it, j):
    t = " ".join(it.get("types", [])).lower()
    title = it.get("title", "")
    if re.match(r"(?i)(correction|addition and correction|erratum|retraction|expression of concern)", title):
        return "Correction"
    if j.get("kind") == "Preprint":
        return "Preprint"
    if re.search(r"(?i)\b(perspective|outlook|viewpoint|miniperspective)\b", t + " " + title[:40]):
        return "Perspective"
    if re.search(r"(?i)\breview\b", t) or j.get("kind") == "Review":
        return "Review"
    if re.search(r"(?i)editorial|news|research highlight|comment|correspondence|book review|in this issue", t):
        return "Editorial/News"
    if j.get("kind") == "Letter" or re.search(r"(?i)\bletter\b|communication", t):
        return "Letter"
    return "Article"


# ---------------------------------------------------------------- main
def make_id(it):
    return it.get("doi") or "u:" + hashlib.sha1((it.get("url") or it["title"]).encode()).hexdigest()[:16]


def iso(d):
    return d.astimezone(dt.timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z")


def finalize(it, jmap):
    j = jmap[it["journal"]]
    fix_elsevier(it)
    if not it.get("abstract"):
        # Some feeds (Wiley, RSC, Nature) carry the abstract in the description
        txt = strip_html(it.get("desc_html", ""))
        txt = re.sub(r"^.*?(Abstract|ABSTRACT)\s*", "", txt) if len(txt) > 200 else txt
        if len(txt) > 140 and not re.match(r"(?i)^(J\. |Journal|DOI)", txt):
            it["abstract"] = txt
    fd, xd = parse_date(it.get("date")), parse_date(it.get("xdate"))
    if it.get("from_kept") and not it.get("xdate") and fd:
        d = fd
    elif xd and not (fd and fd.date() == xd.date()):
        d = xd          # Crossref's online date beats feed dates (missing for Elsevier, odd for RSC)
    else:
        d = fd or xd or parse_date(it.get("first_seen")) or NOW
    out = {
        "id": it["id"], "doi": it.get("doi", ""), "title": it["title"], "authors": it.get("authors", [])[:40],
        "journal": it["journal"], "date": iso(d), "first_seen": it["first_seen"],
        "abstract": (it.get("abstract") or "")[:4000], "imgs": it.get("imgs", [])[:3],
        "url": f"https://doi.org/{it['doi']}" if it.get("doi") else it.get("url", ""),
        "type": infer_type(it, j), "oa": bool(it.get("oa")),
    }
    out["score"] = classify.score(out["title"], out["abstract"] + " " + " ".join(it.get("subjects", [])),
                                  core=bool(j.get("core")))
    if out["type"] == "Correction":
        out["score"] = min(out["score"], -10)
    out["topics"] = classify.topics(out["title"], out["abstract"])
    # internal bookkeeping (stripped before writing feed.json)
    out["_tries"] = it.get("tries", 0)
    out["_img_tries"] = it.get("img_tries", 0)
    out["_pdf"] = it.get("pdf", "")
    out["_subjects"] = it.get("subjects", [])
    out["_link"] = it.get("_link") or it.get("url", "")
    out["_doi_tries"] = it.get("doi_tries", 0)
    out["_xdate"] = it.get("xdate", "")
    return out


def main():
    offline = "--offline" in sys.argv
    archive.ADIR = os.path.join(DOCS, "archive")
    jmap = {j["id"]: j for j in JOURNALS}
    state = {"seen": {}, "pending": {}}
    if os.path.exists(STATE):
        state = json.load(open(STATE))
    feed = {"items": []}
    if os.path.exists(FEED_OUT):
        feed = json.load(open(FEED_OUT))
    kept = {x["id"]: x for x in feed.get("items", [])}
    stats = {}

    new = []
    if not offline:
        log(f"Fetching {len(JOURNALS)} journals…")
        with cf.ThreadPoolExecutor(4) as ex:     # gentle on Crossref's rate limits
            results = list(ex.map(fetch_journal, JOURNALS))
        for j, items in zip(JOURNALS, results):
            stats[j["id"]] = {"fetched": len(items), "new": 0}
            for it in items:
                if not it.get("title"):
                    continue
                it["id"] = make_id(it)
                if it["id"] in state["seen"] or it["id"] in kept or it["id"] in state["pending"]:
                    continue
                it["first_seen"] = iso(NOW)
                new.append(it)
                stats[j["id"]]["new"] += 1
        log(f"{len(new)} new items")

    # candidates to (re-)enrich: new items + pending + kept items still lacking an abstract
    pending = list(state["pending"].values()) + new
    for it in kept.values():
        if ELS_RX.match(it.get("abstract") or "x"):
            it["abstract"] = ""
        needs_doi = not it.get("doi") and it["journal"] in ELS_ISSN and it.get("_doi_tries", 0) < 4
        if needs_doi or ((not it.get("abstract") or not it.get("_xdate")) and it.get("_tries", 0) < MAX_ENRICH_TRIES
                         and it.get("doi")):
            pending.append({**it, "tries": it.get("_tries", 0), "img_tries": it.get("_img_tries", 0),
                            "pdf": it.get("_pdf", ""), "subjects": it.get("_subjects", []), "doi_tries": it.get("_doi_tries", 0), "xdate": it.get("_xdate", ""),
                            "_link": it.get("_link", ""), "from_kept": True})
    if not offline:
        for p in pending:
            fix_elsevier(p)
        with cf.ThreadPoolExecutor(6) as ex:
            list(ex.map(resolve_doi, [p for p in pending if not p.get("doi") and p["journal"] in ELS_ISSN]))
        todo = [p for p in pending if p.get("doi") and (not p.get("abstract") or not p.get("xdate"))]
        log(f"Enriching {len(todo)} items via Crossref/OpenAlex…")
        with cf.ThreadPoolExecutor(8) as ex:
            list(ex.map(enrich, todo))

    state["pending"] = {}
    accepted = []
    for it in pending:
        fin = finalize(it, jmap)
        age = (NOW - parse_date(it["first_seen"])).days
        if not fin["abstract"] and age < PENDING_DAYS and it.get("tries", 0) < MAX_ENRICH_TRIES and \
                fin["score"] < classify.BORDERLINE and not it.get("from_kept"):
            state["pending"][it["id"]] = it  # wait for an abstract before judging it
            continue
        if fin["score"] >= classify.BORDERLINE:
            accepted.append(fin)
        else:
            state["seen"][it["id"]] = it["first_seen"][:10]
            kept.pop(it["id"], None)

    if not offline:
        ca = chemasap_images()
        for x in accepted + list(kept.values()):
            if not x.get("imgs") and x.get("doi") in ca:
                x["imgs"] = [ca[x["doi"]]]
        add_graphics(accepted + [x for x in kept.values() if not x.get("imgs") and x.get("_img_tries", 0) < 3])
    for fin in accepted:
        kept[fin["id"]] = fin

    if True:  # re-score everything kept, so edits to classify.py apply to existing papers
        for k, x in list(kept.items()):
            x["score"] = classify.score(x["title"], x["abstract"] + " " + " ".join(x.get("_subjects", [])),
                                        core=bool(jmap[x["journal"]].get("core")))
            x["topics"] = classify.topics(x["title"], x["abstract"])

    # retention
    cutoff = NOW - dt.timedelta(days=KEEP_DAYS)
    expired = [x for x in kept.values() if parse_date(x["first_seen"]) < cutoff and parse_date(x["date"]) < cutoff
               and x["score"] >= classify.BORDERLINE]
    kept = {k: x for k, x in kept.items() if (parse_date(x["first_seen"]) >= cutoff or parse_date(x["date"]) >= cutoff)
            and x["journal"] in jmap}
    if expired:
        archive.add(expired)
        log(f"Moved {len(expired)} papers older than {KEEP_DAYS} days into the archive")
    archive.prune((NOW - dt.timedelta(days=400)).strftime("%Y-%m"))
    seen_cut = (NOW - dt.timedelta(days=KEEP_DAYS + 30)).date().isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= seen_cut}

    # remove orphaned chemrxiv thumbnails
    cdir = os.path.join(IMG_DIR, "chemrxiv")
    if os.path.isdir(cdir):
        live = {u.split("/")[-1] for x in kept.values() for u in x.get("imgs", []) if u.startswith("img/")}
        for f in os.listdir(cdir):
            if f not in live:
                os.remove(os.path.join(cdir, f))

    items = sorted(kept.values(), key=lambda x: x["date"], reverse=True)
    out = {
        "generated": iso(NOW), "keep_days": KEEP_DAYS,
        "thresholds": {"relevant": classify.RELEVANT, "borderline": classify.BORDERLINE},
        "journals": [{k: j[k] for k in ("id", "short", "name", "pub", "color", "on") if k in j} |
                     {"core": bool(j.get("core")), "extra": bool(j.get("extra"))} for j in JOURNALS],
        "topics": list(classify.TOPICS.keys()),
        "stats": stats,
        "items": items,
    }
    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    os.makedirs(os.path.dirname(FEED_OUT), exist_ok=True)
    # full copy (with bookkeeping) for the next run, slim copy for the app
    json.dump(out, open(FEED_OUT + ".tmp", "w"), ensure_ascii=False, separators=(",", ":"))
    os.replace(FEED_OUT + ".tmp", FEED_OUT)
    json.dump(state, open(STATE, "w"), separators=(",", ":"))
    slim = {**out, "items": [{k: v for k, v in x.items() if not k.startswith("_")} for x in items]}
    json.dump(slim, open(os.path.join(DOCS, "data.json"), "w"), ensure_ascii=False, separators=(",", ":"))

    rel = sum(1 for x in items if x["score"] >= classify.RELEVANT)
    withimg = sum(1 for x in items if x["imgs"])
    log(f"Kept {len(items)} items ({rel} relevant, {len(items) - rel} borderline); {withimg} with graphics; "
        f"{len(state['pending'])} awaiting abstracts")
    if stats:
        log("Per journal fetched/new: " + ", ".join(f"{jmap[k]['short']} {v['fetched']}/{v['new']}"
                                                   for k, v in stats.items()))
    # image coverage per journal, useful for checking
    cov = {}
    for x in items:
        c = cov.setdefault(x["journal"], [0, 0])
        c[0] += 1
        c[1] += bool(x["imgs"])
    log("Graphics coverage: " + ", ".join(f"{jmap[k]['short']} {v[1]}/{v[0]}" for k, v in sorted(cov.items())))


if __name__ == "__main__":
    main()
