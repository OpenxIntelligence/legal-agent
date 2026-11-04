"""Impact scope and dropped-objection dismissal state (closeout section 8).

Three gaps, all reproduced against current source:

1. trace_fact_impact reports direct citation links (cited_by)
   plus issue-level associations (answers) but never states
   that scope — and covers neither transitive dependencies
   nor conclusion/artifact links. section 8 requires the
   report to state its actual scope.

2. A dropped objection stores only {objection,
   counter_evidence} strings, and record_adversarial's
   docstring calls them "disproven". section 8: a nonempty
   counter-evidence string records an explanation; it does
   not prove the objection was disproved. Agent-proposed
   dismissal must be exposed separately from assessed
   dismissal.

3. section 11 required case: counter-evidence naming an
   unresolvable reference (F99, unknown to the record) is
   stored silently with no unassessed/unresolvable marker.

Fixes are peer-owned (reasoning.py, case_record.py). All
marked expectedFailure until they land.
"""

from __future__ import annotations

import unittest

from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.reasoning import trace_fact_impact
from tests import support


def _review_kwargs(**kw):
    args = {
        "strongest_against": "x",
        "fact_risks": "x",
        "adverse_authority": "x",
        "overlooked": "x",
        "opposing_counsel": "x",
        "judge_needs": "x",
        "alternative_theory": "x",
        "reversing_fact": "x",
        "revision_needed": False,
        "revision": "no change needed",
    }
    args.update(kw)
    return args


class ImpactScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_trace_states_its_scope(self) -> None:
        data = {
            "facts": [{"id": "F1", "item": "not_ready",
                       "document_id": "D", "page": 1,
                       "quote": "defects: 21", "stance": "supports"}],
            "foundational": [{"id": "FQ1", "issue": "not_ready"}],
        }
        memo = {"findings": [{"item": "not_ready", "status": "supported",
                              "citations": [{"document_id": "D", "page": 1,
                                             "quote": "defects: 21"}]}]}
        trace = trace_fact_impact(data, memo, "F1")
        scope = str(trace.get("scope", "") or "").lower()
        self.assertTrue(
            "citat" in scope and "answer" in scope,
            f"no scope statement covering links+answers: {trace!r}",
        )
        self.assertFalse(
            "transitive" in scope or "all affected conclusions" in scope,
            f"scope overclaims uncovered analysis: {scope!r}",
        )

    def test_dropped_marks_agent_proposed_vs_assessed(self) -> None:
        entry = self.case.record_adversarial(
            **_review_kwargs(dropped=[
                {"objection": "count is stale",
                 "counter_evidence": "recount F2 confirms 21"},
            ])
        )
        dropped = entry["dropped"][0]
        self.assertTrue(
            any(k in dropped
                for k in ("assessment", "disposition", "state", "verdict")),
            f"dismissal carries no assessment state: {dropped!r}",
        )

    def test_dropped_unresolvable_counter_evidence_flagged(self) -> None:
        entry = self.case.record_adversarial(
            **_review_kwargs(dropped=[
                {"objection": "count is stale",
                 "counter_evidence": "recount F99 confirms 21"},
            ])
        )
        blob = repr(entry["dropped"][0]).lower()
        self.assertIn("f99", blob)
        self.assertTrue(
            any(m in blob for m in ("unresolv", "unassess", "unknown",
                                    "proposed")),
            f"unresolvable F99 stored with no marker: {blob}",
        )


if __name__ == "__main__":
    unittest.main()
