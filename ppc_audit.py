#!/usr/bin/env python3
"""Amazon PPC search-term audit.

Reads an Amazon Ads "Search term" report (CSV or XLSX) and flags:
  * wasted spend      - search terms with clicks/spend but zero orders
  * negative keyword candidates - zero-order terms past a click or spend threshold,
                        plus terms whose ACoS is far above target
  * top converters    - terms with enough orders at or below target ACoS
                        (harvest candidates for exact-match campaigns)

Usage:
    python ppc_audit.py sample/search_term_report_sample.csv --target-acos 30 --out examples/output
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Amazon changes header wording between report versions and marketplaces.
# Each canonical field lists the variants we accept (compared lower-case, trimmed).
COLUMN_ALIASES: dict[str, list[str]] = {
    "search_term": ["customer search term", "search term", "query"],
    "campaign": ["campaign name", "campaign"],
    "ad_group": ["ad group name", "ad group"],
    "targeting": ["targeting", "keyword", "keyword text"],
    "match_type": ["match type"],
    "impressions": ["impressions"],
    "clicks": ["clicks"],
    "spend": ["spend", "cost"],
    "sales": ["7 day total sales", "14 day total sales", "total sales", "sales"],
    "orders": ["7 day total orders (#)", "14 day total orders (#)", "7 day total orders",
               "total orders", "orders", "purchases"],
}
REQUIRED = ["search_term", "clicks", "spend", "sales", "orders"]


@dataclass
class AuditConfig:
    target_acos: float = 30.0       # percent
    min_clicks: int = 10            # clicks with 0 orders before a term is a negative candidate
    min_spend: float | None = None  # spend with 0 orders; default = average order value * target ACoS
    high_acos_multiple: float = 2.0 # ACoS above target * this -> negative / bid-down candidate
    min_orders_converter: int = 2   # orders needed to call a term a top converter


def _clean_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h)).strip().lower()


def map_columns(df: pd.DataFrame) -> pd.DataFrame:
    lookup = {_clean_header(c): c for c in df.columns}
    rename = {}
    for canon, variants in COLUMN_ALIASES.items():
        for v in variants:
            if v in lookup:
                rename[lookup[v]] = canon
                break
    out = df.rename(columns=rename)
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")
    return out


def _to_number(s: pd.Series) -> pd.Series:
    if s.dtype.kind in "if":
        return s.fillna(0)
    cleaned = s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True).replace("", "0")
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def load_report(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    df = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    df = map_columns(df)
    for c in ["impressions", "clicks", "spend", "sales", "orders"]:
        if c in df.columns:
            df[c] = _to_number(df[c])
    df["search_term"] = df["search_term"].astype(str).str.strip().str.lower()
    for c in ["campaign", "ad_group", "targeting", "match_type"]:
        if c not in df.columns:
            df[c] = ""
    return df


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """One row per search term + campaign + ad group (Amazon reports can split by date)."""
    keys = ["search_term", "campaign", "ad_group", "targeting", "match_type"]
    num = [c for c in ["impressions", "clicks", "spend", "sales", "orders"] if c in df.columns]
    g = df.groupby(keys, dropna=False, as_index=False)[num].sum()
    g["ctr_pct"] = (g["clicks"] / g["impressions"].replace(0, np.nan) * 100) if "impressions" in g else np.nan
    g["cpc"] = g["spend"] / g["clicks"].replace(0, np.nan)
    g["cvr_pct"] = g["orders"] / g["clicks"].replace(0, np.nan) * 100
    g["acos_pct"] = g["spend"] / g["sales"].replace(0, np.nan) * 100
    g["roas"] = g["sales"] / g["spend"].replace(0, np.nan)
    return g


def audit(df: pd.DataFrame, cfg: AuditConfig | None = None) -> dict:
    cfg = cfg or AuditConfig()
    g = aggregate(df)
    total_spend, total_sales, total_orders = g["spend"].sum(), g["sales"].sum(), g["orders"].sum()
    aov = total_sales / total_orders if total_orders else 0
    min_spend = cfg.min_spend if cfg.min_spend is not None else round(aov * cfg.target_acos / 100, 2)

    zero = g["orders"] == 0
    wasted = g[zero & (g["spend"] > 0)].sort_values("spend", ascending=False)

    neg_zero = zero & ((g["clicks"] >= cfg.min_clicks) | ((min_spend > 0) & (g["spend"] >= min_spend)))
    neg_acos = ((~zero) & (g["acos_pct"] > cfg.target_acos * cfg.high_acos_multiple)
                & (g["orders"] <= 1) & (g["clicks"] >= cfg.min_clicks))
    negatives = g[neg_zero | neg_acos].copy()
    negatives["reason"] = negatives.apply(
        lambda r: (f"0 orders after {int(r.clicks)} clicks / {r.spend:.2f} spend" if r.orders == 0
                   else f"ACoS {r.acos_pct:.0f}% vs target {cfg.target_acos:.0f}%"), axis=1)
    negatives["suggested_action"] = negatives["orders"].map(
        lambda o: "add as negative exact" if o == 0 else "bid down or negative exact")
    negatives = negatives.sort_values("spend", ascending=False)

    conv = g[(g["orders"] >= cfg.min_orders_converter) & (g["acos_pct"] <= cfg.target_acos)].copy()
    conv["suggested_action"] = conv["match_type"].astype(str).str.lower().map(
        lambda m: "keep / raise bid" if m == "exact" else "harvest to exact-match campaign")
    conv = conv.sort_values(["orders", "sales"], ascending=False)

    neg_spend = negatives.loc[negatives["orders"] == 0, "spend"].sum()
    summary = {
        "search_terms": int(len(g)),
        "total_spend": round(float(total_spend), 2),
        "total_sales": round(float(total_sales), 2),
        "total_orders": int(total_orders),
        "account_acos_pct": round(float(total_spend / total_sales * 100), 1) if total_sales else None,
        "target_acos_pct": cfg.target_acos,
        "zero_order_spend": round(float(wasted["spend"].sum()), 2),
        "zero_order_spend_pct": round(float(wasted["spend"].sum() / total_spend * 100), 1) if total_spend else 0.0,
        "negative_candidates": int(len(negatives)),
        "negative_candidate_spend": round(float(neg_spend), 2),
        "top_converters": int(len(conv)),
        "min_spend_threshold": min_spend,
    }
    return {"summary": summary, "terms": g, "wasted": wasted, "negatives": negatives, "converters": conv}


def write_outputs(res: dict, out_dir: str | Path, chart: bool = True) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["search_term", "campaign", "ad_group", "match_type", "clicks", "spend", "orders", "sales", "acos_pct"]
    res["wasted"][cols].round(2).to_csv(out / "wasted_spend.csv", index=False)
    res["negatives"][cols + ["reason", "suggested_action"]].round(2).to_csv(out / "negative_keyword_candidates.csv", index=False)
    res["converters"][cols + ["cvr_pct", "suggested_action"]].round(2).to_csv(out / "top_converters.csv", index=False)
    (out / "summary.md").write_text(render_summary(res), encoding="utf-8")
    if chart:
        _chart(res, out / "audit_chart.png")
    return out


def render_summary(res: dict) -> str:
    s = res["summary"]
    lines = [
        "# Search-term audit summary", "",
        f"- Search-term rows analysed (term x campaign / ad group): **{s['search_terms']}**",
        f"- Spend: **{s['total_spend']:,.2f}** | Sales: **{s['total_sales']:,.2f}** | Orders: **{s['total_orders']}**",
        f"- Account ACoS: **{s['account_acos_pct']}%** (target {s['target_acos_pct']:.0f}%)",
        f"- Spend on zero-order terms: **{s['zero_order_spend']:,.2f}** ({s['zero_order_spend_pct']}% of spend)",
        f"- Negative keyword candidates: **{s['negative_candidates']}** "
        f"(zero-order spend among them: {s['negative_candidate_spend']:,.2f})",
        f"- Top converters to harvest / protect: **{s['top_converters']}**", "",
        "## Top 10 negative keyword candidates", "",
        _md_table(res["negatives"].head(10), ["search_term", "clicks", "spend", "orders", "reason"]), "",
        "## Top 10 converters", "",
        _md_table(res["converters"].head(10), ["search_term", "match_type", "orders", "sales", "acos_pct", "suggested_action"]),
        "", "_Thresholds are starting points. Review every negative before adding it; "
        "a term with few clicks may simply need more data._",
    ]
    return "\n".join(lines) + "\n"


def _md_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_None found._"
    d = df[cols].copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:,.2f}")
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    rows = ["| " + " | ".join(str(v) for v in r) + " |" for r in d.itertuples(index=False)]
    return head + "\n" + "\n".join(rows)


def _chart(res: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = res["summary"]
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.8), gridspec_kw={"width_ratios": [1, 1.6]})
    conv_spend = res["converters"]["spend"].sum()
    other = s["total_spend"] - s["zero_order_spend"] - conv_spend
    parts = [("Zero-order terms", s["zero_order_spend"], "#ef4444"),
             ("Other converting terms", max(other, 0), "#94a3b8"),
             ("Top converters", conv_spend, "#10b981")]
    left = 0
    for label, val, color in parts:
        ax[0].barh([0], [val], left=left, color=color, label=f"{label}: {val:,.0f}")
        left += val
    ax[0].set_yticks([]); ax[0].set_title("Where the spend went", fontweight="bold")
    ax[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False, fontsize=9)
    top = res["negatives"].head(10).iloc[::-1]
    ax[1].barh(top["search_term"], top["spend"], color="#f97316")
    ax[1].set_title("Top 10 negative keyword candidates by spend", fontweight="bold")
    ax[1].set_xlabel("Spend")
    for a in ax:
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"Search-term audit | ACoS {s['account_acos_pct']}% vs target {s['target_acos_pct']:.0f}% | "
                 f"{s['zero_order_spend_pct']}% of spend on zero-order terms", fontsize=11)
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Audit an Amazon Ads search-term report.")
    p.add_argument("report", help="Search-term report (.csv or .xlsx)")
    p.add_argument("--target-acos", type=float, default=30.0, help="Target ACoS in percent (default 30)")
    p.add_argument("--min-clicks", type=int, default=10, help="Clicks with 0 orders to flag a negative (default 10)")
    p.add_argument("--min-spend", type=float, default=None, help="Spend with 0 orders to flag a negative "
                   "(default: average order value x target ACoS)")
    p.add_argument("--min-orders", type=int, default=2, help="Orders needed for a top converter (default 2)")
    p.add_argument("--out", default="audit_output", help="Output folder (default audit_output)")
    p.add_argument("--no-chart", action="store_true", help="Skip the PNG chart")
    a = p.parse_args(argv)
    cfg = AuditConfig(target_acos=a.target_acos, min_clicks=a.min_clicks, min_spend=a.min_spend,
                      min_orders_converter=a.min_orders)
    try:
        res = audit(load_report(a.report), cfg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = write_outputs(res, a.out, chart=not a.no_chart)
    print(render_summary(res).split("## Top 10")[0].strip())
    print(f"\nFiles written to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
