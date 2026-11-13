import unittest

import pandas as pd

from panel.verify import PHONE_RE, label_unresolved, phones_in


class PhoneParseTests(unittest.TestCase):
    def test_formats_and_tel_links(self):
        html = '<a href="tel:+1-720-903-4175">Call</a> or (303) 529-7025, 541.385.7044'
        self.assertEqual(phones_in(html), {"7209034175", "3035297025", "5413857044"})

    def test_two_stated_numbers(self):
        stated = "(208) 545-2675 or (208) 869-6674"
        self.assertEqual({a + b + c for a, b, c in PHONE_RE.findall(stated)}, {"2085452675", "2088696674"})


class UnresolvedLabelTests(unittest.TestCase):
    def test_reason_by_similarity(self):
        df = pd.DataFrame({"resolution_status": ["unresolved", "unresolved", "resolved"],
                           "match_similarity": ["0.9", "0.3", "1.0"]})
        out = label_unresolved(df, 0.8)
        self.assertEqual(out["unresolved_reason"].tolist(), ["same_name_outside_metro", "low_name_similarity", ""])


if __name__ == "__main__":
    unittest.main()


class CleanNameTests(unittest.TestCase):
    def test_location_suffixes_removed(self):
        from panel.resolve import clean_name
        self.assertEqual(clean_name("24 Hour Flood Pros — Houston"), "24 Hour Flood Pros")
        self.assertEqual(clean_name("USA Cabinet Store (Houston)"), "USA Cabinet Store")
        self.assertEqual(clean_name("A-1 Roofing - Houston TX"), "A-1 Roofing")
        self.assertEqual(clean_name("A-1 Roofing"), "A-1 Roofing")


class AreaCodeTests(unittest.TestCase):
    def test_service_area_business_placed_by_area_code(self):
        from panel.areacodes import in_metro
        self.assertTrue(in_metro("(832) 377-0091", "Houston_TX"))
        self.assertFalse(in_metro("(806) 290-8502", "Bend_OR"))
        self.assertFalse(in_metro("(888) 555-0100", "Denver_CO"))
        self.assertFalse(in_metro("", "Boise_ID"))
        self.assertTrue(in_metro("(208) 555-0100", "Client_City_XX"))


class SampleTests(unittest.TestCase):
    def _df(self):
        rows = []
        for niche in ("cleaning", "roofing"):
            for i in range(80):
                rows.append({"niche": niche, "engine": "openai", "name": "%s biz %d" % (niche, i), "city": "Houston",
                             "state": "TX", "resolution_status": "unresolved", "ghost_manual_check": ""})
        return pd.DataFrame(rows)

    def test_sample_size_and_seed(self):
        from panel.verify import draw_sample
        a = draw_sample(self._df(), "2026-12")
        b = draw_sample(self._df(), "2026-12")
        self.assertEqual(a.groupby("niche").size().tolist(), [50, 50])
        self.assertEqual(a["name"].tolist(), b["name"].tolist())

    def test_estimate(self):
        from panel.verify import estimate_ghost
        df = pd.DataFrame([
            {"niche": "roofing", "engine": "openai", "name": "A", "city": "X", "resolution_status": "resolved", "ghost_manual_check": ""},
            {"niche": "roofing", "engine": "openai", "name": "B", "city": "X", "resolution_status": "unresolved", "ghost_manual_check": "not_found"},
            {"niche": "roofing", "engine": "openai", "name": "C", "city": "X", "resolution_status": "unresolved", "ghost_manual_check": "found_local"},
            {"niche": "roofing", "engine": "openai", "name": "D", "city": "X", "resolution_status": "resolved", "ghost_manual_check": ""},
        ])
        est = estimate_ghost(df, n_boot=50)
        row = est[est.engine == "all"].iloc[0]
        self.assertAlmostEqual(row["ghost_rate"], 0.25)
