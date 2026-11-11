import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ppc_audit as pa  # noqa: E402


def _report(rows):
    cols = ["Customer Search Term", "Campaign Name", "Ad Group Name", "Match Type",
            "Impressions", "Clicks", "Spend", "7 Day Total Sales ", "7 Day Total Orders (#)"]
    return pd.DataFrame(rows, columns=cols)


@pytest.fixture
def report(tmp_path):
    df = _report([
        ["waste term", "C1", "A1", "BROAD", 1000, 25, "$30.00", "$0.00", 0],      # negative (clicks)
        ["tiny term", "C1", "A1", "BROAD", 200, 3, "$2.10", "$0.00", 0],          # wasted, not negative yet
        ["winner", "C1", "A1", "PHRASE", 3000, 40, "$40.00", "$250.00", 10],      # converter -> harvest
        ["exact winner", "C2", "A2", "EXACT", 2000, 30, "$30.00", "$200.00", 8],  # converter -> keep
        ["pricey", "C1", "A1", "BROAD", 900, 20, "$25.00", "$25.00", 1],          # ACoS 100% -> bid down
    ])
    p = tmp_path / "report.csv"
    df.to_csv(p, index=False)
    return p


def test_column_mapping_handles_amazon_headers(report):
    df = pa.load_report(report)
    assert {"search_term", "clicks", "spend", "sales", "orders"} <= set(df.columns)
    assert df["spend"].sum() == pytest.approx(127.10)


def test_missing_columns_raise(tmp_path):
    p = tmp_path / "bad.csv"
    pd.DataFrame({"Search": ["x"], "Clicks": [1]}).to_csv(p, index=False)
    with pytest.raises(ValueError):
        pa.load_report(p)


def test_flags(report):
    res = pa.audit(pa.load_report(report), pa.AuditConfig(target_acos=30, min_clicks=10, min_spend=100))
    negs = set(res["negatives"]["search_term"])
    assert negs == {"waste term", "pricey"}
    assert set(res["wasted"]["search_term"]) == {"waste term", "tiny term"}
    conv = res["converters"].set_index("search_term")["suggested_action"].to_dict()
    assert conv == {"winner": "harvest to exact-match campaign", "exact winner": "keep / raise bid"}
    s = res["summary"]
    assert s["zero_order_spend"] == pytest.approx(32.10)
    assert s["account_acos_pct"] == pytest.approx(round(127.10 / 475 * 100, 1))


def test_default_spend_threshold_uses_aov(report):
    res = pa.audit(pa.load_report(report), pa.AuditConfig(target_acos=30, min_clicks=100))
    # AOV = 475 / 19 = 25 -> threshold 7.50: 'waste term' (30.00) flagged, 'tiny term' (2.10) not
    assert set(res["negatives"]["search_term"]) == {"waste term"}
    assert res["summary"]["min_spend_threshold"] == pytest.approx(7.5)


def test_cli_writes_outputs(report, tmp_path):
    out = tmp_path / "out"
    assert pa.main([str(report), "--out", str(out), "--no-chart"]) == 0
    for f in ["summary.md", "wasted_spend.csv", "negative_keyword_candidates.csv", "top_converters.csv"]:
        assert (out / f).exists()


def test_sample_report_runs():
    res = pa.audit(pa.load_report(ROOT / "sample" / "search_term_report_sample.csv"))
    assert res["summary"]["search_terms"] > 100
    assert res["summary"]["negative_candidates"] > 0
