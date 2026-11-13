"""Public data package for a wave (protocol section 7).

Writes releases/<wave>/ with:
  responses.csv   one row per AI answer: prompt, engine, returned model, answer text,
                  cited URLs. Google AI Overview answer text and citations are SERP
                  content and are NOT included (metadata only).
  mentions.csv    one row per extracted business mention: name and facts as stated by
                  the AI, resolution status, Google place_id, derived flags. Google
                  Places fields (name, address, phone, website, rating, review count,
                  types) are NOT included; re-fetch them from place_id.
  manual_checks.csv  manual verification labels with evidence URLs (protocol 4.6).
  ghost_estimate.csv, metrics_summary.csv, source_mix.csv, source_domains.csv
  README.md       field descriptions and terms.
Local-pack (SERP) results are not released; only the derived pack-overlap metrics are.
"""
import argparse
import json
import shutil
import sys
from typing import List, Optional

import pandas as pd

from panel.paths import DATA_DIR
from panel.storage import read_jsonl

ROOT = DATA_DIR.parent
RESPONSE_COLS = ["wave", "key", "timestamp_utc", "engine", "requested_model", "returned_model", "niche", "template_id",
                 "prompt", "city", "state", "city_key", "tier", "repeat", "latency_s", "error", "response_text", "citations"]
MENTION_COLS = ["wave", "engine", "niche", "template_id", "city", "state", "city_key", "tier", "repeat", "response_key",
                "name", "recommended", "rank_in_answer", "stated_phone", "stated_website", "stated_address",
                "extractor_model", "prompt_version", "resolution_status", "match_similarity", "place_id", "business_key",
                "is_closed", "fact_error_website", "fact_error_address", "fact_error", "fact_error_phone", "phone_check",
                "unresolved_reason", "ghost_manual_check", "is_ghost"]
SERP_ENGINES = {"google_aio"}

README = """# Open Local AI Panel — public data, wave {wave}

Protocol: https://doi.org/10.5281/zenodo.24204767 (PROTOCOL.md). Code: https://github.com/kateryna-cherkas/open-local-ai-panel
Licence: CC BY 4.0 for the files in this folder.

## Files
- `responses.csv` — one row per AI answer. `citations` is a JSON list of URLs returned by the API.
  For `engine = google_aio` (Google AI Overview, collected via DataForSEO) only metadata is released;
  the answer text and citations are Google search-results content and are left empty.
- `mentions.csv` — one row per business named in an answer, as extracted (`recommended` = 1 if the answer
  recommends it). `stated_*` are the facts as written by the AI. `place_id` is the Google Places ID the
  business was resolved to (protocol 4.4). Google Places fields are not redistributed (Google Maps Platform
  terms); re-fetch them with the Places API using `place_id`.
- `manual_checks.csv` — manual verification of unresolved businesses (protocol 4.6) and of AI-stated phone
  numbers, with evidence URLs.
- `metrics_summary.csv`, `ghost_estimate.csv`, `source_mix.csv`, `source_domains.csv` — derived metrics.

Google local-pack results (SERP) are not released; only derived metrics (pack overlap) are.
Private individuals' names that are not part of a business name are not expected in this data; report any to the author.
"""


def export(wave: str) -> str:
    raw_dir = DATA_DIR / "raw" / wave
    proc = DATA_DIR / "processed" / wave
    out = ROOT / "releases" / wave
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    rows = []
    for f in sorted(raw_dir.glob("*.jsonl")):
        if f.stem == "local_pack":
            continue
        for rec in read_jsonl(f):
            row = {c: rec.get(c) for c in RESPONSE_COLS}
            row["citations"] = json.dumps(rec.get("citations") or [])
            if rec.get("engine") in SERP_ENGINES:
                row["response_text"] = ""
                row["citations"] = ""
            rows.append(row)
    pd.DataFrame(rows, columns=RESPONSE_COLS).to_csv(out / "responses.csv", index=False)

    res = pd.read_csv(proc / "resolved.csv", dtype=str, keep_default_na=False)
    for c in MENTION_COLS:
        if c not in res.columns:
            res[c] = ""
    res[MENTION_COLS].to_csv(out / "mentions.csv", index=False)

    manual = []
    mdir = DATA_DIR / "manual" / wave
    for name, kind in (("unresolved_verified.csv", "unresolved_business"), ("unresolved_sample.csv", "unresolved_business"),
                       ("phones_verified.csv", "phone_number")):
        p = mdir / name
        if p.exists():
            m = pd.read_csv(p, dtype=str, keep_default_na=False)
            m.insert(0, "check_type", kind)
            if kind == "phone_number":
                m = m.rename(columns={"place_name": "business_name", "source_url": "evidence_url"})
                m = m.drop(columns=[c for c in ("google_phone", "website") if c in m.columns])
            else:
                m = m.rename(columns={"name": "business_name"})
            manual.append(m)
    if manual:
        mc = pd.concat(manual, ignore_index=True, sort=False)
        # Labels were made on names unresolved at check time; resolution rules may since have
        # matched some of them (e.g. service-area businesses, protocol v1.0). Show the current status.
        status = res.groupby(["name", "city"])["resolution_status"].agg(lambda s: "resolved" if (s == "resolved").any() else s.iloc[0])
        mc["resolution_status_now"] = [status.get((n, c), "") if t == "unresolved_business" else ""
                                       for t, n, c in zip(mc["check_type"], mc["business_name"], mc["city"])]
        mc.to_csv(out / "manual_checks.csv", index=False)

    for name in ("metrics_summary.csv", "ghost_estimate.csv", "source_mix.csv", "source_domains.csv"):
        if (proc / name).exists():
            shutil.copy(proc / name, out / name)
    (out / "README.md").write_text(README.format(wave=wave), encoding="utf-8")
    return str(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wave", required=True)
    args = ap.parse_args(argv)
    print("export ->", export(args.wave))
    return 0


if __name__ == "__main__":
    sys.exit(main())
