#!/usr/bin/env python3
"""Generate a synthetic Amazon Sponsored Products search-term report.

Everything here is made up: product, campaigns, search terms and numbers.
Column names follow the Amazon Ads "Sponsored Products > Search term" report.

    python sample/generate_sample.py --out sample/search_term_report_sample.csv
    python sample/generate_sample.py --days 60 --start 2026-08-01 --out big.csv   # one row per term per day
"""
import argparse
import datetime as dt

import numpy as np
import pandas as pd

SEED = 42
PRICE = 24.99  # fictional insulated water bottle

CORE = ["insulated water bottle", "stainless steel water bottle", "water bottle 32 oz",
        "insulated water bottle with straw", "metal water bottle", "vacuum insulated bottle",
        "water bottle for gym", "leak proof water bottle", "bpa free water bottle",
        "double wall water bottle", "water bottle 1 litre", "sports water bottle",
        "water bottle with handle", "cold water bottle 24 hours", "hiking water bottle"]
RELATED = ["tumbler with lid", "travel mug", "kids water bottle", "glass water bottle",
           "water bottle sleeve", "shaker bottle", "thermos flask", "coffee tumbler",
           "collapsible water bottle", "water jug 1 gallon", "plastic water bottle",
           "water bottle cleaning brush", "insulated lunch bag", "ice pack", "camping mug"]
COMPETITOR = ["brand x bottle", "brand y tumbler", "brand z flask 40 oz", "brand x replacement lid"]
LONGTAIL = [f"{c} {s}" for c in ["insulated water bottle", "steel bottle", "water bottle"]
            for s in ["black", "pink", "for women", "for men", "for school", "with times", "dishwasher safe"]]

CAMPAIGNS = [("SP | Auto | Bottle", "Auto targets", "close-match", "-"),
             ("SP | Broad | Bottle", "Broad core", "insulated water bottle", "BROAD"),
             ("SP | Phrase | Bottle", "Phrase core", "\"water bottle\"", "PHRASE"),
             ("SP | Exact | Bottle", "Exact winners", "[insulated water bottle]", "EXACT")]


def term_profile(term, rng):
    """Base CTR and conversion rate by term type (relevance drives conversion)."""
    if term in CORE:
        return rng.uniform(0.004, 0.009), rng.uniform(0.08, 0.18)
    if term in LONGTAIL:
        return rng.uniform(0.005, 0.012), rng.uniform(0.06, 0.16)
    if term in COMPETITOR:
        return rng.uniform(0.003, 0.007), rng.uniform(0.0, 0.015)
    return rng.uniform(0.003, 0.008), rng.uniform(0.0, 0.02)  # related but wrong intent


def generate(days=1, seed=SEED, start=dt.date(2026, 10, 1)):
    rng = np.random.default_rng(seed)
    terms = CORE + RELATED + COMPETITOR + LONGTAIL
    rows = []
    for camp, adg, targeting, match in CAMPAIGNS:
        pool = terms if match in ("-", "BROAD") else (
            [t for t in terms if "water bottle" in t] if match == "PHRASE" else CORE[:6])
        for term in pool:
            ctr, cvr = term_profile(term, rng)
            monthly_impr = rng.lognormal(7.4 if term in CORE else 7.0, 0.8)
            base_impr = monthly_impr if days == 1 else monthly_impr / 30
            for d in range(days):
                impr = int(rng.poisson(max(base_impr, 1)))
                clicks = int(rng.binomial(impr, ctr)) if impr else 0
                if clicks == 0 and days > 1:
                    continue
                cpc = rng.uniform(0.55, 1.45) * (1.2 if match == "EXACT" else 1.0)
                spend = round(clicks * cpc, 2)
                orders = int(rng.binomial(clicks, cvr)) if clicks else 0
                units = orders + int(rng.binomial(orders, 0.1)) if orders else 0
                sales = round(units * PRICE, 2)
                when = ({"Date": (start + dt.timedelta(days=d)).isoformat()} if days > 1 else
                        {"Start Date": start.isoformat(), "End Date": (start + dt.timedelta(days=29)).isoformat()})
                rows.append({
                    **when,
                    "Portfolio name": "Demo Bottles",
                    "Currency": "USD",
                    "Campaign Name": camp, "Ad Group Name": adg,
                    "Targeting": targeting, "Match Type": match,
                    "Customer Search Term": term,
                    "Impressions": impr, "Clicks": clicks,
                    "Click-Thru Rate (CTR)": round(clicks / impr, 4) if impr else 0,
                    "Cost Per Click (CPC)": round(spend / clicks, 2) if clicks else 0,
                    "Spend": spend,
                    "7 Day Total Sales": sales,
                    "Total Advertising Cost of Sales (ACOS)": round(spend / sales, 4) if sales else 0,
                    "Total Return on Advertising Spend (ROAS)": round(sales / spend, 2) if spend else 0,
                    "7 Day Total Orders (#)": orders,
                    "7 Day Total Units (#)": units,
                    "7 Day Conversion Rate": round(orders / clicks, 4) if clicks else 0,
                })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="1 = 30-day summary report; >1 = daily rows")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--start", default="2026-10-01", help="first day (YYYY-MM-DD)")
    ap.add_argument("--out", default="sample/search_term_report_sample.csv")
    a = ap.parse_args()
    df = generate(a.days, a.seed, dt.date.fromisoformat(a.start))
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df)} rows to {a.out}")
