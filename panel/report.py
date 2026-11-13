"""Markdown wave summary (clearly labelled DRAFT).

    python -m panel.report --wave 2026-11 [--out reports/2026-11_draft.md]
"""
import argparse
import datetime as dt
import sys
from typing import List, Optional

import pandas as pd

from panel.paths import REPORTS_DIR, processed_wave_dir
from panel.storage import read_jsonl
from panel.paths import raw_wave_dir
from panel.config import ENGINE_IDS

METRIC_LABELS = [
    ("distinct_count", "Distinct resolved businesses (per cell)"),
    ("top3_share", "Top-3 share"),
    ("within_wave_stability", "Within-wave stability (mean Jaccard)"),
    ("pack_overlap", "Pack overlap"),
    ("ghost_rate", "Ghost rate"),
    ("closed_rate", "Closed rate"),
    ("fact_error_rate", "Website/address error rate"),
    ("phone_mismatch_rate", "Phone differs from Google profile (of stated phones)"),
    ("unresolved_rate", "Unresolved rate"),
    ("no_answer_rate", "No-answer rate"),
]


def fmt(v, pct=False) -> str:
    try:
        if v is None or pd.isna(v):
            return "n/a"
    except TypeError:
        pass
    if isinstance(v, str):
        return v
    return ("%.1f%%" % (100 * v)) if pct else ("%.2f" % v)


def md_table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def collection_table(wave: str) -> str:
    rows = []
    for e in ENGINE_IDS:
        recs = list(read_jsonl(raw_wave_dir(wave) / ("%s.jsonl" % e)))
        if not recs:
            continue
        ok = [r for r in recs if not r.get("error")]
        models = sorted({str(r.get("returned_model")) for r in ok})
        lat = [r["latency_s"] for r in ok if r.get("latency_s") is not None]
        rows.append([e, recs[0].get("requested_model", ""), ", ".join(models) or "n/a", len(recs), len(recs) - len(ok),
                     fmt(sum(lat) / len(lat)) if lat else "n/a"])
    return md_table(["Engine", "Requested model", "Returned model(s)", "Calls", "Errors", "Mean latency (s)"], rows)


def summary_table(summary: pd.DataFrame, level: str, keys: List[str]) -> str:
    sub = summary[summary["level"] == level]
    if sub.empty:
        return "_no data_"
    headers = keys + [label for _, label in METRIC_LABELS]
    rows = []
    for gkey, g in sub.groupby(keys):
        gkey = gkey if isinstance(gkey, tuple) else (gkey,)
        row = list(gkey)
        for metric, _ in METRIC_LABELS:
            m = g[g["metric"] == metric]
            if m.empty:
                row.append("n/a")
                continue
            r = m.iloc[0]
            pct = metric != "distinct_count"
            row.append("%s [%s, %s]" % (fmt(r["mean"], pct), fmt(r["ci_low"], pct), fmt(r["ci_high"], pct)))
        rows.append(row)
    return md_table(headers, rows)


def source_mix_table(mix: pd.DataFrame) -> str:
    if mix.empty:
        return "_no citations_"
    piv = mix.groupby(["engine", "category"])["n_citations"].sum().reset_index()
    piv["share"] = piv["n_citations"] / piv.groupby("engine")["n_citations"].transform("sum")
    cats = sorted(piv["category"].unique())
    rows = []
    for engine, g in piv.groupby("engine"):
        d = dict(zip(g["category"], g["share"]))
        rows.append([engine, int(g["n_citations"].sum())] + [fmt(d.get(c, 0.0), pct=True) for c in cats])
    return md_table(["Engine", "Citations"] + cats, rows)


def top_businesses(rr: pd.DataFrame, n: int = 15) -> str:
    if rr.empty:
        return "_no businesses_"
    top = rr.sort_values(["rr", "n_responses_naming"], ascending=False).head(n)
    rows = [[r["engine"], r["niche"], r["city_key"], r["name"], r["resolution_status"], fmt(r["rr"], pct=True)] for _, r in top.iterrows()]
    return md_table(["Engine", "Niche", "City", "Business", "Resolution", "RR"], rows)


def build(wave: str) -> str:
    d = processed_wave_dir(wave)
    summary = pd.read_csv(d / "metrics_summary.csv") if (d / "metrics_summary.csv").exists() else pd.DataFrame()
    mix = pd.read_csv(d / "source_mix.csv") if (d / "source_mix.csv").exists() else pd.DataFrame()
    rr = pd.read_csv(d / "metrics_rr.csv") if (d / "metrics_rr.csv").exists() else pd.DataFrame()
    cells = pd.read_csv(d / "metrics_cells.csv") if (d / "metrics_cells.csv").exists() else pd.DataFrame()
    pers = pd.read_csv(d / "persistence.csv") if (d / "persistence.csv").exists() else None

    parts = [
        "# Open Local AI Panel: wave %s" % wave,
        "",
        "> **DRAFT. Auto-generated on %s. Not reviewed. Not for citation.**" % dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "> Protocol v0.1 (see PROTOCOL.md). All associations are correlational. Ghost rate in the tables counts only labeled pairs; the sample-based estimate (protocol 4.6) is in ghost_estimate.csv.",
        "",
        "## 1. Collection",
        "",
        collection_table(wave),
        "",
        "Cells (engine x niche x city) with data: %d. Responses: %s. Recommended mentions: %s." % (
            len(cells), int(cells["n_responses"].sum()) if not cells.empty else 0, int(cells["n_mentions"].sum()) if not cells.empty else 0),
        "",
        "## 2. Metrics by engine (mean over cities, bootstrap 95% CI over cities)",
        "",
        summary_table(summary, "engine", ["engine"]) if not summary.empty else "_no data_",
        "",
        "## 3. Metrics by engine x niche",
        "",
        summary_table(summary, "engine_x_niche", ["engine", "niche"]) if not summary.empty else "_no data_",
        "",
        "## 4. Metrics by engine x city tier",
        "",
        summary_table(summary, "engine_x_tier", ["engine", "tier"]) if not summary.empty else "_no data_",
        "",
        "## 5. Source mix (share of cited URLs by domain category)",
        "",
        source_mix_table(mix),
        "",
        "## 6. Most-recommended businesses (top 15 by Recommendation Rate)",
        "",
        top_businesses(rr),
        "",
    ]
    if pers is not None and not pers.empty:
        parts += ["## 7. Between-wave persistence (RR >= 0.4 in t and t+1)", "",
                  md_table(["Engine", "Niche", "n (t)", "Persistence"],
                           [[r["engine"], r["niche"], int(r["n_businesses_rr_ge_threshold_t"]), fmt(r["persistence"], pct=True)] for _, r in pers.iterrows()]), ""]
    else:
        parts += ["## 7. Between-wave persistence", "", "_Requires a previous processed wave (`panel.metrics --previous-wave`)._", ""]
    parts += ["## Limitations", "",
              "- API answers differ from consumer apps (protocol 3.3). Model strings above are what each API returned.",
              "- Extraction and resolution error: see validation (protocol 4.3) and the unresolved rate.",
              "- 3 niches x 20 cities are not representative of all US local businesses.", ""]
    return "\n".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Draft markdown report for a wave.")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    text = build(args.wave)
    out = args.out or str(REPORTS_DIR / ("%s_draft.md" % args.wave))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("report: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
