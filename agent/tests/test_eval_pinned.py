"""Pinned reference gate: normalized comparison, value-free diffs, quarantine."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from eval import pinned


def _report(**overrides) -> dict:
    base = {
        "case": "harbor-gap",
        "verification": True,
        "client": "scripted",
        "status": "answered",
        "coverage": {"answered": 2, "escalated": 0, "open": 0, "total": 2},
        "memo": {"status": "filed"},
        "claims": [{"outcome": "pass", "passed": True, "assessed": 3}],
        "escalations": 0,
        "refusals": 0,
        "diagnostics": {"turns": 6, "agent_tokens": 10, "tool_tokens": 4,
                        "tool_calls": 6},
    }
    base.update(overrides)
    return base


class PinnedGateTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pinned = Path(tmp.name)

    def pin(self, name: str, report: dict) -> None:
        (self.pinned / f"{name}.json").write_text(json.dumps(report))

    def test_identical_reports_pass_with_no_diffs(self) -> None:
        self.pin("harbor-gap", _report())
        result = pinned.compare_case(
            "harbor-gap", _report(), pinned_dir=self.pinned
        )
        self.assertEqual(
            (False, [], []),
            (result["quarantined"], result["quarantine"], result["diffs"]),
        )

    def test_volatile_leaves_never_gate(self) -> None:
        self.pin("harbor-gap", _report())
        cand = _report()
        cand["diagnostics"]["elapsed_s"] = 12.5
        cand["diagnostics"]["latency_ms"] = 345.1
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertFalse(result["quarantined"])

    def test_changed_status_is_divergent_and_value_free(self) -> None:
        self.pin("harbor-gap", _report())
        cand = _report(status="CANARY-STATUS-VALUE", memo={"status": "filed"})
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertEqual(["divergent"], result["quarantine"])
        self.assertEqual(
            [{"path": "$.status", "kind": "changed"}], result["diffs"]
        )
        self.assertNotIn("CANARY-STATUS-VALUE", json.dumps(result))

    def test_numeric_leaves_carry_only_deltas(self) -> None:
        self.pin("harbor-gap", _report())
        cand = _report()
        cand["diagnostics"]["turns"] = 9
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertIn(
            {"path": "$.diagnostics.turns", "kind": "changed", "delta": 3},
            result["diffs"],
        )

    def test_missing_and_extra_keys(self) -> None:
        self.pin("harbor-gap", _report())
        cand = _report()
        del cand["memo"]
        cand["surprise"] = 1
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        kinds = {(d["path"], d["kind"]) for d in result["diffs"]}
        self.assertIn(("$.memo", "missing"), kinds)
        self.assertIn(("$.surprise", "extra"), kinds)

    def test_list_length_reports_delta_and_items_diff_by_index(self) -> None:
        self.pin("harbor-gap", _report())
        cand = _report()
        cand["claims"].append({"outcome": "pass", "passed": True,
                               "assessed": 1})
        cand["claims"][0] = dict(cand["claims"][0], assessed=9)
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertIn(
            {"path": "$.claims.length", "kind": "changed", "delta": 1},
            result["diffs"],
        )
        self.assertIn(
            {"path": "$.claims[0].assessed", "kind": "changed", "delta": 6},
            result["diffs"],
        )

    def test_diffs_cap_at_100_with_truncation_flag(self) -> None:
        ref = {f"k{i}": i for i in range(150)}
        cand = {f"k{i}": i + 1 for i in range(150)}
        diff = pinned.diff_reports(ref, cand)
        self.assertEqual(100, len(diff["diffs"]))
        self.assertTrue(diff["truncated"])

    def test_unlisted_case_quarantines(self) -> None:
        result = pinned.compare_case(
            "no-such-case", _report(), pinned_dir=self.pinned
        )
        self.assertEqual(["unlisted"], result["quarantine"])

    def test_corrupt_reference_quarantines_unable(self) -> None:
        (self.pinned / "harbor-gap.json").write_text("{not json")
        result = pinned.compare_case(
            "harbor-gap", _report(), pinned_dir=self.pinned
        )
        self.assertEqual(["unable"], result["quarantine"])

    def test_exhausted_turn_budget_quarantines_capacity(self) -> None:
        self.pin("harbor-gap", _report())
        cand = _report()
        cand["diagnostics"]["turns"] = 30
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned, turns=30
        )
        self.assertIn("capacity", result["quarantine"])

    def test_capture_then_gate_passes_on_real_runs(self) -> None:
        names = pinned.capture(pinned_dir=self.pinned, turns=30)
        self.assertEqual(["harbor-gap", "harbor-readiness"], names)
        report, code = pinned.gate(pinned_dir=self.pinned, turns=30)
        self.assertEqual(("PASS", 0), (report["verdict"], code))

    def test_changed_tool_sequence_is_divergent(self) -> None:
        ref = _report(tool_sequence=["set_checklist", "update_plan"])
        self.pin("harbor-gap", ref)
        cand = _report(tool_sequence=["set_checklist", "list_documents"])
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertEqual(["divergent"], result["quarantine"])
        self.assertEqual(
            [{"path": "$.tool_sequence[1]", "kind": "changed"}],
            result["diffs"],
        )

    def test_extra_tool_call_reports_length_delta(self) -> None:
        ref = _report(tool_sequence=["set_checklist"])
        self.pin("harbor-gap", ref)
        cand = _report(tool_sequence=["set_checklist", "update_plan"])
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertIn(
            {"path": "$.tool_sequence.length", "kind": "changed",
             "delta": 1},
            result["diffs"],
        )

    def test_real_reports_carry_the_tool_sequence(self) -> None:
        from eval import run as eval_run

        report = eval_run.run_case("harbor-gap", verification=True)
        self.assertIn("set_checklist", report["tool_sequence"])
        self.assertTrue(
            all(isinstance(name, str) for name in report["tool_sequence"])
        )

    def test_changed_citation_set_is_divergent(self) -> None:
        ref = _report(citation_set=[["DOC-A", "1"]])
        self.pin("harbor-gap", ref)
        cand = _report(citation_set=[["DOC-A", "1"], ["DOC-B", "2"]])
        result = pinned.compare_case(
            "harbor-gap", cand, pinned_dir=self.pinned
        )
        self.assertEqual(["divergent"], result["quarantine"])
        self.assertIn(
            {"path": "$.citation_set.length", "kind": "changed",
             "delta": 1},
            result["diffs"],
        )

    def test_real_reports_carry_sorted_unique_citations(self) -> None:
        from eval import run as eval_run

        report = eval_run.run_case("harbor-gap", verification=True)
        cites = report["citation_set"]
        self.assertEqual(sorted(cites), cites)
        self.assertEqual(len(set(map(tuple, cites))), len(cites))
        for doc_id, page in cites:
            self.assertTrue(isinstance(doc_id, str) and isinstance(page, str))


if __name__ == "__main__":
    unittest.main()
