# business_1: dropshipping product looker

Research tools for launching a dropshipping store. It works like the energy drink concept tester: real Amazon listings and reviews are used as evidence for whether a product is worth testing.

- `CLAUDE.md` makes Claude Code act as a skeptical product researcher. Describe a product and it writes a report with matched listings, niche size and trend, complaint rates with quotes, a dropshipping scorecard and a verdict. Reports go in `reports/`.
- `site/` is the **Product Looker** web app (a claude.ai artifact). It has a product lookup with a scorecard and Claude's verdict, a niche board scored for opportunity, and a margin calculator. See `site/README.md`.
- `data/` holds listings, per-product review stats and quote samples for Pet Supplies, Beauty & Personal Care, Sports & Outdoors and Home & Kitchen, from [Amazon Reviews '23](https://amazon-reviews-2023.github.io/) (through September 2023). `extract_products.py` rebuilds it.
- `FOUNDER_CONTEXT.md` and `.claude/skills/` are founder and marketing skills (go-to-market, pricing, competitor intel, CRO and others) that read the business context.
