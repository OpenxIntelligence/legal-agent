"""Sufficiency gate logic over synthetic ranks (deterministic)."""

from __future__ import annotations

import unittest

from agent_kit.sufficiency import assess


class SufficiencyTest(unittest.TestCase):
    def test_all_sets_hit_is_sufficient(self) -> None:
        out = assess(
            [{"label": "rate", "passages": [("D", 1)]},
             {"label": "date", "passages": [("D", 2), ("E", 1)]}],
            [("D", 1), ("E", 1)],
            adverse=[("D", 3)],
        )
        self.assertTrue(out["sufficient"])
        self.assertEqual([], out["missing"])
        self.assertFalse(out["abstain"])

    def test_missing_set_names_the_gap(self) -> None:
        out = assess(
            [{"label": "rate", "passages": [("D", 1)]},
             {"label": "approver", "passages": [("D", 5)]}],
            [("D", 1)],
        )
        self.assertFalse(out["sufficient"])
        self.assertEqual(["approver"], [m["label"] for m in out["missing"]])
        self.assertFalse(out["abstain"])

    def test_nothing_hit_advises_abstention(self) -> None:
        out = assess(
            [{"label": "insurer", "passages": []}],
            [("X", 9)],
        )
        self.assertFalse(out["sufficient"])
        self.assertTrue(out["abstain"])
        self.assertEqual("insurer", out["missing"][0]["label"])

    def test_unretrieved_evidence_is_missing_not_abstention(self) -> None:
        out = assess(
            [{"label": "rate", "passages": [("V2", 1)]}],
            [("V1", 2)],
            superseded=[("V1", 2)],
        )
        self.assertFalse(out["sufficient"])
        self.assertFalse(out["abstain"])
        self.assertEqual(["rate"], [m["label"] for m in out["missing"]])

    def test_adverse_half_recall_reported(self) -> None:
        out = assess(
            [{"label": "q", "passages": [("D", 1)]}],
            [("D", 1), ("O", 1)],
            adverse=[("O", 1), ("O", 2)],
        )
        self.assertTrue(out["sufficient"])
        self.assertFalse(out["adverse_covered"])
        self.assertEqual(0.5, out["adverse_recall"])

    def test_superseded_return_flags_confusion(self) -> None:
        out = assess(
            [{"label": "rate", "passages": [("V2", 1)]}],
            [("V2", 1), ("V1", 1)],
            superseded=[("V1", 1)],
        )
        self.assertFalse(out["sufficient"])
        self.assertEqual(
            [{"document_id": "V1", "page": 1}], out["version_confusion"])


class MalformedInputTest(unittest.TestCase):
    """Corrupt records fail closed with reasons, never raise."""

    def test_non_dict_need_keeps_the_need_open(self) -> None:
        out = assess(["oops"], [("D", 1)])
        self.assertFalse(out["sufficient"])
        self.assertFalse(out["abstain"])
        self.assertEqual("malformed need entry",
                         out["missing"][0]["reason"])

    def test_unusable_passages_keep_the_need_open(self) -> None:
        out = assess(
            [{"label": "rate", "passages": ["oops", ("D", "p1")]}],
            [("D", 1)],
        )
        self.assertFalse(out["sufficient"])
        self.assertEqual("rate", out["missing"][0]["label"])
        self.assertIn("usable", out["missing"][0]["reason"])

    def test_malformed_returned_cannot_satisfy(self) -> None:
        out = assess(
            [{"label": "rate", "passages": [("D", 1)]}],
            ["oops", ("D", "p1")],
        )
        self.assertFalse(out["sufficient"])
        self.assertEqual("rate", out["missing"][0]["label"])

    def test_malformed_adverse_counts_as_uncovered(self) -> None:
        out = assess(
            [{"label": "q", "passages": [("D", 1)]}],
            [("D", 1), ("O", 1)],
            adverse=[("O", 1), "oops"],
        )
        self.assertFalse(out["adverse_covered"])
        self.assertTrue(any("malformed adverse" in r
                            for r in out["reasons"]))

    def test_malformed_superseded_voids_sufficiency(self) -> None:
        out = assess(
            [{"label": "q", "passages": [("D", 1)]}],
            [("D", 1)],
            superseded=["oops"],
        )
        self.assertFalse(out["sufficient"])
        self.assertEqual([], out["version_confusion"])
        self.assertTrue(any("malformed superseded" in r
                            for r in out["reasons"]))


if __name__ == "__main__":
    unittest.main()
