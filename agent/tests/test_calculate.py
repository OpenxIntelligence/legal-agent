"""Deterministic calculation tool, record, and replay gate (Item 5, G5).

Deadlines and money go through agent_kit.compute and are
recorded with inputs, result, rule, and assumptions; the filing
gate re-runs each record and blocks on mismatch. The known-answer
eval (eval/calculation_cases.py + calculation_run.py) pins
arithmetic conformance; this module pins the tool/record/gate
plumbing around it.
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import calculate
from eval import calculation_run
from tests import support


class CalculationEvalTest(unittest.TestCase):
    def test_floors_hold(self) -> None:
        reports = calculation_run.run_all()
        summary = calculation_run.summarize(reports)
        self.assertEqual(
            summary["answers"]["total"], summary["answers"]["hit"],
            summary["verdict"],
        )
        self.assertEqual(
            summary["refusals"]["total"], summary["refusals"]["hit"],
            summary["verdict"],
        )
        for report in reports:
            self.assertEqual(
                calculation_run.REPORT_KEYS, set(report),
                f"report schema drift: {report['case']}",
            )


class CalculateToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def test_tool_registered(self) -> None:
        self.assertIn("calculate", tools.BASE_TOOLS)
        self.assertIn("calculate", tools.MODULES)
        names = [t["name"] for t in tools.definitions(self.root, self.run_id)]
        self.assertIn("calculate", names)

    def test_deadline_round_trip(self) -> None:
        result = calculate.run(self.ctx, {
            "op": "add_business_days",
            "start": "2026-01-02",
            "days": 1,
            "holidays": [],
            "rule": "contract section 3",
            "assumptions": "no closures beyond weekends",
        })
        entry = result["computed"]
        self.assertEqual("C1", entry["id"])
        self.assertEqual("2026-01-05", entry["result"])
        self.assertEqual(
            {"start": "2026-01-02", "days": 1, "holidays": []},
            entry["inputs"],
        )
        self.assertEqual(
            self.case.data["computations"][-1]["id"], "C1"
        )

    def test_missing_inputs_refused_with_guidance(self) -> None:
        with self.assertRaises(CaseError) as missing:
            calculate.run(self.ctx, {"op": "add_days", "days": 30})
        self.assertIn("YYYY-MM-DD", str(missing.exception))
        with self.assertRaises(CaseError) as holidays:
            calculate.run(self.ctx, {
                "op": "add_business_days", "start": "2026-01-05", "days": 1,
            })
        self.assertIn("holidays", str(holidays.exception))
        with self.assertRaises(CaseError) as op:
            calculate.run(self.ctx, {"op": "compound"})
        self.assertIn("unknown computation", str(op.exception))


class ComputationGateTest(unittest.TestCase):
    def _data(self, computations) -> dict:
        return {"task_type": "research", "computations": computations}

    def _entry(self, **kw) -> dict:
        entry = {
            "id": "C1",
            "op": "add_days",
            "inputs": {"start": "2026-01-01", "days": 30},
            "result": "2026-01-31",
            "rule": "contract section 3",
            "assumptions": "",
        }
        entry.update(kw)
        return entry

    def test_clean_record_passes(self) -> None:
        problems, advisory = reasoning._check_computations(
            self._data([self._entry()])
        )
        self.assertEqual([], problems)
        self.assertEqual([], advisory)

    def test_tampered_result_blocks(self) -> None:
        problems, _ = reasoning._check_computations(
            self._data([self._entry(result="2026-02-15")])
        )
        self.assertEqual(1, len(problems))
        self.assertIn("does not replay", problems[0])

    def test_malformed_entry_blocks(self) -> None:
        problems, _ = reasoning._check_computations(
            self._data([self._entry(op="compound")])
        )
        self.assertEqual(1, len(problems))
        self.assertIn("malformed", problems[0])
        problems, _ = reasoning._check_computations(
            self._data(["C1"])
        )
        self.assertEqual(1, len(problems))

    def test_missing_rule_advises(self) -> None:
        problems, advisory = reasoning._check_computations(
            self._data([self._entry(rule="")])
        )
        self.assertEqual([], problems)
        self.assertEqual(1, len(advisory))
        self.assertIn("no rule", advisory[0])

    def test_gate_wires_into_run_gate(self) -> None:
        result = reasoning.run_gate(
            self._data([self._entry(result="2026-02-15")]), {}
        )
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("does not replay" in p for p in result["problems"]),
            result["problems"],
        )


if __name__ == "__main__":
    unittest.main()
