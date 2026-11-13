import unittest

from panel.geo import haversine_km
from panel.names import digits_only, extract_domain, normalize_name, same_domain, token_set_ratio


class NameTests(unittest.TestCase):
    def test_legal_suffix_removal(self):
        self.assertEqual(normalize_name("Summit Roofing, LLC"), "summit roofing")
        self.assertEqual(normalize_name("The Summit Roofing Co."), "summit roofing")
        self.assertEqual(normalize_name("ACME Plumbing & Drain Inc"), "acme plumbing and drain")

    def test_token_set_ratio(self):
        self.assertEqual(token_set_ratio("Summit Roofing LLC", "Summit Roofing"), 1.0)
        self.assertGreaterEqual(token_set_ratio("Blue Sky HVAC", "Blue Sky HVAC Services"), 0.85)
        self.assertLess(token_set_ratio("Blue Sky HVAC", "Granite Plumbing"), 0.5)
        self.assertEqual(token_set_ratio("", ""), 1.0)
        self.assertEqual(token_set_ratio("x", ""), 0.0)

    def test_domains_and_phones(self):
        self.assertEqual(extract_domain("https://www.Example.com/path?q=1"), "example.com")
        self.assertTrue(same_domain("https://www.example.com/a", "example.com"))
        self.assertTrue(same_domain("https://shop.example.com", "https://example.com"))
        self.assertFalse(same_domain("https://example.com", "https://example.org"))
        self.assertEqual(digits_only("+1 (713) 555-1000"), "7135551000")

    def test_haversine(self):
        # Houston -> Dallas is roughly 362 km
        d = haversine_km(29.7604, -95.3698, 32.7767, -96.7970)
        self.assertTrue(350 < d < 375, d)
        self.assertAlmostEqual(haversine_km(0, 0, 0, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
