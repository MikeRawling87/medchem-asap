#!/usr/bin/env python3
"""Phone notifications for keyword alerts, via ntfy (https://ntfy.sh).

Needs two GitHub Actions secrets:
  NTFY_TOPIC      your private ntfy topic name
  ALERT_KEYWORDS  keywords, one per line or comma-separated. Whole words; several words form a phrase
                  (pan RAS = pan-RAS); degrad* for any ending; KRAS + degrader for words anywhere; -word excludes.
Matches title + abstract of papers kept by any section. Each paper is notified only once.
"""
import hashlib
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
RAW = os.environ.get("ALERT_KEYWORDS", "")
SENT = os.path.join(ROOT, "state", "notified.json")
MAX_SINGLE = 8


SEP = r"[\s\-‐‑‒–/]*"


def phrase_rx(ph):
    words = [w for w in re.split(r"[\s\-‐‑–]+", re.sub(r'["“”]', "", ph).lower()) if w]
    if not words:
        return None
    body = SEP.join(re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) for w in words)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.I)


def compile_alert(a):
    """Same rules as the app: words = phrase (whole words, spaces/hyphens interchangeable); * = any ending;
    + joins parts that can appear anywhere; " -word" excludes."""
    pos, neg = [], []
    for chunk in a.split("+"):
        bits = re.split(r"\s-(?=\S)", " " + chunk)
        head = bits[0].strip()
        if head and phrase_rx(head):
            pos.append(phrase_rx(head))
        neg += [r for r in (phrase_rx(b.strip()) for b in bits[1:]) if r]
    return pos, neg


def alerts():
    parts = re.split(r"[\n,;]+", RAW)
    return [p.strip() for p in parts if p.strip()]


def hits(it, als):
    tx = f"{it['title']} {it.get('abstract', '')}"
    out = []
    for a in als:
        pos, neg = compile_alert(a)
        if pos and all(r.search(tx) for r in pos) and not any(r.search(tx) for r in neg):
            out.append(a)
    return out


def key(pid):
    """Notified papers are stored as salted hashes so the public repo doesn't reveal what you're alerted on."""
    return hashlib.sha256((TOPIC + "|" + pid).encode()).hexdigest()[:20]


def send(title, message, click=None, tags="bell", priority=3):
    body = {"topic": TOPIC, "title": title, "message": message, "tags": [tags], "priority": priority}
    if click:
        body["click"] = click
        body["actions"] = [{"action": "view", "label": "Open paper", "url": click}]
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def main():
    als = alerts()
    if not TOPIC or not als:
        print("Notifications not configured (NTFY_TOPIC / ALERT_KEYWORDS secrets); skipping")
        return
    data = {"items": [], "journals": []}
    for f in ("data.json", "news.json", "clinical.json", "blogs.json"):
        p = os.path.join(ROOT, "docs", f)
        if os.path.exists(p):
            d = json.load(open(p))
            data["items"] += d["items"]
            data["journals"] += d["journals"]
    seen_titles = set()
    data["items"] = [x for x in data["items"] if not (x["title"].lower() in seen_titles or seen_titles.add(x["title"].lower()))]
    jn = {j["id"]: j["short"] for j in data["journals"]}
    first = not os.path.exists(SENT)
    sent = set(json.load(open(SENT))) if not first else set()
    matches = [(it, h) for it in data["items"] if key(it["id"]) not in sent and it["id"] not in sent
               and it["type"] != "Correction"
               for h in [hits(it, als)] if h]
    matches.sort(key=lambda x: x[0]["date"], reverse=True)
    if first:
        send("MedChem ASAP alerts are on",
             f"Watching: {', '.join(als)}. {len(matches)} matching papers are already in your feed; "
             "you'll be notified about new ones after each update.", tags="white_check_mark")
    else:
        for it, h in matches[:MAX_SINGLE]:
            send(f"🔔 {', '.join(h)} · {jn.get(it['journal'], it['journal'])}", it["title"], click=it["url"])
        if len(matches) > MAX_SINGLE:
            send("MedChem ASAP", f"…and {len(matches) - MAX_SINGLE} more alert matches. Open the app to see them all.")
    sent = {x if re.fullmatch(r"[0-9a-f]{20}", x) else key(x) for x in sent}   # migrate any plain IDs
    sent |= {key(it["id"]) for it, _ in matches}
    os.makedirs(os.path.dirname(SENT), exist_ok=True)
    json.dump(sorted(sent), open(SENT, "w"))
    print(f"{len(matches)} alert matches {'recorded' if first else 'notified'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001  never fail the feed update because of a notification
        print(f"Notification step failed: {e}")
