"""Metrics (protocol section 5) and bootstrap CIs over cities (section 6).

    python -m panel.metrics --wave 2026-11 [--previous-wave 2026-10]

Inputs : data/processed/{wave}/resolved.csv, data/raw/{wave}/*.jsonl (for
         response counts and citations), data/raw/{wave}/local_pack.jsonl
Outputs: data/processed/{wave}/
           metrics_cells.csv    one row per engine x niche x city
           metrics_rr.csv       Recommendation Rate per business per cell
           metrics_summary.csv  means with bootstrap 95% CIs by engine,
                                engine x niche, engine x tier (resampling cities)
           source_mix.csv       cited domains grouped by category
           persistence.csv      between-wave persistence (if --previous-wave)

Cell = engine x niche x city x wave. Within a cell there are up to 3 templates
x k repeats = 15 responses.
"""
import argparse
import csv
import itertools
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from panel.config import ENGINE_IDS, load_cities, load_domain_categories, load_panel
from panel.names import extract_domain, token_set_ratio
from panel.paths import processed_wave_dir, raw_wave_dir
from panel.storage import read_jsonl

CELL = ["engine", "niche", "city_key"]
METRIC_COLS = ["distinct_count", "top3_share", "within_wave_stability", "pack_overlap",
               "ghost_rate", "closed_rate", "fact_error_rate", "phone_mismatch_rate", "unresolved_rate", "no_answer_rate"]


# ---------------------------------------------------------------- loading
def load_responses(wave: str) -> pd.DataFrame:
    rows = []
    for e in ENGINE_IDS:
        for rec in read_jsonl(raw_wave_dir(wave) / ("%s.jsonl" % e)):
            rows.append({"engine": e, "niche": rec["niche"], "template_id": rec["template_id"], "city_key": rec["city_key"],
                         "tier": rec["tier"], "repeat": rec["repeat"], "response_key": rec["key"],
                         "ok": rec.get("error") is None, "has_text": bool(rec.get("response_text")) and not str(rec.get("response_text", "")).startswith("[no_"),
                         "citations": rec.get("citations") or []})
    return pd.DataFrame(rows)


def load_resolved(wave: str) -> pd.DataFrame:
    df = pd.read_csv(processed_wave_dir(wave) / "resolved.csv", dtype=str, keep_default_na=False)
    df = df[df["recommended"] == "1"].copy()
    df["repeat"] = df["repeat"].astype(int)
    return df


def load_local_pack(wave: str) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    out = {}  # type: Dict[Tuple[str, str, str], List[Dict[str, Any]]]
    for rec in read_jsonl(raw_wave_dir(wave) / "local_pack.jsonl"):
        if rec.get("error"):
            continue
        # Sponsored map slots (is_paid) are ads, not organic local results.
        out[(rec["niche"], rec["template_id"], rec["city_key"])] = [e for e in (rec.get("entries") or []) if not e.get("is_paid")]
    return out


# ---------------------------------------------------------------- helpers
def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / float(len(a | b))


def mean_pairwise_jaccard(sets: List[Set[str]]) -> Optional[float]:
    if len(sets) < 2:
        return None
    vals = [jaccard(a, b) for a, b in itertools.combinations(sets, 2)]
    return sum(vals) / len(vals)


def in_local_pack(business_name: str, place_name: str, pack: List[Dict[str, Any]], threshold: float) -> bool:
    for entry in pack:
        title = entry.get("title") or ""
        if token_set_ratio(place_name or business_name, title) >= threshold or token_set_ratio(business_name, title) >= threshold:
            return True
    return False


# ---------------------------------------------------------------- cell metrics
def compute_cells(wave: str, panel: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    responses = load_responses(wave)
    resolved = load_resolved(wave)
    packs = load_local_pack(wave)
    threshold = float(panel["resolution"]["name_similarity_threshold"])
    cities = {c.key: c for c in load_cities()}

    cell_rows = []
    rr_rows = []
    if responses.empty:
        return pd.DataFrame(columns=CELL + METRIC_COLS), pd.DataFrame()
    for (engine, niche, city_key), resp in responses.groupby(CELL):
        ok = resp[resp["ok"]]
        n_resp = len(ok)
        n_answered = int(ok["has_text"].sum())
        ment = resolved[(resolved["engine"] == engine) & (resolved["niche"] == niche) & (resolved["city_key"] == city_key)]
        n_ment = len(ment)
        res_ment = ment[ment["resolution_status"] == "resolved"]

        # Recommendation Rate per business: share of the cell's responses naming b
        rr = {}
        if n_ment:
            named = ment.groupby("business_key")["response_key"].nunique()
            for bk, n in named.items():
                rr[bk] = n / float(n_resp) if n_resp else float("nan")
                name = ment[ment["business_key"] == bk].iloc[0]
                rr_rows.append({"wave": wave, "engine": engine, "niche": niche, "city_key": city_key, "business_key": bk,
                                "name": name["place_name"] or name["name"], "resolution_status": name["resolution_status"],
                                "n_responses_naming": int(n), "n_responses": n_resp, "rr": rr[bk]})

        distinct = int(res_ment["business_key"].nunique())
        counts = ment["business_key"].value_counts()
        top3 = counts.head(3).sum() / float(n_ment) if n_ment else None

        # Within-wave stability: per template, Jaccard over repeats of resolved-or-not name sets; mean over templates
        stab_vals = []
        for template_id, group in ok.groupby("template_id"):
            sets = []
            for rep in sorted(group["repeat"].unique()):
                keys = group[group["repeat"] == rep]["response_key"]
                names = set(ment[ment["response_key"].isin(keys)]["business_key"])
                sets.append(names)
            s = mean_pairwise_jaccard(sets)
            if s is not None:
                stab_vals.append(s)
        stability = sum(stab_vals) / len(stab_vals) if stab_vals else None

        # Pack overlap: share of resolved recommended businesses that appear in the local pack for the same query
        overlap_hits, overlap_n = 0, 0
        for _, m in res_ment.drop_duplicates(["business_key", "template_id"]).iterrows():
            pack = packs.get((niche, m["template_id"], city_key))
            if pack is None:
                continue
            overlap_n += 1
            if in_local_pack(m["name"], m["place_name"], pack, threshold):
                overlap_hits += 1
        pack_overlap = overlap_hits / float(overlap_n) if overlap_n else None

        ghost_n = int((ment["is_ghost"] == "1").sum())
        closed_n = int((ment["is_closed"] == "1").sum())
        fact_n = int((ment["fact_error"] == "1").sum())
        phone_checked = ment[ment["fact_error_phone"].isin(["0", "1"])]
        phone_mm = int((phone_checked["fact_error_phone"] == "1").sum())
        unresolved_n = int((ment["resolution_status"] == "unresolved").sum())
        cell_rows.append({
            "wave": wave, "engine": engine, "niche": niche, "city_key": city_key,
            "tier": cities[city_key].tier if city_key in cities else "",
            "n_responses": n_resp, "n_answered": n_answered, "n_mentions": n_ment, "n_resolved_mentions": len(res_ment),
            "distinct_count": distinct, "top3_share": top3, "within_wave_stability": stability,
            "pack_overlap": pack_overlap,
            "ghost_rate": ghost_n / float(n_ment) if n_ment else None,
            "closed_rate": closed_n / float(n_ment) if n_ment else None,
            "fact_error_rate": fact_n / float(n_ment) if n_ment else None,
            "phone_mismatch_rate": phone_mm / float(len(phone_checked)) if len(phone_checked) else None,
            "unresolved_rate": unresolved_n / float(n_ment) if n_ment else None,
            "no_answer_rate": 1 - n_answered / float(n_resp) if n_resp else None,
        })
    return pd.DataFrame(cell_rows), pd.DataFrame(rr_rows)


# ---------------------------------------------------------------- bootstrap
def bootstrap_ci(values_by_city: Dict[str, float], iterations: int, seed: int) -> Tuple[float, float, float]:
    """Mean and percentile 95% CI, resampling cities with replacement."""
    import random
    cities = list(values_by_city.keys())
    vals = [values_by_city[c] for c in cities]
    mean = sum(vals) / len(vals)
    if len(cities) < 2:
        return mean, float("nan"), float("nan")
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [vals[rng.randrange(len(vals))] for _ in range(len(vals))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * (iterations - 1))]
    hi = means[int(0.975 * (iterations - 1))]
    return mean, lo, hi


def summarise(cells: pd.DataFrame, panel: Dict[str, Any]) -> pd.DataFrame:
    iters = int(panel["metrics"]["bootstrap_iterations"])
    seed = int(panel["metrics"]["bootstrap_seed"])
    rows = []
    groupings = [("engine", ["engine"]), ("engine_x_niche", ["engine", "niche"]), ("engine_x_tier", ["engine", "tier"])]
    for level, keys in groupings:
        for gkey, g in cells.groupby(keys):
            gkey = gkey if isinstance(gkey, tuple) else (gkey,)
            for metric in METRIC_COLS:
                # Average within city first (so a city counts once), then bootstrap over cities
                per_city = g.dropna(subset=[metric]).groupby("city_key")[metric].mean()
                if per_city.empty:
                    continue
                mean, lo, hi = bootstrap_ci(per_city.to_dict(), iters, seed)
                row = {"level": level, "metric": metric, "n_cities": len(per_city), "mean": mean, "ci_low": lo, "ci_high": hi}
                row.update(dict(zip(keys, gkey)))
                rows.append(row)
    cols = ["level", "engine", "niche", "tier", "metric", "n_cities", "mean", "ci_low", "ci_high"]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


# ---------------------------------------------------------------- source mix
def categorise_domain(domain: str, mapping: Dict[str, str]) -> str:
    d = domain.lower()
    parts = d.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in mapping:
            return mapping[cand]
    if d.endswith(".gov") or d.endswith(".mil") or ".gov." in d:
        return "government"
    if d.endswith(".edu"):
        return "government"
    if any(t in d for t in ("news", "times", "tribune", "gazette", "herald", "journal", "post", "wiki")):
        return "media"
    return "business_site"


def source_mix(wave: str) -> pd.DataFrame:
    mapping = load_domain_categories()
    responses = load_responses(wave)
    rows = []
    for _, r in responses.iterrows():
        if not r["ok"]:
            continue
        for url in set(r["citations"]):
            dom = extract_domain(url)
            if not dom:
                continue
            rows.append({"wave": wave, "engine": r["engine"], "niche": r["niche"], "city_key": r["city_key"],
                         "domain": dom, "category": categorise_domain(dom, mapping)})
    if not rows:
        return pd.DataFrame(columns=["wave", "engine", "niche", "category", "n_citations", "share"])
    df = pd.DataFrame(rows)
    agg = df.groupby(["wave", "engine", "niche", "category"]).size().reset_index(name="n_citations")
    agg["share"] = agg["n_citations"] / agg.groupby(["wave", "engine", "niche"])["n_citations"].transform("sum")
    domains = df.groupby(["wave", "engine", "domain", "category"]).size().reset_index(name="n_citations").sort_values("n_citations", ascending=False)
    domains.to_csv(processed_wave_dir(wave) / "source_domains.csv", index=False)
    return agg


# ---------------------------------------------------------------- persistence
def persistence(wave: str, previous_wave: str, threshold: float) -> pd.DataFrame:
    cur = pd.read_csv(processed_wave_dir(wave) / "metrics_rr.csv")
    prev = pd.read_csv(processed_wave_dir(previous_wave) / "metrics_rr.csv")
    keys = ["engine", "niche", "city_key", "business_key"]
    prev_hi = prev[prev["rr"] >= threshold][keys]
    cur_hi = cur[cur["rr"] >= threshold][keys]
    merged = prev_hi.merge(cur_hi.assign(persisted=1), on=keys, how="left").fillna({"persisted": 0})
    rows = []
    for (engine, niche), g in merged.groupby(["engine", "niche"]):
        rows.append({"wave_t": previous_wave, "wave_t1": wave, "engine": engine, "niche": niche,
                     "n_businesses_rr_ge_threshold_t": len(g), "persistence": g["persisted"].mean()})
    for engine, g in merged.groupby("engine"):
        rows.append({"wave_t": previous_wave, "wave_t1": wave, "engine": engine, "niche": "ALL",
                     "n_businesses_rr_ge_threshold_t": len(g), "persistence": g["persisted"].mean()})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- main
def run(wave: str, previous_wave: Optional[str] = None) -> Dict[str, Any]:
    panel = load_panel()
    out_dir = processed_wave_dir(wave)
    cells, rr = compute_cells(wave, panel)
    cells.to_csv(out_dir / "metrics_cells.csv", index=False)
    rr.to_csv(out_dir / "metrics_rr.csv", index=False)
    summary = summarise(cells, panel) if not cells.empty else pd.DataFrame()
    summary.to_csv(out_dir / "metrics_summary.csv", index=False)
    mix = source_mix(wave)
    mix.to_csv(out_dir / "source_mix.csv", index=False)
    info = {"cells": len(cells), "businesses": len(rr), "summary_rows": len(summary), "source_mix_rows": len(mix)}
    if previous_wave:
        pers = persistence(wave, previous_wave, float(panel["metrics"]["persistence_rr_threshold"]))
        pers.to_csv(out_dir / "persistence.csv", index=False)
        info["persistence_rows"] = len(pers)
    return info


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compute protocol section 5 metrics for a wave.")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--previous-wave", default=None, help="Earlier processed wave for between-wave persistence")
    args = ap.parse_args(argv)
    print("metrics: %s -> %s" % (run(args.wave, args.previous_wave), processed_wave_dir(args.wave)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
