"""End-to-end mock run: collect -> extract -> (validate sample) -> resolve -> metrics -> report.
Uses a dedicated wave id under data/ and removes it afterwards."""
import csv
import shutil
import unittest

import pandas as pd

from panel import collect, extract, metrics, report, resolve, validate
from panel.extract import mock_rule_extract, parse_extractor_json, validate_against_schema
from panel.paths import processed_wave_dir, raw_wave_dir, CACHE_DIR
from panel.storage import read_jsonl
from panel.validate import match_sets

WAVE = "test-e2e"


class PipelineE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for d in (raw_wave_dir(WAVE), processed_wave_dir(WAVE)):
            shutil.rmtree(d, ignore_errors=True)
        rc = collect.main(["--wave", WAVE, "--mock", "--cities", "Houston", "Bend", "--limit", "20"])
        assert rc == 0
        # resumability: a second run must skip everything
        rc = collect.main(["--wave", WAVE, "--mock", "--cities", "Houston", "Bend", "--limit", "20"])
        assert rc == 0
        cls.extract_stats = extract.run(WAVE, ["openai", "gemini", "perplexity", "claude", "google_aio"], mock=True, limit=None)
        cls.resolve_stats = resolve.run(WAVE, mock=True, limit=None)
        cls.metrics_info = metrics.run(WAVE)
        cls.report_text = report.build(WAVE)

    @classmethod
    def tearDownClass(cls):
        for d in (raw_wave_dir(WAVE), processed_wave_dir(WAVE)):
            shutil.rmtree(d, ignore_errors=True)

    def test_raw_records_have_protocol_4_1_fields(self):
        recs = list(read_jsonl(raw_wave_dir(WAVE) / "claude.jsonl"))
        self.assertEqual(len(recs), 20, "second run must not duplicate records")
        required = {"wave", "timestamp_utc", "engine", "requested_model", "returned_model", "template_id", "city",
                    "repeat", "response_text", "citations", "latency_s", "error", "raw"}
        self.assertTrue(required.issubset(recs[0].keys()), required - set(recs[0].keys()))
        self.assertTrue(recs[0]["timestamp_utc"].endswith("Z"))
        packs = list(read_jsonl(raw_wave_dir(WAVE) / "local_pack.jsonl"))
        self.assertTrue(packs and all(p["entries"] for p in packs))

    def test_extract_and_mentions(self):
        self.assertGreater(self.extract_stats["extracted"], 0)
        self.assertEqual(self.extract_stats["errors"], 0)
        with open(processed_wave_dir(WAVE) / "mentions.csv", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue(rows)
        self.assertIn("rank_in_answer", rows[0])
        flagged = [r for r in rows if r["recommended"] == "0"]
        self.assertTrue(flagged, "platform mentions must be flagged, not counted as recommendations")

    def test_resolve_classification(self):
        self.assertGreater(self.resolve_stats["resolved"], 0)
        self.assertGreater(self.resolve_stats["unresolved"], 0, "mock ghost should be unresolved")
        df = pd.read_csv(processed_wave_dir(WAVE) / "resolved.csv", dtype=str, keep_default_na=False)
        rec = df[df["recommended"] == "1"]
        self.assertTrue((rec["is_closed"] == "1").any(), "mock CLOSED_PERMANENTLY should appear")
        self.assertTrue((rec["fact_error_phone"] == "1").any(), "mock wrong phone should be a fact error")
        self.assertTrue((rec[rec["resolution_status"] == "resolved"]["match_similarity"].astype(float) >= 0.85).all())
        self.assertTrue(any(CACHE_DIR.joinpath("places").glob("*.json")), "places lookups must be cached")

    def test_metrics_outputs(self):
        self.assertGreater(self.metrics_info["cells"], 0)
        cells = pd.read_csv(processed_wave_dir(WAVE) / "metrics_cells.csv")
        for col in ["distinct_count", "top3_share", "within_wave_stability", "pack_overlap", "ghost_rate", "closed_rate", "fact_error_rate"]:
            self.assertIn(col, cells.columns)
        self.assertTrue(((cells["top3_share"].dropna() >= 0) & (cells["top3_share"].dropna() <= 1)).all())
        summary = pd.read_csv(processed_wave_dir(WAVE) / "metrics_summary.csv")
        self.assertTrue((summary["ci_low"].dropna() <= summary["mean"].dropna().loc[summary["ci_low"].dropna().index] + 1e-9).all())
        rr = pd.read_csv(processed_wave_dir(WAVE) / "metrics_rr.csv")
        self.assertTrue(((rr["rr"] > 0) & (rr["rr"] <= 1)).all())
        mix = pd.read_csv(processed_wave_dir(WAVE) / "source_mix.csv")
        self.assertIn("review_platform", set(mix["category"]))

    def test_persistence_against_self(self):
        pers = metrics.persistence(WAVE, WAVE, 0.4)
        self.assertTrue((pers["persistence"] == 1.0).all())

    def test_validation_sample_and_score(self):
        rows = validate.draw_sample(WAVE, 10, seed=3)
        self.assertEqual(len(rows), 10)
        path = validate.write_sample(WAVE, rows)
        # Simulate a perfect coder: copy extractor names into the human column
        with open(path, newline="") as fh:
            filled = list(csv.DictReader(fh))
        for r in filled:
            r["human_recommended_names"] = r["extractor_recommended_names"]
            r["coder_notes"] = "ok"
        filled_path = processed_wave_dir(WAVE) / "validation_filled.csv"
        with open(filled_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=validate.SAMPLE_COLUMNS)
            w.writeheader()
            w.writerows(filled)
        res = validate.score(str(filled_path))
        self.assertEqual(res["ALL"]["precision"], 1.0)
        self.assertEqual(res["ALL"]["recall"], 1.0)

    def test_report_is_labelled_draft(self):
        self.assertIn("DRAFT", self.report_text)
        self.assertIn("## 2. Metrics by engine", self.report_text)
        self.assertIn("| claude |", self.report_text)


class ExtractorUnitTests(unittest.TestCase):
    def test_parse_extractor_json_tolerates_fences(self):
        obj = parse_extractor_json("```json\n{\"businesses\": []}\n```")
        self.assertEqual(obj, {"businesses": []})

    def test_schema_validation_cleans_rows(self):
        from panel.extract import load_schema
        schema = load_schema("v1")
        rows = validate_against_schema({"businesses": [
            {"name": " ACME Plumbing ", "recommended": True, "rank_in_answer": 1, "phone": "", "website": None, "address": "null"},
            {"name": "", "recommended": True}]}, schema)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "ACME Plumbing")
        self.assertIsNone(rows[0]["phone"])
        self.assertIsNone(rows[0]["address"])

    def test_mock_rule_extract(self):
        text = "1. **Summit Roofing LLC** - Phone: (713) 555-1000. - Website: https://x.example.com\nCheck Yelp too."
        out = mock_rule_extract(text)
        self.assertEqual(out[0]["name"], "Summit Roofing LLC")
        self.assertEqual(out[0]["phone"], "(713) 555-1000")
        self.assertFalse([o for o in out if o["name"] == "Yelp"][0]["recommended"])

    def test_match_sets_fuzzy(self):
        tp, fp, fn = match_sets(["Summit Roofing, LLC", "Bogus Co"], ["Summit Roofing", "Blue Sky HVAC"], 0.85)
        self.assertEqual((tp, fp, fn), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
