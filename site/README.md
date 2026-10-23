# Product Looker (website)

A single-page tool built on the Amazon listings and reviews in `data/`, published as a claude.ai artifact. It has four tabs:

- **Look up a product.** Type a product idea, a category, and optionally your price, supplier cost, shipping and angle. The page searches listings with 1,000+ ratings and compares you with the closest ones (untick any that aren't really your product). It shows:
  - a dropshipping scorecard: demand, trend, competition, margin, shipping, returns, and rules and ad policy (thresholds on the About tab and in `scorecard()`);
  - complaint and praise rates counted over every review of the matched listings;
  - reviews per year, unit economics, and the most helpful verified quotes.

  **Claude's verdict** sends that evidence to Claude through the page's `sample` capability, on the viewer's own account, and checks every quote in the answer word for word against the reviews. The report can be copied or saved as Markdown (`downloads` capability).
- **Niches.** Every Amazon sub-category with 15+ qualifying listings, scored 0–100 for opportunity (demand, 2021→2022 growth, complaint gap, room for new sellers, price fit, minus penalties for regulated, heavy or bulky products). Click a niche for its breakdown, top complaints and most-rated listings, then look up a product in it.
- **Margin calculator.** Per-order profit after fees, refunds and ad cost, with break-even ad cost and ROAS. "Open in calculator" on a lookup fills it in.
- **About the data.** Sources, limits and scorecard rules.

## Files

| File | What it is |
|---|---|
| `index.html` | The whole page: HTML, CSS and JavaScript, no build step. Loads `marked` and `DOMPurify` from cdnjs to render Claude's Markdown. |
| `build_site_data.py` | Turns `data/*.csv.gz` into `site/data/`: `meta.json`, `niches.json` (with the opportunity score; the rules are in `score_niches()`), `products-<category>.json` (compact rows for listings with 1,000+ ratings; field order in `meta.json`) and `quotes-000..127.json` (quotes sharded by CRC32 of the ASIN; the page fetches only the shards it needs). |
| `tests/serve.py`, `tests/smoke.js` | A local server and a Playwright smoke test with a mock of the claude.ai runtime. |

`site/data/` is git-ignored. Rebuild it after a fresh clone.

## Build, test, publish

```bash
python3 site/build_site_data.py
python3 site/tests/serve.py &          # serves site/ on http://localhost:8765
cd site/tests && NODE_PATH=$(npm root -g) node smoke.js
```

To publish or update the artifact, ask Claude Code in this repo:

> Rebuild the site data and republish the Product Looker artifact.

Claude runs `build_site_data.py`, then publishes `site/index.html` with every file in `site/data/` to the same artifact URL. The page declares two capabilities: `sample` (Claude) and `downloads` (saving reports).

### Sharing

Open the artifact on claude.ai, use **Share** to get a link, and send it. Viewers sign in to claude.ai. The first verdict asks their permission, and verdicts count against their own Claude usage.
