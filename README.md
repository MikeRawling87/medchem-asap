# MedChem ASAP

A personal, free phone app showing med chem and drug discovery papers from journal ASAP / early-view feeds
(the ChemASAP journal list plus core med chem journals).

- **Updates:** GitHub Actions runs `scripts/fetch.py` before 5 am and 5 pm UK time (`.github/workflows/update.yml`).
  Run it any time from the Actions tab → *Update feed* → *Run workflow*.
- **App:** `docs/` is served by GitHub Pages. Open it in Chrome on Android → ⋮ → *Install app*.
- **Your stars, groups, read and hidden marks** stay on your phone (export a backup from Filters → Backup).
- **Journals:** `scripts/journals.json`. `core: true` = every paper shown; otherwise papers are scored.
- **Relevance rules:** `scripts/classify.py` (term weights, thresholds, topic tags). No paid services are used.
- Abstracts come from the feeds, Crossref and OpenAlex; graphical abstracts from the feeds, publisher pages,
  Elsevier's CDN, or page 1 of the ChemRxiv PDF.
