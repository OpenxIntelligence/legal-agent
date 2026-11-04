"""Judge calibration gate: kappa math, pairing strictness, promotion bars.

Pins the contract: hand-verified kappa values, exact id pairing
(no silent intersection), unknown-rate and minimum-n bars, and
the CLI exit codes that make this CI-gateable.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eval import calibration


def _labels(table: dict[tuple[str, str], int]) -> tuple[list, list]:
    """Paired label lists from a {(judge, human): count} table."""
    judge, human = [], []
    for (jack, halo), count in table.items():
        judge.extend([jack] * count)
        human.extend([halo] * count)
    return judge, human


class KappaTest(unittest.TestCase):
    def test_perfect_agreement_is_one(self) -> None:
        judge = ["pass"] * 30 + ["fail"] * 20
        self.assertEqual(1.0, calibration.cohen_kappa(judge, list(judge)))

    def test_hand_computed_example(self) -> None:
        # Table: PP=45 PF=15 FP=10 FF=30 (judge rows, human cols).
        # po=(45+30)/100=0.75; pe=(60*55+40*45)/10000=0.51;
        # kappa=0.24/0.49=0.4897959...
        judge, human = _labels(
            {("pass", "pass"): 45, ("pass", "fail"): 15,
             ("fail", "pass"): 10, ("fail", "fail"): 30}
        )
        self.assertAlmostEqual(0.4898, calibration.cohen_kappa(judge, human), 4)

    def test_chance_level_is_zero(self) -> None:
        judge, human = _labels(
            {("pass", "pass"): 25, ("pass", "fail"): 25,
             ("fail", "pass"): 25, ("fail", "fail"): 25}
        )
        self.assertAlmostEqual(0.0, calibration.cohen_kappa(judge, human), 9)

    def test_single_class_slice(self) -> None:
        all_pass = ["pass"] * 25
        self.assertEqual(
            1.0, calibration.cohen_kappa(all_pass, list(all_pass))
        )

    def test_rejects_bad_input(self) -> None:
        with self.assertRaises(ValueError):
            calibration.cohen_kappa(["pass"], ["pass", "fail"])
        with self.assertRaises(ValueError):
            calibration.cohen_kappa([], [])
        with self.assertRaises(ValueError):
            calibration.cohen_kappa(["pass"], ["unknown"])


def _write_labels(rows: list[tuple[str, str]]) -> str:
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    )
    with handle:
        for item, label in rows:
            handle.write(json.dumps({"id": item, "label": label}) + "\n")
    return handle.name


class CalibrateTest(unittest.TestCase):
    def _passing_pair(self) -> tuple[dict, dict]:
        # Table PP=75 PF=5 FP=5 FF=15: kappa=0.6875, n=100.
        judge, human = _labels(
            {("pass", "pass"): 75, ("pass", "fail"): 5,
             ("fail", "pass"): 5, ("fail", "fail"): 15}
        )
        ids = [f"item-{i:03d}" for i in range(100)]
        return dict(zip(ids, judge)), dict(zip(ids, human))

    def test_passing_report(self) -> None:
        judge, human = self._passing_pair()
        report = calibration.calibrate(judge, human)
        self.assertTrue(report["passed"], report)
        self.assertAlmostEqual(0.6875, report["kappa"], 4)
        self.assertEqual(100, report["n"])
        self.assertEqual(10, len(report["disagreements"]))

    def test_low_kappa_fails_with_reason(self) -> None:
        judge, human = _labels(
            {("pass", "pass"): 45, ("pass", "fail"): 15,
             ("fail", "pass"): 10, ("fail", "fail"): 30}
        )
        ids = [f"item-{i:03d}" for i in range(100)]
        report = calibration.calibrate(dict(zip(ids, judge)), dict(zip(ids, human)))
        self.assertFalse(report["passed"])
        self.assertTrue(any("kappa" in r for r in report["reasons"]))

    def test_small_sample_fails(self) -> None:
        labels = {f"item-{i}": "pass" for i in range(10)}
        report = calibration.calibrate(dict(labels), dict(labels))
        self.assertFalse(report["passed"])
        self.assertTrue(any("need 20" in r for r in report["reasons"]))

    def test_unknowns_excluded_but_capped(self) -> None:
        judge, human = self._passing_pair()
        for i in range(30):
            judge[f"item-{i:03d}"] = "unknown"
        report = calibration.calibrate(judge, human)
        self.assertFalse(report["passed"])
        self.assertTrue(any("unknown rate" in r for r in report["reasons"]))
        self.assertEqual(30, report["unknowns"])

    def test_id_mismatch_is_an_error(self) -> None:
        judge, human = self._passing_pair()
        judge["rogue-1"] = "pass"
        del human["item-000"]
        with self.assertRaises(ValueError) as raised:
            calibration.calibrate(judge, human)
        self.assertIn("rogue-1", str(raised.exception))
        self.assertIn("item-000", str(raised.exception))

    def test_loader_rejects_bad_rows(self) -> None:
        path = _write_labels([("a", "pass"), ("b", "maybe")])
        try:
            with self.assertRaises(ValueError):
                calibration.load_labels(path)
        finally:
            Path(path).unlink()
        dup = _write_labels([("a", "pass"), ("a", "fail")])
        try:
            with self.assertRaises(ValueError):
                calibration.load_labels(dup)
        finally:
            Path(dup).unlink()

    def test_cli_exit_codes(self) -> None:
        judge, human = self._passing_pair()
        judge_path = _write_labels(sorted(judge.items()))
        human_path = _write_labels(sorted(human.items()))
        try:
            root = str(Path(__file__).resolve().parents[1])
            passing = subprocess.run(
                [sys.executable, "-m", "eval.calibration",
                 judge_path, human_path],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, passing.returncode, passing.stderr)
            self.assertIn('"passed": true', passing.stdout)
            failing = subprocess.run(
                [sys.executable, "-m", "eval.calibration",
                 judge_path, judge_path, "--min-n", "101"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, failing.returncode)
            missing = subprocess.run(
                [sys.executable, "-m", "eval.calibration",
                 judge_path, "/tmp/no-such-labels.jsonl"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(2, missing.returncode)
        finally:
            Path(judge_path).unlink()
            Path(human_path).unlink()




# ---- probability-calibration pins (Brier/ECE/bootstrap/adequacy) ----
def _pair(prediction, outcome, source="human", group="all"):
    return {
        "prediction": prediction,
        "outcome": outcome,
        "source": source,
        "group": group,
    }


class MetricTest(unittest.TestCase):
    def test_brier_hand_value(self) -> None:
        # (0 + 0 + 0.25 + 0.25) / 4.
        pairs = [
            _pair(0.0, 0), _pair(1.0, 1),
            _pair(0.5, 0), _pair(0.5, 1),
        ]
        self.assertAlmostEqual(0.125, calibration.brier_score(pairs))

    def test_ece_hand_value(self) -> None:
        # Bin [0,.5): pred .125 vs out 0 (n=2); bin [.5,1]:
        # pred .875 vs out .5 (n=2). ECE = .0625 + .1875.
        pairs = [
            _pair(0.0, 0), _pair(0.25, 0),
            _pair(0.75, 1), _pair(1.0, 0),
        ]
        self.assertAlmostEqual(
            0.25, calibration.expected_calibration_error(pairs, 2)
        )
        table = calibration.reliability_table(pairs, 2)
        self.assertEqual(2, table[0]["n"])
        self.assertAlmostEqual(0.125, table[0]["mean_pred"])
        self.assertEqual(0, table[0]["mean_outcome"])
        self.assertEqual(2, table[1]["n"])
        self.assertAlmostEqual(0.875, table[1]["mean_pred"])
        self.assertAlmostEqual(0.5, table[1]["mean_outcome"])

    def test_empty_scores_zero(self) -> None:
        self.assertEqual(0.0, calibration.brier_score([]))
        self.assertEqual(0.0, calibration.expected_calibration_error([]))
        self.assertEqual(
            {"estimate": 0.0, "low": 0.0, "high": 0.0, "n": 0},
            calibration.bootstrap_ci(calibration.brier_score, []),
        )


class IntegrityTest(unittest.TestCase):
    def test_agent_labels_rejected(self) -> None:
        for source in ("agent", "self", "model", "Agent"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    calibration.brier_score([_pair(0.9, 1, source)])

    def test_malformed_labels_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calibration.brier_score([_pair(1.5, 1)])
        with self.assertRaises(ValueError):
            calibration.brier_score([_pair(0.5, 2)])
        with self.assertRaises(ValueError):
            calibration.brier_score(["nope"])

    def test_bootstrap_deterministic_and_ordered(self) -> None:
        pairs = [
            _pair(0.9, 1), _pair(0.9, 0),
            _pair(0.1, 0), _pair(0.1, 1),
        ]
        first = calibration.bootstrap_ci(
            calibration.brier_score, pairs, resamples=200, seed=7
        )
        second = calibration.bootstrap_ci(
            calibration.brier_score, pairs, resamples=200, seed=7
        )
        self.assertEqual(first, second)
        self.assertLessEqual(first["low"], first["estimate"])
        self.assertLessEqual(first["estimate"], first["high"])
        self.assertGreater(first["high"], first["low"])

    def test_by_group_breakdown(self) -> None:
        pairs = [
            _pair(1.0, 1, group="contracts"),
            _pair(1.0, 1, group="contracts"),
            _pair(0.5, 0, group="torts"),
            _pair(0.5, 1, group="torts"),
        ]
        groups = calibration.by_group(pairs)
        self.assertEqual(2, groups["contracts"]["n"])
        self.assertEqual(1.0, groups["contracts"]["rate"])
        self.assertEqual(0.0, groups["contracts"]["std"])
        self.assertEqual(0.0, groups["contracts"]["brier"])
        self.assertEqual(0.5, groups["torts"]["rate"])
        self.assertAlmostEqual(0.5, groups["torts"]["std"])
        self.assertAlmostEqual(0.25, groups["torts"]["brier"])


class AdequacyTest(unittest.TestCase):
    def _labels(self, count):
        return [
            _pair(0.8 if n % 2 else 0.2, n % 2, "fixture")
            for n in range(count)
        ]

    def test_below_floor_defers(self) -> None:
        summary = calibration.summarize(
            self._labels(calibration.MIN_LABELS - 1)
        )
        self.assertTrue(
            summary["verdict"].startswith("INADEQUATE"),
            summary["verdict"],
        )
        self.assertIn(str(calibration.MIN_LABELS - 1), summary["verdict"])

    def test_at_floor_permits_fitting_only(self) -> None:
        summary = calibration.summarize(
            self._labels(calibration.MIN_LABELS)
        )
        self.assertTrue(
            summary["verdict"].startswith("SUFFICIENT-FOR-FITTING"),
            summary["verdict"],
        )
        self.assertIn("held-out", summary["verdict"])


if __name__ == "__main__":
    unittest.main()
