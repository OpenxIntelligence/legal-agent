"""Supersession must be claim-local, not memo-global (closeout §4).

Finding A relies on F1; F2 corrects F1. When an UNRELATED
finding cites F2, the current memo rules stay silent and the
memo passes while A still asserts the superseded content:
`cited_ids` in verify_memo is built across all findings, so
a globally present corrective citation excuses the affected
assertion. A companion defect: `superseded_by` is a plain
dict, so branching successors (two corrections of one fact)
silently collapse to one.

Fixed in the working tree by the peer session (claim-local
cited ids + successor sets in verify_memo.py). All three
tests below are hard assertions; if they ever fail, the
memo-global regression is back.
"""

from __future__ import annotations

import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from verification import verify_memo

QUOTE = "Mr. Lindgren confirmed the system is ready for go-live."
OPPOSE = "Open Severity 1 defects: 21 at period end."


class SupersessionLocalityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        analyze(self.fake.client, self.root,
                ["DOC-MINUTES", "DOC-REPORT"], "x")
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def _finding(self, item, doc, quote, summary):
        return {"item": item, "status": "asserted", "summary": summary,
                "citations": [{"document_id": doc, "page": 1,
                               "quote": quote}]}

    def test_unrelated_citation_does_not_absolve_supersession(self) -> None:
        self.case.set_checklist([support.checklist_item("item_x"),
                                 support.checklist_item("item_y")])
        old = self.case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE,
                                    "ready stated")
        new = self.case.record_fact("item_x", "DOC-REPORT", 1, OPPOSE,
                                    "not ready", supersedes=old["id"])
        self.case.record_fact("item_y", "DOC-REPORT", 1, OPPOSE,
                              "defects noted")
        memo = {"findings": [
            self._finding("item_x", "DOC-MINUTES", QUOTE, "System is ready."),
            self._finding("item_y", "DOC-REPORT", OPPOSE, "Defects noted."),
        ]}
        out = verify_memo.run_verification(self.ctx, memo)
        flagged = [fb for fb in out["feedback"] if "superseded by" in fb]
        self.assertTrue(
            flagged,
            f"item_x still asserts superseded {old['id']} while only "
            f"unrelated item_y cites {new['id']}: rules stayed silent",
        )
        self.assertIn("item_x", " ".join(flagged))

    def test_multistep_chain_converges_level_by_level(self) -> None:
        # F1 <- F2 <- F3: distinct passages (a page substring is its
        # own passage), so each level is citable independently.
        short = "Severity 1 defects: 21"
        self.case.set_checklist([support.checklist_item("item_x")])
        first = self.case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE,
                                      "ready stated")
        second = self.case.record_fact("item_x", "DOC-REPORT", 1, OPPOSE,
                                       "not ready",
                                       supersedes=first["id"])
        third = self.case.record_fact("item_x", "DOC-REPORT", 1, short,
                                      "defect count refined",
                                      supersedes=second["id"])

        def cited(*quotes):
            return [
                {"document_id": doc, "page": 1, "quote": quote}
                for doc, quote in quotes
            ]

        only_first = {"findings": [{
            "item": "item_x", "status": "asserted",
            "summary": "System is ready.",
            "citations": cited(("DOC-MINUTES", QUOTE)),
        }]}
        out = verify_memo.run_verification(self.ctx, only_first)
        self.assertIn(
            f"cites {first['id']}, superseded by {second['id']}",
            " ".join(out["feedback"]),
        )
        first_two = {"findings": [{
            "item": "item_x", "status": "asserted",
            "summary": "System is ready, defects noted.",
            "citations": cited(("DOC-MINUTES", QUOTE),
                               ("DOC-REPORT", OPPOSE)),
        }]}
        out = verify_memo.run_verification(self.ctx, first_two)
        self.assertIn(
            f"cites {second['id']}, superseded by {third['id']}",
            " ".join(out["feedback"]),
        )
        all_three = {"findings": [{
            "item": "item_x", "status": "asserted",
            "summary": "Ready per minutes; 21 sev-1 defects per report.",
            "citations": cited(("DOC-MINUTES", QUOTE),
                               ("DOC-REPORT", OPPOSE),
                               ("DOC-REPORT", short)),
        }]}
        out = verify_memo.run_verification(self.ctx, all_three)
        self.assertNotIn("superseded by", " ".join(out["feedback"]))

    def test_absent_correction_is_flagged(self) -> None:
        self.case.set_checklist([support.checklist_item("item_x")])
        old = self.case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE,
                                    "ready stated")
        new = self.case.record_fact("item_x", "DOC-REPORT", 1, OPPOSE,
                                    "not ready", supersedes=old["id"])
        memo = {"findings": [
            self._finding("item_x", "DOC-MINUTES", QUOTE, "System is ready."),
        ]}
        out = verify_memo.run_verification(self.ctx, memo)
        self.assertFalse(out["passed"])
        self.assertIn(
            f"item_x cites {old['id']}, superseded by {new['id']}",
            " ".join(out["feedback"]),
        )

    def test_branching_successors_each_need_disposition(self) -> None:
        self.case.set_checklist([support.checklist_item("item_x")])
        old = self.case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE,
                                    "ready stated")
        first = self.case.record_fact("item_x", "DOC-REPORT", 1, OPPOSE,
                                      "not ready", supersedes=old["id"])
        second = self.case.record_fact("item_x", "DOC-REPORT", 1, OPPOSE,
                                       "not ready either",
                                       supersedes=old["id"],
                                       kind="inference")
        self.assertNotEqual(first["id"], second["id"])
        memo = {"findings": [
            self._finding("item_x", "DOC-MINUTES", QUOTE, "System is ready."),
        ]}
        out = verify_memo.run_verification(self.ctx, memo)
        flagged = " ".join(fb for fb in out["feedback"]
                           if "superseded by" in fb)
        # Both proposed successors must reach a disposition; the
        # current dict keeps only one.
        self.assertIn(first["id"], flagged)
        self.assertIn(second["id"], flagged)


if __name__ == "__main__":
    unittest.main()
