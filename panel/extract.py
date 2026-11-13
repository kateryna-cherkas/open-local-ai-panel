"""LLM entity extraction (protocol 4.2).

    python -m panel.extract --wave 2026-11 [--engines ...] [--mock] [--limit N]

Reads data/raw/{wave}/{engine}.jsonl, calls the extractor model with the
versioned prompt in extract/prompt_v{N}.txt and schema extract/schema_v{N}.json,
writes data/processed/{wave}/mentions.csv (one row per business named) and
data/processed/{wave}/extractions.jsonl (one row per response, with the raw
extractor output, for auditing). Resumable by response key.

The extractor is Claude (Messages API, raw HTTP) with a cheap model by default
(config `extractor.model`). In --mock mode the mock answers are parsed with a
deterministic rule so the whole pipeline runs offline.
"""
import argparse
import csv
import json
import re
import sys
from typing import Any, Dict, List, Optional

from panel.config import ENGINE_IDS, load_panel
from panel.engines.google_aio import NO_AIO_MARKER
from panel.env import require_secret
from panel.http import post_json
from panel.paths import EXTRACT_DIR, FIXTURES_DIR, processed_wave_dir, raw_wave_dir
from panel.storage import append_jsonl, completed_keys, load_json, read_jsonl

MENTION_COLUMNS = [
    "wave", "engine", "niche", "template_id", "city", "state", "city_key", "tier", "repeat", "response_key",
    "name", "recommended", "rank_in_answer", "stated_phone", "stated_website", "stated_address",
    "extractor_model", "extractor_returned_model", "prompt_version",
]

NON_BUSINESS_TERMS = {"yelp", "angi", "angie's list", "homeadvisor", "thumbtack", "google", "bbb", "better business bureau",
                      "nextdoor", "reddit", "houzz", "porch", "networx"}


def load_prompt(version: str) -> str:
    return (EXTRACT_DIR / ("prompt_%s.txt" % version)).read_text(encoding="utf-8")


def load_schema(version: str) -> Dict[str, Any]:
    return load_json(EXTRACT_DIR / ("schema_%s.json" % version))


def render_prompt(template: str, schema: Dict[str, Any], response_text: str) -> str:
    return template.replace("{SCHEMA}", json.dumps(schema, indent=2)).replace("{RESPONSE_TEXT}", response_text)


def parse_extractor_json(text: str) -> Dict[str, Any]:
    """Tolerant JSON parse: strips markdown fences and leading prose."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in extractor output")
    return json.loads(s[start:end + 1])


def validate_against_schema(obj: Dict[str, Any], schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Light-weight validation of the v1 schema (no jsonschema dependency).
    Returns the cleaned business list; raises ValueError on structural errors."""
    if not isinstance(obj, dict) or "businesses" not in obj or not isinstance(obj["businesses"], list):
        raise ValueError("missing 'businesses' list")
    props = schema["properties"]["businesses"]["items"]["properties"]
    cleaned = []
    for b in obj["businesses"]:
        if not isinstance(b, dict) or not str(b.get("name", "")).strip():
            continue
        row = {}
        for k in props:
            row[k] = b.get(k)
        row["name"] = str(row["name"]).strip()
        row["recommended"] = bool(row.get("recommended"))
        rank = row.get("rank_in_answer")
        row["rank_in_answer"] = int(rank) if isinstance(rank, (int, float)) and rank >= 1 else None
        for k in ("phone", "website", "address"):
            v = row.get(k)
            row[k] = str(v).strip() if v not in (None, "", "null") else None
        cleaned.append(row)
    return cleaned


class ClaudeExtractor:
    """Messages API call, raw HTTP (doc: platform.claude.com Messages API)."""

    def __init__(self, cfg: Dict[str, Any], retry_cfg: Optional[Dict[str, Any]], mock: bool):
        self.cfg = cfg
        self.retry_cfg = retry_cfg or {}
        self.mock = mock
        self.version = cfg.get("prompt_version", "v1")
        self.template = load_prompt(self.version)
        self.schema = load_schema(self.version)

    def build_request(self, response_text: str):
        payload = {
            "model": self.cfg["model"],
            "max_tokens": int(self.cfg.get("max_tokens", 2048)),
            "messages": [{"role": "user", "content": render_prompt(self.template, self.schema, response_text)}],
        }
        headers = {"x-api-key": require_secret("ANTHROPIC_API_KEY"),
                   "anthropic-version": self.cfg.get("anthropic_version", "2023-06-01")}
        return self.cfg["endpoint"], payload, headers

    def extract(self, response_text: str) -> Dict[str, Any]:
        """Return {"businesses": [...], "returned_model": str, "raw": ...}."""
        if self.mock:
            raw = self.mock_raw(response_text)
        else:
            url, payload, headers = self.build_request(response_text)
            raw, _ = post_json(url, payload, headers=headers, retry=self.retry_cfg,
                               rate_key="extractor", rpm=self.cfg.get("requests_per_minute"))
        text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
        businesses = validate_against_schema(parse_extractor_json(text), self.schema)
        return {"businesses": businesses, "returned_model": raw.get("model"), "raw": raw}

    def mock_raw(self, response_text: str) -> Dict[str, Any]:
        fx = load_json(FIXTURES_DIR / "extractor_response.json")
        fx["model"] = self.cfg["model"]
        fx["content"] = [{"type": "text", "text": json.dumps({"businesses": mock_rule_extract(response_text)})}]
        return fx


_LINE_RE = re.compile(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*(.*)$")
_PHONE_RE = re.compile(r"Phone:\s*(\+?1?\s*\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4})")
_SITE_RE = re.compile(r"Website:\s*(\S+)")
_AIO_RE = re.compile(r"include (.+?)\.$", re.S)


def mock_rule_extract(text: str) -> List[Dict[str, Any]]:
    """Deterministic stand-in for the LLM extractor, aligned with mockdata.render_answer.
    Also handles the mock AI Overview sentence and flags platform mentions."""
    out = []  # type: List[Dict[str, Any]]
    rank = 0
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m:
            rank += 1
            rest = m.group(3)
            phone = _PHONE_RE.search(rest)
            site = _SITE_RE.search(rest)
            out.append({"name": m.group(2).strip(), "recommended": True, "rank_in_answer": rank,
                        "phone": phone.group(1).strip() if phone else None,
                        "website": site.group(1).strip() if site else None, "address": None})
    if not out:
        m = _AIO_RE.search(text)
        if m:
            for i, name in enumerate(part.strip() for part in m.group(1).split(";")):
                if name:
                    out.append({"name": name, "recommended": True, "rank_in_answer": i + 1,
                                "phone": None, "website": None, "address": None})
    lowered = text.lower()
    for term in sorted(NON_BUSINESS_TERMS):
        if re.search(r"\b%s\b" % re.escape(term), lowered):
            out.append({"name": term.title() if term != "bbb" else "BBB", "recommended": False,
                        "rank_in_answer": None, "phone": None, "website": None, "address": None})
    return out


def run(wave: str, engine_ids: List[str], mock: bool, limit: Optional[int]) -> Dict[str, int]:
    panel = load_panel()
    extractor = ClaudeExtractor(panel["extractor"], panel.get("retry"), mock)
    out_dir = processed_wave_dir(wave)
    out_dir.mkdir(parents=True, exist_ok=True)
    extractions_path = out_dir / "extractions.jsonl"
    done = completed_keys(extractions_path, key_field="response_key")
    stats = {"responses": 0, "extracted": 0, "skipped": 0, "errors": 0, "empty": 0}
    n_calls = 0
    for engine_id in engine_ids:
        for rec in read_jsonl(raw_wave_dir(wave) / ("%s.jsonl" % engine_id)):
            stats["responses"] += 1
            if rec.get("error") or not rec.get("response_text") or rec["response_text"] == NO_AIO_MARKER:
                stats["empty"] += 1
                continue
            if rec["key"] in done:
                stats["skipped"] += 1
                continue
            if limit is not None and n_calls >= limit:
                break
            n_calls += 1
            row = {"response_key": rec["key"], "wave": wave, "engine": engine_id, "niche": rec["niche"],
                   "template_id": rec["template_id"], "city": rec["city"], "state": rec["state"],
                   "city_key": rec["city_key"], "tier": rec["tier"], "repeat": rec["repeat"],
                   "extractor_model": panel["extractor"]["model"], "prompt_version": extractor.version}
            try:
                res = extractor.extract(rec["response_text"])
                row.update({"businesses": res["businesses"], "extractor_returned_model": res["returned_model"],
                            "raw": res["raw"], "error": None})
                stats["extracted"] += 1
            except Exception as exc:  # noqa: BLE001
                row.update({"businesses": [], "extractor_returned_model": None, "raw": None,
                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:500])})
                stats["errors"] += 1
            append_jsonl(extractions_path, row)
    write_mentions_csv(wave)
    return stats


def write_mentions_csv(wave: str) -> int:
    out_dir = processed_wave_dir(wave)
    rows = []
    for ex in read_jsonl(out_dir / "extractions.jsonl"):
        if ex.get("error"):
            continue
        for b in ex["businesses"]:
            rows.append({
                "wave": wave, "engine": ex["engine"], "niche": ex["niche"], "template_id": ex["template_id"],
                "city": ex["city"], "state": ex["state"], "city_key": ex["city_key"], "tier": ex["tier"],
                "repeat": ex["repeat"], "response_key": ex["response_key"],
                "name": b["name"], "recommended": int(bool(b["recommended"])),
                "rank_in_answer": b["rank_in_answer"] if b["rank_in_answer"] is not None else "",
                "stated_phone": b.get("phone") or "", "stated_website": b.get("website") or "",
                "stated_address": b.get("address") or "",
                "extractor_model": ex["extractor_model"], "extractor_returned_model": ex.get("extractor_returned_model") or "",
                "prompt_version": ex["prompt_version"],
            })
    path = out_dir / "mentions.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MENTION_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LLM entity extraction for a wave.")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--engines", nargs="*", default=None, choices=ENGINE_IDS)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="Max extractor calls this run")
    args = ap.parse_args(argv)
    stats = run(args.wave, args.engines or ENGINE_IDS, args.mock, args.limit)
    print("extract: %s -> %s" % (stats, processed_wave_dir(args.wave) / "mentions.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
