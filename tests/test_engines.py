"""Engine adapters: request shapes match the doc-verified API and parsers
handle the fixture response shapes."""
import json
import unittest
from unittest import mock

from panel.config import load_cities, load_panel, prompt_text
from panel.engines import ENGINE_CLASSES, make_engine
from panel.engines.base import CallContext
from panel.engines.google_aio import NO_AIO_MARKER
from panel.engines.places import PlacesClient
from panel.paths import FIXTURES_DIR


def ctx_for(engine_id, panel, city, repeat=0):
    return CallContext(engine_id, "2026-11", "roofing", "P1", prompt_text(panel, "roofing", "P1", city), city,
                       repeat, panel["engines"][engine_id]["model"])


class EngineRequestShapeTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel()
        self.city = [c for c in load_cities() if c.city == "Houston"][0]
        self.secret_patch = mock.patch("panel.env.get_secret", return_value="test-not-a-real-key")
        self.secret_patch.start()

    def tearDown(self):
        self.secret_patch.stop()

    def test_openai_responses_web_search(self):
        url, payload, headers, auth = make_engine("openai", self.panel).build_request(ctx_for("openai", self.panel, self.city))
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(payload["input"], "What are the best roofing companies in Houston, TX?")
        tool = payload["tools"][0]
        self.assertEqual(tool["type"], "web_search")
        self.assertEqual(tool["user_location"], {"type": "approximate", "country": "US", "city": "Houston",
                                                 "region": "Texas", "timezone": "America/Chicago"})
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        self.assertNotIn("system", json.dumps(payload).lower().replace("search_context_size", ""))

    def test_gemini_generate_content_google_search(self):
        url, payload, headers, _ = make_engine("gemini", self.panel).build_request(ctx_for("gemini", self.panel, self.city))
        self.assertTrue(url.endswith(":generateContent"))
        self.assertIn("/v1beta/models/gemini-", url)
        self.assertEqual(payload["tools"], [{"google_search": {}}])
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "What are the best roofing companies in Houston, TX?")
        self.assertIn("x-goog-api-key", headers)
        self.assertNotIn("systemInstruction", payload)

    def test_perplexity_chat_completions_user_location(self):
        panel = json.loads(json.dumps(self.panel))
        panel["engines"]["perplexity"]["mode"] = "chat_completions"
        url, payload, headers, _ = make_engine("perplexity", panel).build_request(ctx_for("perplexity", panel, self.city))
        self.assertEqual(url, "https://api.perplexity.ai/chat/completions")
        self.assertEqual(payload["model"], "sonar")
        loc = payload["web_search_options"]["user_location"]
        self.assertEqual(loc["country"], "US")
        self.assertAlmostEqual(loc["latitude"], 29.7604)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "What are the best roofing companies in Houston, TX?"}])

    def test_perplexity_agent_mode(self):
        panel = json.loads(json.dumps(self.panel))
        panel["engines"]["perplexity"]["mode"] = "agent"
        eng = make_engine("perplexity", panel)
        url, payload, _, _ = eng.build_request(ctx_for("perplexity", panel, self.city))
        self.assertEqual(url, "https://api.perplexity.ai/v1/agent")
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        self.assertIn("latitude", payload["tools"][0]["user_location"])
        eng.mock = True
        res = eng.call(ctx_for("perplexity", panel, self.city))
        self.assertIsNone(res.error)
        self.assertIn("mock", res.text.lower())
        self.assertTrue(res.citations)

    def test_claude_messages_web_search_tool(self):
        url, payload, headers, _ = make_engine("claude", self.panel).build_request(ctx_for("claude", self.panel, self.city))
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertIn("x-api-key", headers)
        tool = payload["tools"][0]
        self.assertTrue(tool["type"].startswith("web_search_2026") or tool["type"] == "web_search_20250305")
        self.assertEqual(tool["name"], "web_search")
        self.assertEqual(tool["user_location"]["type"], "approximate")
        self.assertEqual(tool["user_location"]["region"], "Texas")
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertNotIn("system", payload)

    def test_dataforseo_serp_task(self):
        url, payload, headers, auth = make_engine("google_aio", self.panel).build_request(ctx_for("google_aio", self.panel, self.city))
        self.assertEqual(url, "https://api.dataforseo.com/v3/serp/google/organic/live/advanced")
        self.assertIsInstance(payload, list)
        task = payload[0]
        self.assertEqual(task["location_name"], "Houston,Texas,United States")
        self.assertEqual(task["keyword"], "What are the best roofing companies in Houston, TX?")
        self.assertTrue(task["load_async_ai_overview"])
        self.assertEqual(len(auth), 2)

    def test_places_search_text(self):
        client = PlacesClient(self.panel["places"])
        url, payload, headers = client.build_request("Summit Roofing", self.city)
        self.assertEqual(url, "https://places.googleapis.com/v1/places:searchText")
        self.assertIn("X-Goog-FieldMask", headers)
        self.assertIn("places.businessStatus", headers["X-Goog-FieldMask"])
        self.assertEqual(payload["textQuery"], "Summit Roofing Houston, TX")
        self.assertLessEqual(payload["locationBias"]["circle"]["radius"], 50000)


class EngineParserTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel()
        self.city = [c for c in load_cities() if c.city == "Bend"][0]

    def test_all_engines_mock_roundtrip(self):
        for engine_id in ENGINE_CLASSES:
            eng = make_engine(engine_id, self.panel, mock=True)
            res = eng.call(ctx_for(engine_id, self.panel, self.city, repeat=1))
            self.assertIsNone(res.error, engine_id)
            self.assertTrue(res.text, engine_id)
            self.assertTrue(res.citations, engine_id)
            # Perplexity agent mode reports models as "provider/model".
            self.assertTrue(res.returned_model.endswith(self.panel["engines"][engine_id]["model"]), engine_id)
            self.assertIsNotNone(res.raw)
            self.assertIsNotNone(res.latency_s)

    def test_mock_is_deterministic(self):
        a = make_engine("openai", self.panel, mock=True).call(ctx_for("openai", self.panel, self.city, 2)).text
        b = make_engine("openai", self.panel, mock=True).call(ctx_for("openai", self.panel, self.city, 2)).text
        self.assertEqual(a, b)

    def test_parsers_on_raw_fixtures(self):
        for engine_id, fixture in [("openai", "openai_response.json"), ("gemini", "gemini_response.json"),
                                   ("perplexity", "perplexity_response.json"), ("claude", "claude_response.json")]:
            with open(FIXTURES_DIR / fixture) as fh:
                raw = json.load(fh)
            parsed = make_engine(engine_id, self.panel).parse_response(raw)
            self.assertIn("https://example.com", parsed.citations, engine_id)
            self.assertTrue(parsed.returned_model, engine_id)

    def test_claude_search_error_block(self):
        eng = make_engine("claude", self.panel)
        raw = {"model": "m", "content": [
            {"type": "web_search_tool_result", "tool_use_id": "x", "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
            {"type": "text", "text": "Sorry."}]}
        parsed = eng.parse_response(raw)
        self.assertEqual(parsed.extra["search_errors"], ["max_uses_exceeded"])
        self.assertEqual(parsed.text, "Sorry.")

    def test_aio_missing_marker_and_local_pack(self):
        eng = make_engine("google_aio", self.panel, mock=True)
        with open(FIXTURES_DIR / "dataforseo_response.json") as fh:
            raw = json.load(fh)
        parsed = eng.parse_response(raw)  # fixture has no items -> no AI Overview
        self.assertEqual(parsed.text, NO_AIO_MARKER)
        self.assertFalse(parsed.extra["ai_overview_present"])
        lp = eng.parse_local_pack(eng.mock_local_pack_raw(ctx_for("google_aio", self.panel, self.city)))
        self.assertEqual(len(lp), 3)
        self.assertTrue(all(e["title"] and e["rank_absolute"] for e in lp))

    def test_engine_call_records_error_instead_of_raising(self):
        eng = make_engine("openai", self.panel, mock=True)
        with mock.patch.object(eng, "mock_raw", side_effect=RuntimeError("boom")):
            res = eng.call(ctx_for("openai", self.panel, self.city))
        self.assertTrue(res.error.startswith("RuntimeError"))
        self.assertEqual(res.text, "")


if __name__ == "__main__":
    unittest.main()


class GeminiRedirectTests(unittest.TestCase):
    def test_redirect_uris_mapped_to_title_domain(self):
        panel = load_panel()
        eng = make_engine("gemini", panel)
        raw = {"modelVersion": "gemini-x", "candidates": [{"content": {"parts": [{"text": "hi"}]},
               "groundingMetadata": {"groundingChunks": [
                   {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA", "title": "Reddit.com"}},
                   {"web": {"uri": "https://example.org/page", "title": "Example page"}}]}}]}
        res = eng.parse_response(raw)
        self.assertEqual(res.citations, ["https://reddit.com/", "https://example.org/page"])
        self.assertEqual(len(res.extra["grounding_redirects"]), 2)
