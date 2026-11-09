#!/usr/bin/env python3
"""AI in Drug Discovery section -> docs/ai.json.

Pulls AI-relevant items out of every other section (literature, news, clinical, blogs) and adds AI-specific
sources: AI journals, arXiv, bioRxiv, GitHub releases of key open models/tools, and ClinicalTrials.gov updates
from AI-first companies. Scoring favours substance (validation, benchmarks, clinical data) over hype.
Run after fetch.py and sections.py.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import fetch  # noqa: E402
import sections  # noqa: E402

DOCS, NOW, log = fetch.DOCS, fetch.NOW, fetch.log
THRESH = 8

# ---------------------------------------------------------------- scoring (edit freely)
AI_TERMS = [
    (r"machine[- ]learning|deep[- ]learning|neural network|graph neural|\bGNNs?\b|(?-i:\bML\b)", 5),
    (r"artificial intelligence|\bAI\b[- ](?:designed|discovered|driven|enabled|generated|powered|first|native)|"
     r"\bAI\b(?: model| platform| drug)", 5),
    (r"\bAI\b", 3),
    (r"generative (?:model|design|chemistry|AI)|diffusion model|flow matching|variational autoencoder|\bVAE\b|"
     r"reinforcement learning|de novo (?:molecular |drug )?design", 6),
    (r"transformer|large language model|\bLLMs?\b|foundation model|language model|agentic|AI agents?", 5),
    (r"AlphaFold|co-?folding|Boltz|Chai-?1|RoseTTAFold|RFdiffusion|ProteinMPNN|OpenFold|Protenix|ESM-?\d|"
     r"structure prediction", 6),
    (r"protein design|binder design|de novo (?:protein|binder|antibod)|antibody design", 5),
    (r"active learning|Bayesian optimi[sz]ation|self-driving lab|autonomous lab|closed[- ]loop", 5),
    (r"retrosynthe|reaction prediction|synthesis planning", 4),
    (r"free energy perturbation|\bFEP\+?\b|machine[- ]learned (?:potential|force field)|neural network potential|"
     r"\bMLIPs?\b|docking|virtual screening|ultra-large", 3),
    (r"\bQSAR\b|property prediction|ADMET prediction|predictive model|molecular representation|embedding", 3),
    (r"cheminformatic|computational", 2),
]
DRUG_CONTEXT = re.compile(r"drug|ligand|molecul|protein|binding|ADMET|pharma|therapeut|inhibitor|compound|"
                          r"chemi|antibod|peptide|target|clinical|biotech", re.I)
SUBSTANCE = [
    (r"prospective|experimentally validated|experimental validation|wet[- ]lab|synthesi[sz]ed and tested|"
     r"confirmed (?:by|in) (?:assay|experiment)", 3),
    (r"benchmark|blind (?:challenge|test|prediction)|leaderboard|\bCASP\d*|\bCACHE\b|Polaris|held[- ]out", 3),
    (r"\bIC50\b|\bnM\b|in vivo|crystal structure|cryo-?EM", 2),
    (r"phase (?:1|2|3|i|ii|iii)\b|clinical trial|\bIND\b|first patient|dosed", 3),
]
HYPE = re.compile(r"revolutioni[sz]|game[- ]chang|transformative|unlock(?:s|ing)? the|supercharg|next[- ]gen(?:eration)? AI|"
                  r"AI[- ]powered platform", re.I)

TOPICS = {
    "Co-folding & structure": r"AlphaFold|co-?folding|Boltz|Chai-?1|RoseTTAFold|OpenFold|Protenix|structure prediction",
    "Generative design": r"generative|de novo (?:molecular |drug )?design|diffusion model|flow matching|REINVENT|"
                         r"molecule generation|molecular generation",
    "Docking & free energy": r"docking|free energy|\bFEP\b|virtual screening|gnina|DiffDock|ultra-large|potential",
    "ADMET & property prediction": r"ADMET|property prediction|toxicity prediction|solubility|permeability|QSAR|"
                                   r"pharmacokinetic",
    "Protein design & binders": r"protein design|binder|RFdiffusion|ProteinMPNN|antibody design|de novo protein",
    "Foundation models & LLMs": r"foundation model|language model|\bLLMs?\b|transformer|agentic|AI agents?|\bESM",
    "Active learning & optimisation": r"active learning|Bayesian optimi|reinforcement learning|closed[- ]loop|"
                                      r"self-driving|autonomous lab",
    "Retrosynthesis & automation": r"retrosynthe|reaction prediction|synthesis planning|robot|automat|self-driving",
    "Benchmarks & challenges": r"benchmark|blind|leaderboard|\bCASP|\bCACHE\b|Polaris|challenge",
}

# AI-first drug discovery companies, used for the clinic tracker and deals. Edit freely.
COMPANIES = [
    "Insilico Medicine", "Recursion", "Exscientia", "Isomorphic Labs", "Xaira", "Iambic", "Chai Discovery",
    "Generate Biomedicines", "Absci", "Schrödinger", "Schrodinger", "Relay Therapeutics", "BenevolentAI",
    "Valo Health", "Atomwise", "XtalPi", "Genesis Therapeutics", "Terray", "Enveda", "Verge Genomics", "insitro",
    "Formation Bio", "Iktos", "Aqemia", "Healx", "Model Medicines", "Pathos", "Nabla Bio", "Cradle", "Profluent",
    "EvolutionaryScale", "Latent Labs", "Inductive Bio", "CHARM Therapeutics", "Charm Therapeutics", "Anagenex",
    "Variational AI", "Deep Genomics", "BigHat", "Peptilogics", "Structure Therapeutics", "Vant AI",
]
COMP_RX = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in COMPANIES) + r")\b", re.I)

_ai = [(re.compile(p, re.I), w) for p, w in AI_TERMS]
_sub = [(re.compile(p, re.I), w) for p, w in SUBSTANCE]
_top = {k: re.compile(v, re.I) for k, v in TOPICS.items()}


def score(title, text, press_release=False):
    full = f"{title} {text}"
    s = 0
    for rx, w in _ai:
        if rx.search(title):
            s += 2 * w
        elif rx.search(text):
            s += w
    if COMP_RX.search(full):
        s += 8 if COMP_RX.search(title) else 4      # AI-first companies count as an AI signal
    if s == 0:
        return 0
    s += sum(w for rx, w in _sub if rx.search(full))
    substance = sum(w for rx, w in _sub if rx.search(full))
    if HYPE.search(full):
        s -= 12 if not substance else 3
    if press_release:
        s -= 4 if not substance else 1   # press releases need more than buzzwords to get in
    if not DRUG_CONTEXT.search(full):
        s -= 8
    return s


def topics(text):
    return [k for k, rx in _top.items() if rx.search(text)]


# ---------------------------------------------------------------- new sources
AI_SOURCES = [
    {"id": "dd", "short": "Digital Discovery", "name": "Digital Discovery (RSC)", "pub": "RSC", "color": "#5a4fcf",
     "feed": "http://feeds.rsc.org/rss/dd"},
    {"id": "natmi", "short": "Nat. Mach. Intell.", "name": "Nature Machine Intelligence", "pub": "Springer Nature",
     "color": "#3d348b", "feed": "https://www.nature.com/natmachintell.rss"},
    {"id": "natcs", "short": "Nat. Comput. Sci.", "name": "Nature Computational Science", "pub": "Springer Nature",
     "color": "#7678ed", "feed": "https://www.nature.com/natcomputsci.rss"},
    {"id": "jcheminf", "short": "J. Cheminform.", "name": "Journal of Cheminformatics", "pub": "Springer/BMC",
     "color": "#2b9348", "issn": "1758-2946"},
    {"id": "arxiv", "short": "arXiv", "name": "arXiv (q-bio, chem-ph, cs.LG)", "pub": "arXiv", "color": "#b31b1b"},
    {"id": "biorxiv", "short": "bioRxiv", "name": "bioRxiv", "pub": "CSHL", "color": "#bc2635"},
    {"id": "github", "short": "Model release", "name": "Open models & tools (GitHub releases)", "pub": "GitHub",
     "color": "#24292f"},
    {"id": "aiclin", "short": "ClinicalTrials.gov", "name": "ClinicalTrials.gov — AI-first companies", "pub": "NIH",
     "color": "#205493"},
]
REPOS = {
    "Boltz": "jwohlwend/boltz", "Chai-1": "chaidiscovery/chai-lab", "OpenFold3": "aqlaboratory/openfold-3",
    "AlphaFold 3": "google-deepmind/alphafold3", "RFdiffusion": "RosettaCommons/RFdiffusion",
    "DiffDock": "gcorso/DiffDock", "Protenix": "bytedance/Protenix", "REINVENT 4": "MolecularAI/REINVENT4",
    "gnina": "gnina/gnina", "OpenMM": "openmm/openmm", "RDKit": "rdkit/rdkit", "Polaris": "polaris-hub/polaris",
    "datamol": "datamol-io/datamol",
}


def item(prefix, src, title, url, date, abstract="", authors=None, imgs=None):
    return {"id": prefix + sections.hashlib.sha1((url or title).encode()).hexdigest()[:16], "doi": "",
            "title": title, "authors": authors or [], "journal": src, "date": fetch.iso(fetch.parse_date(date) or NOW),
            "first_seen": fetch.iso(NOW), "abstract": abstract, "imgs": imgs or [], "url": url, "type": "",
            "oa": False, "score": 0, "topics": []}


def feed_items(src):
    out = []
    for x in sections.read_feed(src):
        if x.get("title"):
            out.append(item("a:", src["id"], x["title"], x.get("url"), x.get("date"), sections.summary(x.get("desc_html", ""), 600),
                            x.get("authors"), [u for u in x.get("imgs", []) if fetch.good_img(u)][:1]))
    return out


def jcheminf():
    src = AI_SOURCES[3]
    try:
        got = fetch.crossref_items(f"journals/{src['issn']}", {"id": "jcheminf"}, days=10)
    except Exception as e:  # noqa: BLE001
        log(f"  ! J. Cheminform.: {e}")
        return []
    return [dict(item("a:", "jcheminf", x["title"], x["url"], x["xdate"] or x["date"], x["abstract"][:1500], x["authors"]),
                 doi=x["doi"]) for x in got]


def arxiv():
    q = ("(cat:q-bio.BM OR cat:q-bio.QM OR cat:physics.chem-ph OR cat:cs.LG) AND "
         "(abs:\"drug discovery\" OR abs:\"drug design\" OR abs:ligand OR abs:\"binding affinity\" OR abs:\"protein-ligand\" "
         "OR abs:\"molecular generation\" OR abs:ADMET OR abs:\"virtual screening\" OR abs:docking OR abs:\"protein design\")")
    url = ("http://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q) +
           "&sortBy=submittedDate&sortOrder=descending&max_results=150")
    try:
        xs = fetch.parse_feed(fetch.get(url, ua=fetch.API_UA, timeout=60))
    except Exception as e:  # noqa: BLE001
        log(f"  ! arXiv: {e}")
        return []
    return [item("a:", "arxiv", re.sub(r"\s+", " ", x["title"]), x.get("url"), x.get("date"),
                 fetch.strip_html(x.get("desc_html", ""))[:1500], x.get("authors")) for x in xs if x.get("title")]


def biorxiv():
    out = []
    a = (NOW - dt.timedelta(days=3)).date().isoformat()
    b = NOW.date().isoformat()
    keep = re.compile(r"bioinformatics|biophysics|pharmacology|synthetic biology|biochemistry|systems biology|"
                      r"bioengineering", re.I)
    cursor = 0
    while cursor < 3000:
        try:
            d = json.loads(fetch.get(f"https://api.biorxiv.org/details/biorxiv/{a}/{b}/{cursor}", ua=fetch.API_UA,
                                     accept="application/json", timeout=60))
        except Exception as e:  # noqa: BLE001
            log(f"  ! bioRxiv: {e}")
            break
        coll = d.get("collection", [])
        for x in coll:
            if not keep.search(x.get("category", "")) or str(x.get("version", "1")) != "1":
                continue
            doi = x.get("doi", "")
            it = item("a:", "biorxiv", x.get("title", ""), f"https://www.biorxiv.org/content/{doi}v1", x.get("date"),
                      x.get("abstract", "")[:1500], [s.strip() for s in x.get("authors", "").split(";") if s.strip()][:30])
            it["doi"] = doi
            out.append(it)
        if len(coll) < 100:
            break
        cursor += 100
    return out


def releases():
    out = []
    def one(kv):
        name, repo = kv
        src = {"short": name, "feed": f"https://github.com/{repo}/releases.atom"}
        res = []
        for x in sections.read_feed(src)[:5]:
            t = x.get("title", "").strip()
            if not t:
                continue
            title = t if name.lower().split()[0] in t.lower() else f"{name} {t}"
            it = item("a:", "github", title, x.get("url"), x.get("date"), sections.summary(x.get("desc_html", ""), 400),
                      [name])
            it["score"], it["type"], it["topics"] = 99, "Models & tools", topics(f"{name} {title}") or ["Models & tools"]
            res.append(it)
        return res
    with cf.ThreadPoolExecutor(6) as ex:
        for r in ex.map(one, REPOS.items()):
            out += r
    return out


def ctgov_ai():
    out = []
    since = (NOW - dt.timedelta(days=14)).date().isoformat()
    names = " OR ".join(f'"{c}"' for c in COMPANIES if c not in ("Recursion", "Pathos", "Cradle", "Terray"))
    names += ' OR "Recursion Pharmaceuticals" OR "Pathos AI" OR "Terray Therapeutics"'
    q = f"AREA[LeadSponsorName]({names}) AND AREA[LastUpdatePostDate]RANGE[{since},MAX]"
    url = f"https://clinicaltrials.gov/api/v2/studies?pageSize=100&filter.advanced={urllib.parse.quote(q)}"
    try:
        studies = json.loads(fetch.get(url, ua=fetch.API_UA, accept="application/json", timeout=60)).get("studies", [])
    except Exception as e:  # noqa: BLE001
        log(f"  ! ClinicalTrials.gov (AI companies): {e}")
        return out
    for s in studies:
        p = s.get("protocolSection", {})
        idm, st = p.get("identificationModule", {}), p.get("statusModule", {})
        nct = idm.get("nctId")
        if not nct:
            continue
        sponsor = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
        phases = ", ".join(x.replace("PHASE", "Phase ") for x in p.get("designModule", {}).get("phases", []))
        conds = "; ".join(p.get("conditionsModule", {}).get("conditions", [])[:3])
        ints = "; ".join(i.get("name", "") for i in p.get("armsInterventionsModule", {}).get("interventions", [])[:3])
        status = st.get("overallStatus", "").replace("_", " ").title()
        it = item("a:", "aiclin", idm.get("briefTitle", ""), f"https://clinicaltrials.gov/study/{nct}",
                  (st.get("lastUpdatePostDateStruct") or {}).get("date"),
                  " · ".join(x for x in [sponsor, phases, status + (f": {st.get('whyStopped')}" if st.get("whyStopped") else ""),
                                         conds and "Conditions: " + conds, ints and "Interventions: " + ints] if x),
                  [sponsor])
        it["id"] = f"a:ct:{nct}"
        it["score"], it["type"], it["topics"] = 99, "AI drugs in the clinic", ["AI drugs in the clinic", status]
        out.append(it)
    return out


# ---------------------------------------------------------------- assemble
def categorise(it, origin):
    text = f"{it['title']} {it.get('abstract', '')}"
    if origin == "github":
        return "Models & tools"
    if re.search(r"\bFDA\b|\bEMA\b|regulat|guidance|policy|legislat", text, re.I) and \
            re.search(r"\bAI\b|artificial intelligence|machine learning", text, re.I) and \
            re.search(r"guidance|framework|policy|regulat", text, re.I):
        return "Policy"
    if origin in ("clin", "aiclin") or (COMP_RX.search(text) and re.search(
            r"phase (?:1|2|3|i|ii|iii)\b|clinical|first patient|dosed|\bIND\b|topline|readout", text, re.I)):
        return "AI drugs in the clinic"
    if re.search(r"benchmark|blind (?:challenge|test|prediction)|leaderboard|\bCASP\d*\b|\bCACHE\b|Polaris", text, re.I):
        return "Benchmarks"
    if origin == "news":
        return "News & deals"
    if origin == "blogs":
        return "Blogs"
    if origin in ("arxiv", "biorxiv") or it["journal"] == "chemrxiv":
        return "Preprints"
    return "Papers"


def main():
    log("AI in drug discovery…")
    pool = []
    # 1. AI-relevant items already collected by the other sections
    for name, origin in (("data", "lit"), ("news", "news"), ("clinical", "clin"), ("blogs", "blogs")):
        p = os.path.join(DOCS, f"{name}.json")
        if not os.path.exists(p):
            continue
        for x in json.load(open(p))["items"]:
            if origin == "lit" and x.get("score", 0) < 4:        # literature: borderline+ med chem only
                continue
            pool.append((dict(x), origin))
    # 2. AI-specific sources
    new = []
    with cf.ThreadPoolExecutor(6) as ex:
        futs = [ex.submit(feed_items, s) for s in AI_SOURCES[:3]] + \
               [ex.submit(f) for f in (jcheminf, arxiv, biorxiv, releases, ctgov_ai)]
        for f in futs:
            try:
                new += f.result()
            except Exception as e:  # noqa: BLE001
                log(f"  ! AI source failed: {e}")
    counts = {}
    for x in new:
        counts[x["journal"]] = counts.get(x["journal"], 0) + 1
    log("  new-source items: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    pool += [(x, x["journal"] if x["journal"] in ("github", "aiclin", "arxiv", "biorxiv") else "lit") for x in new]

    out, seen = [], set()
    for x, origin in pool:
        key = (x.get("doi") or x["title"]).lower()
        if key in seen:
            continue
        text = f"{x.get('abstract', '')} {' '.join(x.get('topics', []))}"
        if x.get("score") == 99 and x["journal"] in ("github", "aiclin"):
            s = 99
        else:
            s = score(x["title"], text, press_release=(x["journal"] == "prn"))
            if origin == "blogs" and x["journal"] in ("practchem", "owlposting"):
                s += 3                                     # AI-focused blogs
        if s < THRESH:
            continue
        seen.add(key)
        x["type"] = categorise(x, origin)
        x["topics"] = [t for t in [x["type"]] + topics(f"{x['title']} {x.get('abstract', '')}")]
        x["topics"] = list(dict.fromkeys(x["topics"]))
        x["score"] = 99                                   # the app's relevance filter doesn't apply here
        x["ai_score"] = s
        out.append(x)

    src_meta = {j["id"]: j for f in ("data", "news", "clinical", "blogs") if os.path.exists(os.path.join(DOCS, f"{f}.json"))
                for j in json.load(open(os.path.join(DOCS, f"{f}.json")))["journals"]}
    for s in AI_SOURCES:
        src_meta[s["id"]] = {k: s[k] for k in ("id", "short", "name", "pub", "color")} | {"on": True, "core": False,
                                                                                         "extra": False}
    prev = os.path.join(DOCS, "ai.json")
    used = sorted({x["journal"] for x in out} |
                  ({x["journal"] for x in json.load(open(prev))["items"]} if os.path.exists(prev) else set()))
    sources = [src_meta[k] | {"on": True} for k in used if k in src_meta]
    cats = ["Papers", "Preprints", "News & deals", "AI drugs in the clinic", "Models & tools", "Benchmarks", "Blogs", "Policy"]
    sections.merge_write("ai", out, [], 120, cats + list(TOPICS),
                         extra={"journals": [{"id": s["id"], "short": s["short"], "name": s["name"], "pub": s["pub"],
                                              "color": s["color"], "on": True, "core": False, "extra": False}
                                             for s in sources]})
    c = {}
    for x in out:
        c[x["type"]] = c.get(x["type"], 0) + 1
    log("AI categories: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
