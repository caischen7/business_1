#!/usr/bin/env python3
"""
Turn data/products_*, stats_* and reviews_* into the JSON the Product Looker
page loads (site/data/). Run after extract_products.py:

  python3 site/build_site_data.py

Writes
  meta.json          categories, theme labels, counts, build date
  niches.json        every niche (Amazon category path, levels 2-3) with demand,
                     trend, competition, price, complaint rates and an opportunity score
  products-<cat>.json  compact rows for listings with SITE_MIN_RATINGS+ ratings
                     (the products the lookup searches); fields in meta.json
  quotes-NN.json     review quotes keyed by parent_asin, sharded by a hash of
                     the ASIN so the page fetches only the shards it needs
"""

import glob
import gzip
import json
import math
import re
import sys
import zlib
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "data"

SITE_MIN_RATINGS = 1000   # listings the browser search covers
QUOTE_SHARDS = 400        # small shards: a lookup fetches ~8 of them
MIN_NICHE_LISTINGS = 15   # smaller niches are left off the niche board

CATEGORY_LABELS = {
    "Pet_Supplies": "Pet Supplies",
    "Beauty_and_Personal_Care": "Beauty & Personal Care",
    "Sports_and_Outdoors": "Sports & Outdoors",
    "Home_and_Kitchen": "Home & Kitchen",
}
COMPLAINTS = ["broke_cheap", "stopped_working", "not_as_pictured", "size_fit", "shipping_damage", "smell",
              "hard_to_use", "returned", "pet_refused", "skin_reaction", "battery_charge"]
PRAISE = ["quality", "easy", "value", "as_described", "pet_loves", "gift", "would_buy_again"]
LABELS = {
    "broke_cheap": "Broke or feels cheap", "stopped_working": "Stopped working or defective",
    "not_as_pictured": "Smaller or different than pictured", "size_fit": "Wrong size or fit",
    "shipping_damage": "Arrived damaged, leaking or missing parts", "smell": "Bad or chemical smell",
    "hard_to_use": "Hard to assemble, use or clean", "returned": "Returned it or wants a refund",
    "pet_refused": "Pet won't use it", "skin_reaction": "Skin reaction or irritation",
    "battery_charge": "Battery or charging problems",
    "quality": "Quality and sturdiness", "easy": "Easy to use", "value": "Good value",
    "as_described": "As described", "pet_loves": "Pet loves it", "gift": "Bought as a gift",
    "would_buy_again": "Would buy again",
}
FLAGS = ["ingestible", "topical", "battery", "fragile", "liquid_aerosol", "sized", "plug_in", "sharp_weapon", "kids", "pesticide"]
# Added after the first extraction; applied here too so existing data/products_* files get it.
PESTICIDE = re.compile(r"\b(?:flea|ticks?|pesticides?|insecticides?|insect repellent|bug spray|mosquito repellent|"
                       r"dewormer|wormer|herbicides?|rodenticide|rat poison)\b", re.I)
VIDEO_TAG = re.compile(r"\[\[VIDEOID:[^\]]*\]\]")
QUOTE_MAX = 450  # extract_products.SAMPLE_MAX_CHARS
YEARS = list(range(2015, 2024))


def shard_of(asin):
    return zlib.crc32(asin.encode()) % QUOTE_SHARDS


def load(kind):
    frames = []
    for f in sorted(glob.glob(str(DATA / f"{kind}_*.csv.gz"))):
        frames.append(pd.read_csv(f, dtype={"parent_asin": str}, keep_default_na=False, na_values=[""]))
    if not frames:
        sys.exit(f"no data/{kind}_*.csv.gz; run extract_products.py first")
    return pd.concat(frames, ignore_index=True)


def r(x, n=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    products = load("products")
    stats = load("stats")
    df = products.merge(stats, on="parent_asin", how="left")
    for c in stats.columns:
        if c != "parent_asin":
            df[c] = df[c].fillna(0)
    df["low_n"] = df["stars_1"] + df["stars_2"]
    df["high_n"] = df["stars_4"] + df["stars_5"]
    df["risk_flags"] = df["risk_flags"].fillna("")
    pest = (df["title"].fillna("") + " " + df["category_path"].fillna("")).str.contains(PESTICIDE)
    df.loc[pest & ~df["risk_flags"].str.contains("pesticide"), "risk_flags"] = df["risk_flags"].where(df["risk_flags"] == "", df["risk_flags"] + ";") + "pesticide"
    df["store"] = df["store"].fillna("")
    df["first_year"] = pd.to_datetime(df["first_available"], errors="coerce").dt.year

    # Reviews in the dataset dip from 2021 to 2022 across whole categories (a collection effect),
    # so growth is reported relative to the category's own change.
    cat_growth = {c: float(g["y2022"].sum() / g["y2021"].sum() - 1) if g["y2021"].sum() else 0.0
                  for c, g in df.groupby("category")}

    # ---- niches -------------------------------------------------------------
    niches = []
    for (cat, niche), g in df.groupby(["category", "niche"]):
        if len(g) < MIN_NICHE_LISTINGS:
            continue
        reviews = g["reviews"].sum()
        low = g["low_n"].sum()
        high = g["high_n"].sum()
        by_store = g.groupby("store")["rating_number"].sum().sort_values(ascending=False)
        top3 = by_store.head(3).sum() / max(by_store.sum(), 1)
        y21, y22 = g["y2021"].sum(), g["y2022"].sum()
        growth = (y22 - y21) / y21 if y21 else None
        rel = (1 + growth) / (1 + cat_growth[cat]) - 1 if growth is not None else None
        flag_share = {f: r(g["risk_flags"].str.contains(rf"\b{f}\b").mean(), 3) for f in FLAGS}
        new_share = (g["first_year"] >= 2021).mean()
        top = g.sort_values("rating_number", ascending=False).head(8)
        niches.append({
            "id": len(niches), "category": cat, "niche": niche, "listings": int(len(g)),
            "ratings": int(g["rating_number"].sum()), "reviews": int(reviews),
            "median_price": r(g["price"].median(), 2), "avg_rating": r((g["average_rating"] * g["rating_number"]).sum() / g["rating_number"].sum(), 2),
            "low_share": r(low / reviews if reviews else None), "top3_share": r(top3),
            "top_stores": [[s, r(v / max(by_store.sum(), 1))] for s, v in by_store.head(3).items()],
            "years": [int(g[f"y{y}"].sum()) for y in YEARS], "growth": r(growth), "growth_vs_cat": r(rel),
            "new_listing_share": r(new_share), "median_weight": r(g["weight_lb"].median(), 2),
            "median_side": r(g["longest_side_in"].median(), 1),
            "complaints": {k: r(g[f"low_{k}"].sum() / low if low else None) for k in COMPLAINTS},
            "praise": {k: r(g[f"high_{k}"].sum() / high if high else None) for k in PRAISE},
            "flags": flag_share,
            "top": [[t.parent_asin, re.sub(r"\s+", " ", str(t.title))[:120], str(t.store)[:40], r(t.price, 2),
                     r(t.average_rating, 1), int(t.rating_number), int(t.y2021), int(t.y2022)]
                    for t in top.itertuples(index=False)],
        })
    score_niches(niches)
    niche_ids = {(n["category"], n["niche"]): n["id"] for n in niches}

    # ---- products for the browser search --------------------------------------
    site = df[df["rating_number"] >= SITE_MIN_RATINGS].copy()
    fields = (["asin", "title", "store", "price", "rating", "ratings", "niche", "weight", "side", "flags", "listed",
               "reviews", "low", "high"] + [f"y{y}" for y in YEARS[-5:]]
              + [f"c_{k}" for k in COMPLAINTS] + [f"p_{k}" for k in PRAISE])
    counts = {}
    for cat, g in site.groupby("category"):
        rows = []
        for t in g.itertuples(index=False):
            d = t._asdict()
            nid = niche_ids.get((cat, d["niche"]), -1)
            title = re.sub(r"\s+", " ", str(d["title"]))[:140]
            rows.append([d["parent_asin"], title, str(d["store"])[:40], r(d["price"], 2), r(d["average_rating"], 1),
                         int(d["rating_number"]), nid, r(d["weight_lb"], 2), r(d["longest_side_in"], 1),
                         d["risk_flags"], int(d["first_year"]) if not pd.isna(d["first_year"]) else None,
                         int(d["reviews"]), int(d["low_n"]), int(d["high_n"])]
                        + [int(d[f"y{y}"]) for y in YEARS[-5:]]
                        + [int(d[f"low_{k}"]) for k in COMPLAINTS] + [int(d[f"high_{k}"]) for k in PRAISE])
        (OUT / f"products-{cat}.json").write_text(json.dumps(rows, separators=(",", ":")))
        counts[cat] = len(rows)

    # ---- quotes, sharded ---------------------------------------------------------
    keep = set(site["parent_asin"])
    reviews = load("reviews")
    reviews = reviews[reviews["parent_asin"].isin(keep)]
    shards = [dict() for _ in range(QUOTE_SHARDS)]
    reviews = reviews[reviews["rating"] != 3]   # the page and verdict use low and high quotes only
    for t in reviews.itertuples(index=False):
        text = str(t.review_text) if not pd.isna(t.review_text) else ""
        cut = len(text) >= QUOTE_MAX
        text = re.sub(r"\s+", " ", VIDEO_TAG.sub(" ", text)).strip()
        if cut:
            text = text.rsplit(" ", 1)[0].rstrip(" ,;:-") + " …"
        title = str(t.review_title) if not pd.isna(t.review_title) else ""
        shards[shard_of(t.parent_asin)].setdefault(t.parent_asin, []).append(
            [int(t.rating), title, text, int(t.helpful_vote), int(t.year) if not pd.isna(t.year) else None])
    for i, s in enumerate(shards):
        (OUT / f"quotes-{i:03d}.json").write_text(json.dumps(s, separators=(",", ":"), ensure_ascii=False))

    (OUT / "niches.json").write_text(json.dumps(niches, separators=(",", ":"), ensure_ascii=False))
    meta = {
        "built": date.today().isoformat(), "fields": fields, "years": YEARS[-5:], "categories": CATEGORY_LABELS,
        "complaints": COMPLAINTS, "praise": PRAISE, "labels": LABELS, "flags": FLAGS,
        "quote_shards": QUOTE_SHARDS, "site_min_ratings": SITE_MIN_RATINGS,
        "products_in_search": counts, "listings_total": int(len(df)),
        "listings_by_category": {k: int(v) for k, v in df["category"].value_counts().items()},
        "reviews_total": int(df["reviews"].sum()), "niches": len(niches),
        "category_growth": {c: round(v, 4) for c, v in cat_growth.items()},
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    size = sum(p.stat().st_size for p in OUT.glob("*.json")) / 1e6
    print(f"{len(df):,} listings, {len(niches):,} niches, {sum(counts.values()):,} searchable products, "
          f"{len(reviews):,} quotes; site/data is {size:.1f} MB")


def score_niches(niches):
    """Opportunity score, 0-100: demand, growth, an unmet-complaint gap, open competition,
    a dropship-friendly price, and penalties for regulated, heavy or bulky products.
    The page shows each part, so the score is never a black box."""
    def clamp(x, lo=0.0, hi=1.0):
        return max(lo, min(hi, x))
    for n in niches:
        demand = clamp((math.log10(max(n["ratings"], 1)) - 4) / 2)               # 10k ratings -> 0, 1M -> 1
        g = n["growth_vs_cat"] if n["growth_vs_cat"] is not None else 0
        growth = clamp((g + 0.2) / 0.5)                     # vs. category: -20% -> 0, +30% -> 1
        gap = clamp(((n["low_share"] or 0) - 0.06) / 0.10)                       # 6% low-star -> 0, 16% -> 1
        openness = clamp(1 - ((n["top3_share"] or 0) - 0.15) / 0.5)              # top 3 stores hold 15% -> 1, 65% -> 0
        p = n["median_price"] or 0
        price = 1.0 if 20 <= p <= 60 else (0.5 if 15 <= p <= 80 else 0.0)
        risk = max((n["flags"].get(f) or 0) for f in ("ingestible", "topical", "sharp_weapon", "pesticide"))
        w = n["median_weight"] or 0
        side = n["median_side"] or 0
        bulk = 1.0 if (w > 5 or side > 30) else (0.5 if (w > 2 or side > 18) else 0.0)
        parts = {"demand": demand, "growth": growth, "gap": gap, "openness": openness, "price": price}
        weights = {"demand": 0.3, "growth": 0.2, "gap": 0.15, "openness": 0.2, "price": 0.15}
        base = sum(parts[k] * weights[k] for k in parts)
        penalty = 0.35 * clamp(risk / 0.5) + 0.2 * bulk
        n["score"] = round(100 * clamp(base - penalty), 1)
        n["score_parts"] = {**{k: round(v, 2) for k, v in parts.items()}, "risk_penalty": round(0.35 * clamp(risk / 0.5), 2),
                            "bulk_penalty": round(0.2 * bulk, 2)}


if __name__ == "__main__":
    main()
