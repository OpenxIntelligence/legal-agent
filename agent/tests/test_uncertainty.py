"""Uncertainty interfaces: hand-checked rates, abstention, gates.

Pins the math (rates, std, interval ordering, determinism),
the abstention precision/recall semantics, and the refusal
to score unknown targets or bless empty label sets.
"""

from __future__ import annotations

import unittest

from eval import uncertainty


class RateTest(unittest.TestCase):
    def test_hand_values(self) -> None:
        interval = uncertainty.rate_ci([1, 1, 1, 0])
        self.assertEqual(0.75, interval["rate"])
        self.assertAlmostEqual(0.4330127019, interval["std"])
        self.assertEqual(4, interval["n"])
        self.assertLessEqual(interval["low"], interval["rate"])
        self.assertLessEqual(interval["rate"], interval["high"])

    def test_deterministic(self) -> None:
        outcomes = [1, 0, 1, 1, 0, 0, 1, 0]
        first = uncertainty.rate_ci(outcomes)
        second = uncertainty.rate_ci(list(outcomes))
        self.assertEqual(first, second)

    def test_empty_is_zeroed(self) -> None:
        self.assertEqual(
            {"rate": 0.0, "std": 0.0, "low": 0.0, "high": 0.0, "n": 0},
            uncertainty.rate_ci([]),
        )


class AbstentionTest(unittest.TestCase):
    def test_precision_and_recall(self) -> None:
        # Should silence on 3, silent on 2 (1 correct, 1 wrong).
        pairs = [
            (True, True), (True, False), (True, False),
            (False, True), (False, False),
        ]
        report = uncertainty.abstention(pairs)
        self.assertEqual(0.5, report["precision"]["rate"])
        self.assertAlmostEqual(1 / 3, report["recall"]["rate"])
        self.assertEqual(5, report["n"])
        self.assertEqual(2, report["silences"])
        self.assertEqual(3, report["should_silences"])

    def test_perfect_abstention(self) -> None:
        pairs = [(True, True), (True, True), (False, False)]
        report = uncertainty.abstention(pairs)
        self.assertEqual(1.0, report["precision"]["rate"])
        self.assertEqual(1.0, report["recall"]["rate"])


class PanelTest(unittest.TestCase):
    def test_retrieval_panel_shape(self) -> None:
        report = {
            "queries": [
                {"id": "a", "split": "dev", "complete": True,
                 "required_complete": True, "prohibited_free": True,
                 "empty": False},
                {"id": "b", "split": "heldout", "complete": False,
                 "required_complete": False, "prohibited_free": True,
                 "empty": True},
            ]
        }
        panel = uncertainty.summarize_retrieval(report, {"b"})
        self.assertEqual(0.5, panel["complete"]["rate"])
        self.assertEqual(1.0, panel["prohibited_free"]["rate"])
        self.assertEqual(1.0, panel["abstention"]["recall"]["rate"])
        self.assertEqual(1, panel["by_split"]["dev"]["n"])
        self.assertEqual(1, panel["by_split"]["heldout"]["n"])

    def test_unknown_target_refused(self) -> None:
        with self.assertRaises(ValueError):
            uncertainty.calibration_status("vibes", [])

    def test_empty_labels_inadequate(self) -> None:
        for target in uncertainty.TARGETS:
            with self.subTest(target=target):
                status = uncertainty.calibration_status(target, [])
                self.assertTrue(
                    status["verdict"].startswith("INADEQUATE"),
                    status["verdict"],
                )
                self.assertEqual(target, status["target"])


if __name__ == "__main__":
    unittest.main()
