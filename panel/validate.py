"""Extraction validation (protocol 4.3).

Step 1: draw the hand-coding sample
    python -m panel.validate sample --wave 2026-11 [--n 100] [--seed 1]
  -> data/processed/{wave}/validation_sample.csv  (one row per response, with
     the response text and the extractor's names, plus empty columns for the
     human coder to fill: human_recommended_names, separated by " | ").

Step 2: score a filled sample
    python -m panel.validate score --wave 2026-11 --filled path/to/filled.csv
  -> precision and recall at the business-name level, micro-averaged across
     the sample, plus per-engine breakdown. Names are matched after
     normalisation (panel.names.normalize_name) with token-set ratio >= 0.85
     so spelling/suffix variation does not count as disagreement.
"""
import argparse
import csv
import random
import sys
from typing import Dict, List, Optional, Tuple

from panel.config import ENGINE_IDS, load_panel
from panel.names import token_set_ratio
from panel.paths import processed_wave_dir, raw_wave_dir
from panel.storage import read_jsonl

SEP = " | "
SAMPLE_COLUMNS = ["response_key", "engine", "niche", "template_id", "city", "state", "repeat", "response_text",
                  "extractor_recommended_names", "extractor_mentioned_names",
                  "human_recommended_names", "human_mentioned_names", "coder_notes"]


def draw_sample(wave: str, n: int, seed: int) -> List[Dict[str, str]]:
    extractions = {}
    for ex in read_jsonl(processed_wave_dir(wave) / "extractions.jsonl"):
        if not ex.get("error"):
            extractions[ex["response_key"]] = ex
    pool = []
    for engine_id in ENGINE_IDS:
        for rec in read_jsonl(raw_wave_dir(wave) / ("%s.jsonl" % engine_id)):
            if rec.get("error") or not rec.get("response_text") or rec["key"] not in extractions:
                continue
            pool.append(rec)
    rng = random.Random(seed)
    rng.shuffle(pool)
    rows = []
    for rec in pool[:n]:
        ex = extractions[rec["key"]]
        recs = [b["name"] for b in ex["businesses"] if b["recommended"]]
        ments = [b["name"] for b in ex["businesses"] if not b["recommended"]]
        rows.append({
            "response_key": rec["key"], "engine": rec["engine"], "niche": rec["niche"], "template_id": rec["template_id"],
            "city": rec["city"], "state": rec["state"], "repeat": rec["repeat"], "response_text": rec["response_text"],
            "extractor_recommended_names": SEP.join(recs), "extractor_mentioned_names": SEP.join(ments),
            "human_recommended_names": "", "human_mentioned_names": "", "coder_notes": "",
        })
    return rows


def write_sample(wave: str, rows: List[Dict[str, str]]):
    path = processed_wave_dir(wave) / "validation_sample.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SAMPLE_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def split_names(cell: str) -> List[str]:
    return [p.strip() for p in (cell or "").split("|") if p.strip()]


def match_sets(pred: List[str], gold: List[str], threshold: float) -> Tuple[int, int, int]:
    """Greedy one-to-one matching. Returns (tp, fp, fn)."""
    remaining = list(gold)
    tp = 0
    for p in pred:
        best_i, best_s = -1, 0.0
        for i, g in enumerate(remaining):
            s = token_set_ratio(p, g)
            if s > best_s:
                best_i, best_s = i, s
        if best_i >= 0 and best_s >= threshold:
            tp += 1
            remaining.pop(best_i)
    fp = len(pred) - tp
    fn = len(remaining)
    return tp, fp, fn


def score(filled_path: str, threshold: float = 0.85) -> Dict[str, Dict[str, float]]:
    totals = {}  # type: Dict[str, List[int]]
    with open(filled_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("human_recommended_names") or "").strip() and not (row.get("coder_notes") or "").strip():
                # Treat an entirely blank human cell as "not coded yet" only if notes are blank too.
                # A coder marks "no businesses" explicitly by writing e.g. "none" in coder_notes.
                continue
            gold = split_names(row.get("human_recommended_names", ""))
            pred = split_names(row.get("extractor_recommended_names", ""))
            tp, fp, fn = match_sets(pred, gold, threshold)
            for key in ("ALL", row.get("engine", "?")):
                t = totals.setdefault(key, [0, 0, 0, 0])
                t[0] += tp
                t[1] += fp
                t[2] += fn
                t[3] += 1
    out = {}
    for key, (tp, fp, fn, n) in totals.items():
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / (tp + fn) if (tp + fn) else float("nan")
        out[key] = {"n_responses": n, "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Hand-coding sample and precision/recall scoring.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--wave", required=True)
    s.add_argument("--n", type=int, default=None)
    s.add_argument("--seed", type=int, default=1)
    c = sub.add_parser("score")
    c.add_argument("--wave", required=True)
    c.add_argument("--filled", required=True)
    args = ap.parse_args(argv)
    panel = load_panel()
    if args.cmd == "sample":
        n = args.n or int(panel["validation"]["sample_size"])
        rows = draw_sample(args.wave, n, args.seed)
        path = write_sample(args.wave, rows)
        print("Wrote %d responses to %s (requested %d). Fill human_* columns, then run `score`." % (len(rows), path, n))
        return 0
    res = score(args.filled, panel["resolution"]["name_similarity_threshold"])
    if not res:
        print("No coded rows found (human_recommended_names all blank).")
        return 1
    for key in sorted(res, key=lambda k: (k != "ALL", k)):
        r = res[key]
        print("%-10s n=%3d  precision=%.3f  recall=%.3f  (tp=%d fp=%d fn=%d)" % (key, r["n_responses"], r["precision"], r["recall"], r["tp"], r["fp"], r["fn"]))
    min_recall = float(panel["validation"]["min_recall"])
    if res["ALL"]["recall"] < min_recall:
        print("Recall %.3f < %.2f: per protocol 4.3, fix the extractor and re-run `panel.extract` for this wave." % (res["ALL"]["recall"], min_recall))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
