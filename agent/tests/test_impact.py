"""Block extractions: dropped objections + fact impact trace.

Pins the two smallest steals: adversarial reviews record raised
and disproven objections with counter-evidence (review's dropped
with reasons), and trace_fact_impact answers "what breaks if
this fact goes" (model-ledger impact trace), enforced by the
auditor against filed memos resting on withdrawn/superseded facts.
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.textnorm import NORMALIZER_VERSION
from agent_kit.tools import adversarial_review
from scripts.audit import _check_impact, _check_spans
from tests import support
from verification import verify_memo


def _review(**kw):
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


class DroppedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.fake = support.scripted(self.root, [])
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def test_dropped_stored_with_counter_evidence(self) -> None:
        entry = self.case.record_adversarial(
            **_review(dropped=[
                {"objection": "count is stale",
                 "counter_evidence": "recount F2 confirms 21"},
            ])
        )
        # Dismissals are agent-proposed, never assessed (closeout
        # §8); F2 names no recorded fact, so it is flagged.
        self.assertEqual(
            [{"objection": "count is stale",
              "counter_evidence": "recount F2 confirms 21",
              "assessment": "proposed",
              "unresolved_refs": ["F2"]}],
            entry["dropped"],
        )

    def test_dropped_defaults_empty(self) -> None:
        entry = self.case.record_adversarial(**_review())
        self.assertEqual([], entry["dropped"])

    def test_invalid_dropped_rejected(self) -> None:
        with self.assertRaises(CaseError):
            self.case.record_adversarial(
                **_review(dropped=[{"objection": "x"}])
            )
        with self.assertRaises(CaseError):
            self.case.record_adversarial(**_review(dropped="x"))
        with self.assertRaises(CaseError):
            self.case.record_adversarial(
                **_review(dropped=[{"objection": " ", "counter_evidence": "y"}])
            )

    def test_tool_passes_dropped_through(self) -> None:
        result = adversarial_review.run(
            self.ctx,
            _review(dropped=[
                {"objection": "o", "counter_evidence": "c"},
            ]),
        )
        self.assertTrue(result["reviewed"].startswith("AR"))
        latest = self.case.data["adversarial"][-1]
        self.assertEqual(
            [{"objection": "o", "counter_evidence": "c",
              "assessment": "proposed", "unresolved_refs": []}],
            latest["dropped"],
        )


def _data():
    return {
        "facts": [
            {"id": "F1", "item": "not_ready", "document_id": "D",
             "page": 1, "quote": "defects: 21", "stance": "supports"},
            {"id": "F2", "item": "not_ready", "document_id": "D",
             "page": 1, "quote": "defects: 21", "stance": "supports",
             "supersedes": "F1"},
            {"id": "F3", "item": "timing", "document_id": "D",
             "page": 1, "quote": "go-live June", "stance": "supports"},
        ],
        "withdrawn_facts": [
            {"id": "F0", "item": "timing", "document_id": "P",
             "page": 2, "quote": "privileged note", "stance": "supports"},
        ],
        "foundational": [
            {"id": "FQ1", "issue": "not_ready"},
            {"id": "FQ2", "issue": "timing"},
        ],
    }


def _memo(*cited):
    return {
        "findings": [
            {
                "item": item,
                "status": status,
                "summary": "s",
                "citations": [
                    {"document_id": doc, "page": page, "quote": quote}
                    for doc, page, quote in quotes
                ],
            }
            for item, status, quotes in cited
        ]
    }


class TraceTest(unittest.TestCase):
    def test_live_fact_trace(self) -> None:
        memo = _memo(("not_ready", "asserted", [("D", 1, "defects: 21")]))
        trace = reasoning.trace_fact_impact(_data(), memo, "F2")
        self.assertEqual("live", trace["status"])
        self.assertEqual("not_ready", trace["item"])
        self.assertEqual(["not_ready (asserted)"], trace["cited_by"])
        self.assertEqual(["FQ1"], trace["answers"])
        self.assertIsNone(trace["superseded_by"])

    def test_superseded_fact_trace(self) -> None:
        memo = _memo(("not_ready", "asserted", [("D", 1, "defects: 21")]))
        trace = reasoning.trace_fact_impact(_data(), memo, "F1")
        self.assertEqual("superseded", trace["status"])
        self.assertEqual("F2", trace["superseded_by"])
        self.assertEqual(["not_ready (asserted)"], trace["cited_by"])

    def test_withdrawn_fact_trace(self) -> None:
        memo = _memo(("timing", "asserted", [("P", 2, "privileged note")]))
        trace = reasoning.trace_fact_impact(_data(), memo, "F0")
        self.assertEqual("withdrawn", trace["status"])
        self.assertEqual(["timing (asserted)"], trace["cited_by"])
        self.assertEqual(["FQ2"], trace["answers"])

    def test_unknown_fact(self) -> None:
        trace = reasoning.trace_fact_impact(_data(), _memo(), "F9")
        self.assertEqual("unknown", trace["status"])
        self.assertEqual([], trace["cited_by"])

    def test_branching_successors_all_listed(self) -> None:
        data = _data()
        data["facts"].append(
            {"id": "F2b", "item": "not_ready", "document_id": "D",
             "page": 1, "quote": "defects: 21", "stance": "supports",
             "supersedes": "F1"},
        )
        trace = reasoning.trace_fact_impact(data, _memo(), "F1")
        self.assertEqual("superseded", trace["status"])
        # The old loop kept the last match only; branches collapsed.
        self.assertEqual(["F2", "F2b"], trace["superseded_by_all"])
        self.assertEqual("F2", trace["superseded_by"])

    def test_page_int_and_str_join(self) -> None:
        memo = _memo(("not_ready", "asserted", [("D", "1", "defects: 21")]))
        trace = reasoning.trace_fact_impact(_data(), memo, "F2")
        self.assertEqual(["not_ready (asserted)"], trace["cited_by"])

    def test_curly_quote_joins_straight_fact(self) -> None:
        memo = _memo(
            ("timing", "asserted", [("D", 1, 'chair said "go-live June"')])
        )
        data = _data()
        data["facts"][2]["quote"] = "chair said \u201cgo-live June\u201d"
        trace = reasoning.trace_fact_impact(data, memo, "F3")
        self.assertEqual(["timing (asserted)"], trace["cited_by"])

    def test_join_sites_share_normalizer_pin(self) -> None:
        self.assertEqual("textnorm/v1", NORMALIZER_VERSION)
        self.assertEqual(NORMALIZER_VERSION, reasoning.QUOTE_KEY_NORMALIZER)
        self.assertEqual(NORMALIZER_VERSION, verify_memo.QUOTE_NORMALIZER)

    def test_cross_item_quote_not_misattributed(self) -> None:
        data = _data()
        data["facts"].append(
            {"id": "F4", "item": "timing", "document_id": "D",
             "page": 1, "quote": "defects: 21", "stance": "context"},
        )
        memo = _memo(("timing", "asserted", [("D", 1, "defects: 21")]))
        trace = reasoning.trace_fact_impact(data, memo, "F2")
        self.assertEqual([], trace["cited_by"])


class AuditImpactTest(unittest.TestCase):
    def _run(self, data):
        outcome: dict = {}
        fails, notes = [], []

        def fail(check, detail):
            fails.append((check, detail))

        def note(check, detail):
            notes.append((check, detail))

        _check_impact(data, fail, note)
        outcome["fails"] = fails
        outcome["notes"] = notes
        return outcome

    def test_no_memo_notes(self) -> None:
        outcome = self._run({})
        self.assertEqual([], outcome["fails"])
        self.assertEqual("impact", outcome["notes"][0][0])

    def test_clean_memo_notes(self) -> None:
        data = _data()
        data["memo"] = _memo(("timing", "asserted", [("D", 1, "go-live June")]))
        outcome = self._run(data)
        self.assertEqual([], outcome["fails"])
        self.assertIn("live, current", outcome["notes"][0][1])

    def test_withdrawn_citation_fails(self) -> None:
        data = _data()
        data["memo"] = _memo(
            ("timing", "asserted", [("P", 2, "privileged note")])
        )
        outcome = self._run(data)
        self.assertEqual(1, len(outcome["fails"]))
        self.assertIn("F0", outcome["fails"][0][1])
        self.assertIn("timing", outcome["fails"][0][1])

    def test_superseded_without_current_fails(self) -> None:
        data = _data()
        # F1 and F2 share the quote; drop F2's match by re-quoting it
        # so the memo cites only the superseded version.
        data["facts"][1]["quote"] = "defects: 21 confirmed"
        data["memo"] = _memo(
            ("not_ready", "asserted", [("D", 1, "defects: 21")])
        )
        outcome = self._run(data)
        self.assertEqual(1, len(outcome["fails"]))
        self.assertIn("F1", outcome["fails"][0][1])
        self.assertIn("F2", outcome["fails"][0][1])

    def test_superseded_with_current_passes(self) -> None:
        data = _data()
        data["facts"][1]["quote"] = "defects: 21 confirmed"
        data["memo"] = _memo(
            ("not_ready", "asserted",
             [("D", 1, "defects: 21"), ("D", 1, "defects: 21 confirmed")])
        )
        outcome = self._run(data)
        self.assertEqual([], outcome["fails"])


class SpanInventoryTest(unittest.TestCase):
    def _run(self, data):
        fails, notes = [], []

        def fail(check, detail):
            fails.append((check, detail))

        def note(check, detail):
            notes.append((check, detail))

        _check_spans(data, fail, note)
        return fails, notes

    def test_no_memo_notes(self) -> None:
        fails, notes = self._run({})
        self.assertEqual([], fails)
        self.assertEqual("spans", notes[0][0])

    def test_inventory_counts(self) -> None:
        data = {
            "memo": {
                "findings": [
                    {"item": "a", "status": "asserted", "citations": [
                        {"document_id": "D", "page": 1,
                         "quote": "Open Severity 1 defects: 21"},
                    ]},
                    {"item": "b", "status": "asserted", "citations": [
                        {"document_id": "D", "page": 1, "quote": "Green light"},
                    ]},
                ]
            }
        }
        fails, notes = self._run(data)
        self.assertEqual([], fails)
        self.assertIn("2 cited spans", notes[0][1])
        self.assertIn("1 with digits", notes[0][1])
        self.assertIn("shortest b (11 chars)", notes[0][1])


class WithdrawalImpactTest(unittest.TestCase):
    def test_flag_carries_precise_impact(self) -> None:
        from agent_kit.tools.analyze_document import analyze

        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        fake = support.scripted(root, [])
        run_id = new_run(root, instructions="i", objective="o")
        case = CaseRecord(root, run_id)
        case.set_checklist(support.CHECKLIST_ITEMS)
        analyze(fake.client, root, ["DOC-REPORT"], "x")
        fact = case.record_fact(
            "not_ready", "DOC-REPORT", 1,
            "Open Severity 1 defects: 21", "21 open",
        )
        case.answer_foundational(
            qclass="facts", question="Q?", rule_or_source="r",
            evidence="e", answer="a", basis="b", contrary="c",
            effect="f", issue="not_ready",
        )
        case.file_memo(
            {"conclusion": "c", "gaps": "g", "findings": [{
                "item": "not_ready", "status": "asserted", "summary": "s",
                "citations": [{
                    "document_id": "DOC-REPORT", "page": 1,
                    "quote": "Open Severity 1 defects: 21",
                }],
            }]},
            "awaiting_signoff",
        )
        case.mark_answered(["not_ready"], {"not_ready": "asserted"})
        flag = case.flag_privileged("DOC-REPORT", "test privilege")
        self.assertEqual(
            {"item": "not_ready",
             "cited_by": ["not_ready (asserted)"],
             "answers": ["FQ1"]},
            flag["impact"][fact["id"]],
        )
        # Coarse fail-safe reopening is unchanged.
        self.assertEqual("open", case.data["checklist"]["not_ready"]["status"])


if __name__ == "__main__":
    unittest.main()
