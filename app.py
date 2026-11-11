"""Gradio demo for the search-term audit (works locally or as a Hugging Face Space)."""
import tempfile
from pathlib import Path

import gradio as gr

import ppc_audit as pa

SAMPLE = Path(__file__).parent / "sample" / "search_term_report_sample.csv"


def run(file, target_acos, min_clicks):
    path = file if file else str(SAMPLE)
    cfg = pa.AuditConfig(target_acos=float(target_acos), min_clicks=int(min_clicks))
    res = pa.audit(pa.load_report(path), cfg)
    out = pa.write_outputs(res, tempfile.mkdtemp(prefix="ppc_audit_"))
    cols = ["search_term", "campaign", "clicks", "spend", "orders", "acos_pct"]
    return (pa.render_summary(res).split("## Top 10")[0],
            str(out / "audit_chart.png"),
            res["negatives"][cols + ["reason"]].round(2).head(50),
            res["converters"][cols + ["suggested_action"]].round(2).head(50),
            [str(out / f) for f in ("negative_keyword_candidates.csv", "top_converters.csv",
                                    "wasted_spend.csv", "summary.md")])


with gr.Blocks(title="Amazon PPC Search-Term Audit") as demo:
    gr.Markdown("# Amazon PPC Search-Term Audit\nUpload an Amazon Ads search-term report (CSV/XLSX), "
                "or leave it empty to run the synthetic sample. Files are processed in memory for this session only.")
    with gr.Row():
        f = gr.File(label="Search-term report", file_types=[".csv", ".xlsx"], type="filepath")
        with gr.Column():
            acos = gr.Slider(5, 100, value=30, step=1, label="Target ACoS (%)")
            clicks = gr.Slider(3, 50, value=10, step=1, label="Clicks with 0 orders before flagging")
            btn = gr.Button("Run audit", variant="primary")
    summary = gr.Markdown()
    chart = gr.Image(label="Audit chart")
    negs = gr.Dataframe(label="Negative keyword candidates")
    conv = gr.Dataframe(label="Top converters")
    files = gr.File(label="Download results", file_count="multiple")
    btn.click(run, [f, acos, clicks], [summary, chart, negs, conv, files])

if __name__ == "__main__":
    demo.launch()
