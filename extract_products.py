#!/usr/bin/env python3
"""
Build the dropshipping product data from the McAuley Lab Amazon Reviews '23
dataset (https://amazon-reviews-2023.github.io/; reviews May 1996 - Sep 2023).

Two phases, one category file at a time so the ~21 GB of raw downloads never
have to sit on disk together:

  python3 extract_products.py products   # metadata -> data/products_<cat>.csv.gz
  python3 extract_products.py reviews    # reviews  -> data/stats_<cat>.csv.gz
                                         #             data/reviews_<cat>.csv.gz

The products phase keeps listings a dropshipper could plausibly sell: a price
in the CONFIG range and enough ratings to judge. It records shipping weight,
size, how long the listing has existed, the niche (Amazon category path) and
risk flags (ingestible, fragile, battery, sized-to-fit...).

The reviews phase streams every review of the kept products. From ALL of them
it counts stars, reviews per year, verified share and complaint / praise
themes (stats file). It also keeps a small sample per product - the most
helpful low, middle and high star reviews - for quotes (reviews file).

Raw files are cached in --raw-dir and resume if interrupted. Pass
--delete-raw to remove each raw file once it has been processed.
Needs Python 3.8+; uses orjson when installed (much faster), else json.
"""

import argparse
import csv
import gzip
import heapq
import io
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import orjson as _json
    loads = _json.loads
except ImportError:  # pragma: no cover
    import json as _json
    loads = _json.loads

# =============================================================================
# CONFIG
# =============================================================================

BASE_URL = "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw"
CATEGORIES = ["Pet_Supplies", "Beauty_and_Personal_Care", "Sports_and_Outdoors", "Home_and_Kitchen"]

PRICE_MIN, PRICE_MAX = 10.0, 100.0  # typical dropshipping retail range
MIN_RATINGS = 100                   # enough ratings to read demand and complaints

# Review sample kept per product for quotes: most helpful verified reviews in each
# star band, only for products with SAMPLE_MIN_RATINGS+ ratings (keeps files under GitHub's limit).
SAMPLE = {"low": 2, "mid": 1, "high": 2}
SAMPLE_MIN_RATINGS = 300
SAMPLE_MIN_CHARS, SAMPLE_MAX_CHARS = 60, 450

# Risk flags: things that make a product harder or riskier to dropship.
RISK_RULES = {
    "ingestible": r"\b(?:supplement|vitamin|capsules?|gummies|gummy|probiotic|protein powder|edible|"
                  r"treats?|chews?|kibble|dog food|cat food|wet food|dry food|jerky|snacks?|tea|coffee|"
                  r"collagen|melatonin|cbd|hemp oil|fish oil)\b",
    "topical": r"\b(?:serum|cream|lotion|moisturi[sz]er|shampoo|conditioner|cleanser|sunscreen|spf|"
               r"toner|essential oil|hair dye|bleach|retinol|peel|ointment|balm|perfume|cologne|"
               r"deodorant|toothpaste|mouthwash|nail polish|foundation|mascara|lipstick)\b",
    "battery": r"\b(?:rechargeable|battery|batteries|lithium|li-ion|usb[- ]c? charg\w*|cordless)\b",
    "fragile": r"\b(?:glass|ceramic|porcelain|crystal|mirror|stoneware)\b",
    "liquid_aerosol": r"\b(?:spray|aerosol|fl\.? ?oz|liquid|gel)\b",
    "sized": r"\b(?:shoes?|boots?|sneakers?|leggings|shirt|jacket|pants|shorts|gloves?|"
             r"size (?:xs|s|m|l|xl|small|medium|large)|x-large|xx-large)\b",
    "plug_in": r"\b(?:plug[- ]in|110v|120v|220v|watts?|\d+ ?w\b|electric|corded)\b",
    "sharp_weapon": r"\b(?:knife|knives|blade|machete|axe|hatchet|airsoft|bb gun|pepper spray|"
                    r"crossbow|arrows?|ammo|ammunition|holster)\b",
    "kids": r"\b(?:baby|babies|infant|toddler|kids?|children|child)\b",
    "pesticide": r"\b(?:flea|ticks?|pesticides?|insecticides?|insect repellent|bug spray|mosquito repellent|"
                 r"dewormer|wormer|herbicides?|rodenticide|rat poison)\b",
}
RISK_RES = {k: re.compile(v, re.I) for k, v in RISK_RULES.items()}

# Review themes. Counted on every review; reported by star band in the stats file.
COMPLAINTS = {
    "broke_cheap": r"\b(?:broke|broken|breaks|cheaply made|cheap(?:ly)? material|flimsy|fell apart|falls apart|"
                   r"snapped|cracked|poor(?:ly)? (?:made|quality)|low quality|ripped|tore|torn|came apart)\b",
    "stopped_working": r"\b(?:stopped working|quit working|stopped charging|died after|does ?n[o']t work|"
                       r"did ?n[o']t work|not working|defective|dead on arrival|doa|malfunction\w*)\b",
    "not_as_pictured": r"\b(?:smaller than (?:expected|i thought|pictured|it looks)|not as (?:pictured|described|shown|advertised)|"
                       r"looks nothing like|misleading|different (?:from|than) (?:the )?(?:picture|photo|description)|"
                       r"tiny|much smaller)\b",
    "size_fit": r"\b(?:runs (?:small|large|big)|too (?:small|big|large|tight|loose|short|long)|"
                r"does ?n[o']t fit|did ?n[o']t fit|wrong size|size up|size down)\b",
    "shipping_damage": r"\b(?:arrived (?:damaged|broken|cracked|dented|leaking)|damaged (?:in|during) (?:shipping|transit)|"
                       r"came (?:damaged|broken|cracked)|missing (?:parts?|pieces?|screws?)|leak(?:ed|ing|s)|"
                       r"package was (?:damaged|open)|used item|been used)\b",
    "smell": r"\b(?:smell(?:s|ed)? (?:bad|awful|terrible|horrible|strong|like chemicals?)|chemical smell|odor|stinks|stench|toxic smell)\b",
    "hard_to_use": r"\b(?:hard to (?:assemble|install|use|clean|open|put together)|difficult to (?:assemble|install|use|clean)|"
                   r"instructions (?:are|were) (?:terrible|useless|confusing|unclear)|no instructions|confusing)\b",
    "returned": r"\b(?:returned (?:it|them|this)|returning (?:it|them|this)|sent (?:it|them) back|refund|waste of money|"
                r"money back|not worth (?:it|the money))\b",
    "pet_refused": r"\b(?:(?:my )?(?:dog|dogs|cat|cats|puppy|kitten)s? (?:won'?t|wouldn'?t|does ?n[o']t|did ?n[o']t|refuse[sd]?|"
                   r"hates?|ignored?|ignores) (?:use |touch |eat |play |go )?)",
    "skin_reaction": r"\b(?:rash|irritat\w+|burn(?:ed|ing|s)? my|broke me out|breakouts?|allergic reaction|itch(?:y|ing))\b",
    "battery_charge": r"\b(?:battery (?:life|died|dies|does ?n[o']t|won'?t)|won'?t (?:hold a )?charge|stopped charging|"
                      r"doesn'?t hold (?:a )?charge)\b",
}
PRAISE = {
    "quality": r"\b(?:(?:great|good|high|excellent|amazing|nice) quality|well[- ]made|sturdy|durable|solid)\b",
    "easy": r"\b(?:easy to (?:use|clean|install|assemble|set up|put together|apply)|simple to use)\b",
    "value": r"\b(?:(?:great|good|excellent) (?:value|price|deal)|worth (?:the|every)|for the price|bang for)\b",
    "as_described": r"\b(?:as (?:described|pictured|advertised|shown)|exactly what|exactly as)\b",
    "pet_loves": r"\b(?:(?:dog|dogs|cat|cats|puppy|kitten|pup)s? (?:loves?|enjoys?|adores?|is obsessed))\b",
    "gift": r"\b(?:gift|gave (?:it|this|them|one) to|bought (?:it|this|these) for my)\b",
    "would_buy_again": r"\b(?:buy (?:it |this |these )?again|bought (?:a )?(?:second|another|more)|ordered (?:another|more))\b",
}
COMPLAINT_RES = {k: re.compile(v, re.I) for k, v in COMPLAINTS.items()}
PRAISE_RES = {k: re.compile(v, re.I) for k, v in PRAISE.items()}
THEMES = list(COMPLAINTS) + list(PRAISE)
THEME_RES = {**COMPLAINT_RES, **PRAISE_RES}

# =============================================================================

WEIGHT_RE = re.compile(r"([\d.]+)\s*(pounds?|lbs?|ounces?|oz|kilograms?|kg|grams?|g)\b", re.I)
DIMS_RE = re.compile(r"([\d.]+)\s*[x×]\s*([\d.]+)(?:\s*[x×]\s*([\d.]+))?\s*(inches|inch|in|cm|centimeters)?", re.I)
DATE_FMT = "%B %d, %Y"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("phase", choices=["products", "reviews"])
    p.add_argument("--categories", nargs="+", default=CATEGORIES)
    p.add_argument("--raw-dir", default="amazon_2023_raw")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--delete-raw", action="store_true", help="delete each raw file after processing it")
    p.add_argument("--no-download", action="store_true")
    return p.parse_args()


def raw_path(args, cat, kind):
    name = f"meta_{cat}.jsonl.gz" if kind == "meta" else f"{cat}.jsonl.gz"
    return Path(args.raw_dir) / name


def ensure(args, cat, kind):
    path = raw_path(args, cat, kind)
    sub = "meta_categories" if kind == "meta" else "review_categories"
    url = f"{BASE_URL}/{sub}/{path.name}"
    if args.no_download:
        if not path.exists():
            sys.exit(f"missing {path} and --no-download given")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = path.with_suffix(path.suffix + ".complete")
    if path.exists() and marker.exists():
        return path
    # curl resumes partial downloads (-C -) and retries; far more robust than urllib here.
    for attempt in range(6):
        print(f"downloading {url} (attempt {attempt + 1})", flush=True)
        r = subprocess.run(["curl", "-sS", "-L", "-C", "-", "--retry", "5", "-o", str(path), url])
        if r.returncode in (0, 33):  # 33: range not satisfiable = already complete
            marker.touch()
            return path
        time.sleep(2 ** attempt)
    sys.exit(f"download failed: {url}")


def finish(args, path):
    if args.delete_raw:
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".complete").unlink(missing_ok=True)


def lines(path):
    with gzip.open(path, "rb") as f:
        for n, line in enumerate(io.BufferedReader(f, 1 << 20)):
            yield n, line


def weight_lb(details):
    for key in ("Item Weight", "Product Dimensions", "Package Dimensions"):
        v = details.get(key)
        if not isinstance(v, str):
            continue
        m = WEIGHT_RE.search(v.split(";")[-1] if ";" in v else v)
        if not m:
            continue
        num, unit = float(m.group(1)), m.group(2).lower()
        if unit.startswith(("pound", "lb")):
            return num
        if unit.startswith(("ounce", "oz")):
            return num / 16
        if unit.startswith(("kilo", "kg")):
            return num * 2.2046
        if unit.startswith("g"):
            return num / 453.6
    return None


def longest_side_in(details):
    for key in ("Product Dimensions", "Package Dimensions", "Item Dimensions LxWxH"):
        v = details.get(key)
        if not isinstance(v, str):
            continue
        m = DIMS_RE.search(v)
        if not m:
            continue
        sides = [float(x) for x in m.groups()[:3] if x]
        side = max(sides)
        if (m.group(4) or "").lower().startswith(("cm", "centi")):
            side /= 2.54
        return side
    return None


def first_available(details):
    v = details.get("Date First Available")
    if isinstance(v, str):
        try:
            return datetime.strptime(v.strip(), DATE_FMT).date().isoformat()
        except ValueError:
            return ""
    return ""


def risk_flags(text, cat):
    flags = [k for k, rx in RISK_RES.items() if rx.search(text)]
    if cat == "Beauty_and_Personal_Care" and "topical" not in flags and re.search(r"\b(?:oz|ml|skin|face|hair)\b", text, re.I) \
            and re.search(r"\b(?:oil|wash|mask|scrub|treatment)\b", text, re.I):
        flags.append("topical")
    return flags


def products_phase(args):
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["parent_asin", "category", "niche", "category_path", "title", "store", "price", "average_rating",
            "rating_number", "weight_lb", "longest_side_in", "first_available", "risk_flags", "features", "image"]
    for cat in args.categories:
        path = ensure(args, cat, "meta")
        dest = out / f"products_{cat}.csv.gz"
        tmp = dest.with_suffix(".tmp")
        seen = kept = 0
        reasons = Counter()
        t0 = time.time()
        with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for n, line in lines(path):
                seen += 1
                if n % 500000 == 0 and n:
                    print(f"  {cat}: {n:,} listings, {kept:,} kept ({time.time() - t0:.0f}s)", flush=True)
                m = loads(line)
                price = m.get("price")
                try:
                    price = float(price) if price not in (None, "", "None") else None
                except (TypeError, ValueError):
                    price = None
                if price is None:
                    reasons["no price"] += 1
                    continue
                if not PRICE_MIN <= price <= PRICE_MAX:
                    reasons["price out of range"] += 1
                    continue
                if (m.get("rating_number") or 0) < MIN_RATINGS:
                    reasons["too few ratings"] += 1
                    continue
                cats = [c for c in (m.get("categories") or []) if c]
                if len(cats) < 2:
                    reasons["no category path"] += 1
                    continue
                details = m.get("details") or {}
                title = (m.get("title") or "").strip()
                feats = " | ".join(x.strip() for x in (m.get("features") or [])[:4] if isinstance(x, str))[:700]
                flags = risk_flags(f"{title} {feats} {' '.join(cats)}", cat)
                img = ""
                for im in m.get("images") or []:
                    if im.get("variant") == "MAIN":
                        img = im.get("large") or ""
                        break
                w.writerow([
                    m["parent_asin"], cat, " > ".join(cats[1:3]), " > ".join(cats), title,
                    (m.get("store") or details.get("Brand") or "").strip(), price, m.get("average_rating"),
                    m.get("rating_number"), round(weight_lb(details), 3) if weight_lb(details) else "",
                    round(longest_side_in(details), 2) if longest_side_in(details) else "",
                    first_available(details), ";".join(flags), feats, img,
                ])
                kept += 1
        tmp.replace(dest)
        print(f"{cat}: {seen:,} listings, kept {kept:,} -> {dest}", flush=True)
        for r, c in reasons.most_common():
            print(f"    dropped {c:,}: {r}")
        finish(args, path)


def load_kept(out, cat):
    path = out / f"products_{cat}.csv.gz"
    if not path.exists():
        sys.exit(f"{path} missing; run the products phase first")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {row["parent_asin"]: int(float(row["rating_number"])) >= SAMPLE_MIN_RATINGS for row in csv.DictReader(f)}


def band(stars):
    return "low" if stars <= 2 else ("mid" if stars == 3 else "high")


def clean(text):
    text = re.sub(r"<br\s*/?>", " ", text or "", flags=re.I)
    text = re.sub(r"\[\[VIDEOID:[^\]]*\]\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def reviews_phase(args):
    out = Path(args.out_dir)
    for cat in args.categories:
        kept = load_kept(out, cat)
        path = ensure(args, cat, "reviews")
        print(f"{cat}: scanning reviews for {len(kept):,} products", flush=True)
        stats = defaultdict(lambda: {"n": 0, "stars": [0] * 5, "verified": 0, "years": Counter(),
                                     "themes": {b: Counter() for b in ("low", "mid", "high")},
                                     "first": None, "last": None})
        samples = defaultdict(lambda: {"low": [], "mid": [], "high": []})
        t0 = time.time()
        matched = 0
        for n, line in lines(path):
            if n % 2000000 == 0 and n:
                print(f"  {cat}: {n:,} reviews read, {matched:,} matched ({time.time() - t0:.0f}s)", flush=True)
            # Cheap pre-filter before parsing: parent_asin is always present.
            i = line.find(b'"parent_asin": "')
            if i < 0:
                continue
            asin = line[i + 16:i + 26].decode("ascii", "ignore")
            if asin not in kept:
                continue
            r = loads(line)
            asin = r.get("parent_asin")
            if asin not in kept:
                continue
            matched += 1
            stars = int(round(float(r.get("rating") or 0)))
            if not 1 <= stars <= 5:
                continue
            s = stats[asin]
            s["n"] += 1
            s["stars"][stars - 1] += 1
            if r.get("verified_purchase"):
                s["verified"] += 1
            ts = r.get("timestamp") or 0
            year = datetime.fromtimestamp(ts / 1000, timezone.utc).year if ts else None
            if year:
                s["years"][year] += 1
                s["first"] = year if s["first"] is None else min(s["first"], year)
                s["last"] = year if s["last"] is None else max(s["last"], year)
            text = clean(r.get("text"))
            title = clean(r.get("title"))
            blob = f"{title}. {text}"
            b = band(stars)
            for k, rx in THEME_RES.items():
                if rx.search(blob):
                    s["themes"][b][k] += 1
            if kept[asin] and SAMPLE_MIN_CHARS <= len(text) and r.get("verified_purchase"):
                heap = samples[asin][b]
                key = (int(r.get("helpful_vote") or 0), min(len(text), 400), ts)
                item = (key, [asin, stars, title[:150], text[:SAMPLE_MAX_CHARS], int(r.get("helpful_vote") or 0), year or ""])
                if len(heap) < SAMPLE[b]:
                    heapq.heappush(heap, item)
                elif key > heap[0][0]:
                    heapq.heapreplace(heap, item)
        years = list(range(2015, 2024))
        st_cols = (["parent_asin", "reviews", "verified"] + [f"stars_{i}" for i in range(1, 6)]
                   + ["first_year", "last_year"] + [f"y{y}" for y in years]
                   + [f"{b}_{t}" for b in ("low", "mid", "high") for t in THEMES])
        dest = out / f"stats_{cat}.csv.gz"
        with gzip.open(dest.with_suffix(".tmp"), "wt", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(st_cols)
            for asin, s in stats.items():
                w.writerow([asin, s["n"], s["verified"], *s["stars"], s["first"] or "", s["last"] or ""]
                           + [s["years"].get(y, 0) for y in years]
                           + [s["themes"][b][t] for b in ("low", "mid", "high") for t in THEMES])
        dest.with_suffix(".tmp").replace(dest)
        rdest = out / f"reviews_{cat}.csv.gz"
        with gzip.open(rdest.with_suffix(".tmp"), "wt", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["parent_asin", "rating", "review_title", "review_text", "helpful_vote", "year"])
            for asin, bands in samples.items():
                for b in ("low", "mid", "high"):
                    for _, row in sorted(bands[b], reverse=True):
                        w.writerow(row)
        rdest.with_suffix(".tmp").replace(rdest)
        print(f"{cat}: {matched:,} reviews matched for {len(stats):,} products -> {dest}, {rdest} "
              f"({time.time() - t0:.0f}s)", flush=True)
        finish(args, path)


def main():
    args = parse_args()
    if args.phase == "products":
        products_phase(args)
    else:
        reviews_phase(args)


if __name__ == "__main__":
    main()
