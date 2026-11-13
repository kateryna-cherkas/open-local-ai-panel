"""Collect one wave of AI responses and local packs (protocol 3.3-3.5, 4.1).

    python -m panel.collect --wave 2026-11 [--engines openai claude] [--cities Houston Bend]
                            [--limit N] [--mock] [--yes] [--skip-local-pack]

Output: data/raw/{wave}/{engine}.jsonl (one record per call) and
        data/raw/{wave}/local_pack.jsonl (one record per prompt x city).
Resumable: keys already present without an error are skipped.
"""
import argparse
import datetime as dt
import sys
import time
from typing import Any, Dict, List, Optional

from panel.config import ENGINE_IDS, City, iter_prompts, load_cities, load_panel, prompt_text, select_cities
from panel.engines import make_engine
from panel.engines.base import CallContext
from panel.engines.google_aio import GoogleAIOEngine
from panel.http import post_json
from panel.paths import raw_wave_dir
from panel.storage import append_jsonl, completed_keys


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_jobs(panel: Dict[str, Any], cities: List[City], engine_id: str, wave: str, repeats: int) -> List[CallContext]:
    model = panel["engines"][engine_id]["model"]
    jobs = []
    for niche, template_id in iter_prompts(panel):
        for city in cities:
            for r in range(repeats):
                jobs.append(CallContext(engine_id, wave, niche, template_id,
                                        prompt_text(panel, niche, template_id, city), city, r, model))
    return jobs


def local_pack_jobs(panel: Dict[str, Any], cities: List[City], wave: str) -> List[CallContext]:
    return [CallContext("local_pack", wave, niche, template_id, prompt_text(panel, niche, template_id, city), city, 0, "google-local-pack")
            for niche, template_id in iter_prompts(panel) for city in cities]


def record_from(ctx: CallContext, result) -> Dict[str, Any]:
    """Raw record with all fields from protocol section 4.1."""
    return {
        "key": ctx.key,
        "wave": ctx.wave,
        "timestamp_utc": utc_now(),
        "engine": ctx.engine,
        "requested_model": ctx.requested_model,
        "returned_model": result.returned_model,
        "niche": ctx.niche,
        "template_id": ctx.template_id,
        "prompt": ctx.prompt,
        "city": ctx.city.city,
        "state": ctx.city.state,
        "city_key": ctx.city.key,
        "tier": ctx.city.tier,
        "repeat": ctx.repeat,
        "response_text": result.text,
        "citations": result.citations,
        "latency_s": result.latency_s,
        "error": result.error,
        "extra": result.extra,
        "raw": result.raw,
    }


def estimate(panel: Dict[str, Any], engine_ids: List[str], n_cities: int, repeats: int, limit: Optional[int],
             with_local_pack: bool) -> List[Dict[str, Any]]:
    n_prompts = sum(1 for _ in iter_prompts(panel))
    rows = []
    for e in engine_ids:
        n = n_prompts * n_cities * repeats
        if limit is not None:
            n = min(n, limit)
        price = panel["engines"][e].get("price_per_call_usd")
        rows.append({"engine": e, "model": panel["engines"][e]["model"], "calls": n,
                     "cost_usd": (n * price) if price is not None else None})
    if with_local_pack:
        n = n_prompts * n_cities
        if limit is not None:
            n = min(n, limit)
        price = panel["local_pack"].get("price_per_call_usd")
        rows.append({"engine": "local_pack", "model": "dataforseo-serp", "calls": n,
                     "cost_usd": (n * price) if price is not None else None})
    return rows


def print_estimate(rows: List[Dict[str, Any]]) -> None:
    print("\nPlanned volume for this run (before resume-skips):")
    total_calls, total_cost, unknown = 0, 0.0, False
    for r in rows:
        cost = "$%.2f" % r["cost_usd"] if r["cost_usd"] is not None else "unknown (set price_per_call_usd in config/panel.json)"
        print("  %-11s %-22s %6d calls  %s" % (r["engine"], r["model"], r["calls"], cost))
        total_calls += r["calls"]
        if r["cost_usd"] is None:
            unknown = True
        else:
            total_cost += r["cost_usd"]
    print("  total: %d calls, %s%s\n" % (total_calls, "$%.2f" % total_cost, " + unknown" if unknown else ""))


def confirm(yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print("Non-interactive session and no --yes: aborting before any paid call.")
        return False
    ans = input("Proceed with these paid API calls? [y/N] ").strip().lower()
    return ans in ("y", "yes")


def run_engine(panel: Dict[str, Any], engine_id: str, cities: List[City], wave: str, limit: Optional[int], mock: bool) -> Dict[str, int]:
    out_path = raw_wave_dir(wave) / ("%s.jsonl" % engine_id)
    done = completed_keys(out_path)
    engine = make_engine(engine_id, panel, mock=mock)
    jobs = build_jobs(panel, cities, engine_id, wave, int(panel["k_repeats"]))
    if limit is not None:
        jobs = jobs[:limit]
    stats = {"planned": len(jobs), "skipped": 0, "ok": 0, "error": 0}
    for i, ctx in enumerate(jobs, 1):
        if ctx.key in done:
            stats["skipped"] += 1
            continue
        result = engine.call(ctx)
        append_jsonl(out_path, record_from(ctx, result))
        stats["ok" if result.error is None else "error"] += 1
        if i % 25 == 0 or i == len(jobs):
            print("  [%s] %d/%d  ok=%d err=%d skipped=%d" % (engine_id, i, len(jobs), stats["ok"], stats["error"], stats["skipped"]))
    return stats


def run_local_pack(panel: Dict[str, Any], cities: List[City], wave: str, limit: Optional[int], mock: bool) -> Dict[str, int]:
    out_path = raw_wave_dir(wave) / "local_pack.jsonl"
    done = completed_keys(out_path)
    cfg = dict(panel["engines"]["google_aio"])
    cfg.update(panel.get("local_pack", {}))
    engine = GoogleAIOEngine(cfg, retry_cfg=panel.get("retry"), mock=mock)
    jobs = local_pack_jobs(panel, cities, wave)
    if limit is not None:
        jobs = jobs[:limit]
    stats = {"planned": len(jobs), "skipped": 0, "ok": 0, "error": 0}
    for ctx in jobs:
        if ctx.key in done:
            stats["skipped"] += 1
            continue
        t0 = time.monotonic()
        error, raw, entries = None, None, []
        try:
            if mock:
                raw = engine.mock_local_pack_raw(ctx)
            else:
                url, payload, headers, auth = engine.build_local_pack_request(ctx)
                raw, _ = post_json(url, payload, headers=headers, auth=auth, retry=panel.get("retry"),
                                   rate_key="local_pack", rpm=cfg.get("requests_per_minute"))
            entries = engine.parse_local_pack(raw)
        except Exception as exc:  # noqa: BLE001
            error = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        append_jsonl(out_path, {
            "key": ctx.key, "wave": wave, "timestamp_utc": utc_now(), "engine": "local_pack",
            "niche": ctx.niche, "template_id": ctx.template_id, "prompt": ctx.prompt,
            "city": ctx.city.city, "state": ctx.city.state, "city_key": ctx.city.key, "tier": ctx.city.tier,
            "location_name": ctx.city.dataforseo_location_name, "entries": entries,
            "latency_s": round(time.monotonic() - t0, 3), "error": error, "raw": raw,
        })
        stats["ok" if error is None else "error"] += 1
    print("  [local_pack] planned=%d ok=%d err=%d skipped=%d" % (stats["planned"], stats["ok"], stats["error"], stats["skipped"]))
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Collect one wave of AI responses and local packs.")
    ap.add_argument("--wave", required=True, help="Wave id, e.g. 2026-11")
    ap.add_argument("--engines", nargs="*", default=None, choices=ENGINE_IDS, help="Subset of engines (default: all enabled)")
    ap.add_argument("--cities", nargs="*", default=None, help="Subset of cities by name (default: all 20)")
    ap.add_argument("--limit", type=int, default=None, help="Max calls per engine (for smoke tests)")
    ap.add_argument("--mock", action="store_true", help="Use fixture responses; no network")
    ap.add_argument("--yes", action="store_true", help="Skip the cost confirmation prompt")
    ap.add_argument("--skip-local-pack", action="store_true")
    ap.add_argument("--repeats", type=int, default=None, help="Override k (smoke tests only; waves must use the protocol k)")
    ap.add_argument("--niches", nargs="*", default=None, help="Subset of niches (pilot runs only; default: all)")
    args = ap.parse_args(argv)

    panel = load_panel()
    if args.niches:
        unknown = [n for n in args.niches if n not in panel["niches"]]
        if unknown:
            ap.error("unknown niche(s): %s" % ", ".join(unknown))
        panel["niches"] = [n for n in panel["niches"] if n in args.niches]
    if args.repeats is not None:
        if args.repeats < 1:
            ap.error("--repeats must be >= 1")
        panel["k_repeats"] = args.repeats
    cities = select_cities(load_cities(), args.cities)
    engine_ids = args.engines or [e for e in ENGINE_IDS if panel["engines"][e].get("enabled", True)]

    print_estimate(estimate(panel, engine_ids, len(cities), int(panel["k_repeats"]), args.limit, not args.skip_local_pack))
    if args.mock:
        print("--mock: no network calls will be made.")
    elif not confirm(args.yes):
        return 1

    for e in engine_ids:
        s = run_engine(panel, e, cities, args.wave, args.limit, args.mock)
        print("  [%s] done: %s" % (e, s))
    if not args.skip_local_pack:
        run_local_pack(panel, cities, args.wave, args.limit, args.mock)
    print("Raw data: %s" % raw_wave_dir(args.wave))
    return 0


if __name__ == "__main__":
    sys.exit(main())
