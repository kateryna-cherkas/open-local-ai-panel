import unittest

from panel.config import ENGINE_IDS, TEMPLATE_IDS, iter_prompts, load_cities, load_panel, prompt_text


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.panel = load_panel()
        self.cities = load_cities()

    def test_protocol_constants(self):
        self.assertEqual(self.panel["k_repeats"], 5)
        self.assertEqual(self.panel["niches"], ["construction", "appliance_repair", "roofing", "cleaning"])
        self.assertEqual(len(list(iter_prompts(self.panel))), 12)
        self.assertEqual(len(ENGINE_IDS), 5)
        self.assertEqual(self.panel["resolution"]["name_similarity_threshold"], 0.85)
        self.assertEqual(self.panel["validation"]["sample_size"], 100)
        self.assertEqual(self.panel["validation"]["min_recall"], 0.9)
        self.assertEqual(self.panel["metrics"]["persistence_rr_threshold"], 0.4)

    def test_prompts_verbatim_from_protocol_3_1(self):
        houston = [c for c in self.cities if c.city == "Houston"][0]
        expected = {
            ("roofing", "P1"): "What are the best roofing companies in Houston, TX?",
            ("roofing", "P2"): "I need a roof replacement in Houston, TX. Which companies should I call?",
            ("roofing", "P3"): "My roof is leaking after a storm in Houston, TX. Who can I call to fix it fast?",
            ("construction", "P1"): "What are the best general contractors in Houston, TX?",
            ("construction", "P2"): "I want to remodel my kitchen in Houston, TX. Which contractors should I call?",
            ("construction", "P3"): "I need a contractor to repair water damage in my house in Houston, TX. Who can start soon?",
            ("appliance_repair", "P1"): "What are the best appliance repair companies in Houston, TX?",
            ("appliance_repair", "P2"): "My dishwasher is not draining in Houston, TX. Which appliance repair companies should I call?",
            ("appliance_repair", "P3"): "My refrigerator stopped cooling in Houston, TX. Who can come out today?",
            ("cleaning", "P1"): "What are the best house cleaning services in Houston, TX?",
            ("cleaning", "P2"): "I need a deep cleaning for my house in Houston, TX. Which companies should I call?",
            ("cleaning", "P3"): "I'm moving out this week in Houston, TX. Who can do a move-out cleaning on short notice?",
        }
        for (niche, tid), text in expected.items():
            self.assertEqual(prompt_text(self.panel, niche, tid, houston), text)
        self.assertEqual(TEMPLATE_IDS, ["P1", "P2", "P3"])

    def test_cities_from_protocol_3_2(self):
        self.assertEqual(len(self.cities), 26)
        tiers = {}
        for c in self.cities:
            tiers.setdefault(c.tier, []).append(c.city)
        self.assertEqual(len(tiers["large"]), 10)
        self.assertEqual(len(tiers["mid"]), 8)
        self.assertEqual(len(tiers["small"]), 8)
        self.assertIn("Bend", tiers["small"])
        self.assertIn("Greenville", tiers["small"])
        for c in self.cities:
            # Tier thresholds, protocol 3.2: large >= 500k, mid 150k-500k, small < 150k.
            p = c.population
            self.assertEqual(c.tier, "large" if p >= 500000 else "mid" if p >= 150000 else "small", c.city)
        for c in self.cities:
            self.assertTrue(-125 < c.longitude < -65 and 24 < c.latitude < 50, c)
            self.assertTrue(c.dataforseo_location_name.endswith(",United States"))
            self.assertEqual(len(c.key.split("_")[-1]), 2)
        self.assertEqual(len({c.key for c in self.cities}), 26)
        from panel.areacodes import AREA_CODES
        self.assertEqual({c.key for c in self.cities}, set(AREA_CODES))


if __name__ == "__main__":
    unittest.main()
