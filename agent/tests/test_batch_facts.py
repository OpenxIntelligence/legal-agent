"""Increment 1: bounded batch fact recording with per-entry validation.

One record_facts call carries up to MAX_FACTS_PER_BATCH proposals;
each is validated exactly as record_fact would validate it, in
order. Recording never settles an item. All fixtures synthetic.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agent_kit import case_record, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support

from tests.test_kit import call


def proposal(item="not_ready", doc="DOC-REPORT", page=1, quote="", claim="c",
             stance="supports", **extra):
    entry = {
        "item": item,
        "document_id": doc,
        "page": page,
        "quote": quote,
        "claim": claim,
        "stance": stance,
    }
    entry.update(extra)
    return entry


class BatchFactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.client = self.fake.client
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=False
        )
        analyze(self.client, self.root, ["DOC-REPORT", "DOC-MINUTES"], "x")
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self.report_text = support.INITIAL["DOC-REPORT"][0]
        self.minutes_text = support.INITIAL["DOC-MINUTES"][0]

    def test_mixed_batch_records_valid_and_reports_each_error(self) -> None:
        result = self.case.record_facts(
            [
                proposal(quote=self.report_text[:40]),
                proposal(quote="an invented quote that is nowhere"),
                proposal(
                    item="ready_statement",
                    doc="DOC-MINUTES",
                    quote=self.minutes_text[:40],
                    stance="opposes",
                ),
                proposal(item="no_such_item", quote=self.report_text[:40]),
            ]
        )
        self.assertEqual(2, result["accepted"])
        self.assertEqual(2, result["errors"])
        ok = [r for r in result["results"] if r["ok"]]
        bad = [r for r in result["results"] if not r["ok"]]
        self.assertEqual(["F1", "F2"], [r["id"] for r in ok])
        self.assertEqual([0, 2], [r["index"] for r in ok])
        self.assertEqual([1, 3], [r["index"] for r in bad])
        self.assertIn("quote not found", bad[0]["error"])
        self.assertIn("unknown checklist item", bad[1]["error"])
        for entry in result["results"]:
            self.assertTrue(entry["op"].startswith("op-"))
        # The invented quote was logged as rejected; items stay open.
        self.assertEqual(1, len(self.case.data["rejected_facts"]))
        progress = self.case.progress()
        self.assertIn("not_ready", progress["open"])
        self.assertIn("ready_statement", progress["open"])
        self.assertIn("not_ready", result["open"])

    def test_batch_matches_single_calls_in_order(self) -> None:
        proposals = [
            proposal(quote=self.report_text[:40]),
            proposal(
                item="ready_statement",
                doc="DOC-MINUTES",
                quote=self.minutes_text[:40],
                stance="opposes",
                basis="independent observation",
            ),
        ]
        batched = self.case.record_facts([dict(p) for p in proposals])
        other_id = new_run(
            self.root, instructions="i", objective="o", verification=False
        )
        other = CaseRecord(self.root, other_id)
        other.set_checklist(support.CHECKLIST_ITEMS)
        singles = [
            other.record_fact(
                p["item"], p["document_id"], p["page"], p["quote"],
                p["claim"], p["stance"], p.get("basis", ""),
            )
            for p in proposals
        ]
        self.assertEqual(
            [f["id"] for f in singles],
            [r["id"] for r in batched["results"]],
        )
        for single, entry in zip(singles, batched["results"]):
            stored = next(
                f for f in self.case.data["facts"] if f["id"] == entry["id"]
            )
            for key in ("item", "document_id", "page", "quote", "claim",
                        "stance", "basis", "fidelity"):
                self.assertEqual(single[key], stored[key])

    def test_over_cap_empty_and_malformed_batches_refused(self) -> None:
        good = proposal(quote=self.report_text[:40])
        with self.assertRaises(CaseError):
            self.case.record_facts([dict(good) for _ in range(5)])
        with self.assertRaises(CaseError):
            self.case.record_facts([])
        with self.assertRaises(CaseError):
            self.case.record_facts("not a list")
        self.assertEqual([], self.case.data["facts"])
        # A non-object entry fails only itself.
        result = self.case.record_facts([dict(good), "oops"])
        self.assertEqual(1, result["accepted"])
        self.assertEqual(1, result["errors"])
        self.assertIn("must be an object", result["results"][1]["error"])

    def test_retry_is_idempotent_with_stable_op_ids(self) -> None:
        proposals = [
            proposal(quote=self.report_text[:40]),
            proposal(quote=self.report_text[8:48]),
        ]
        first = self.case.record_facts([dict(p) for p in proposals])
        second = self.case.record_facts([dict(p) for p in proposals])
        self.assertEqual(
            [r["id"] for r in first["results"]],
            [r["id"] for r in second["results"]],
        )
        self.assertEqual(
            [r["op"] for r in first["results"]],
            [r["op"] for r in second["results"]],
        )
        self.assertEqual(2, len(self.case.data["facts"]))
        self.assertEqual(2, second["accepted"])

    def test_supersede_links_to_an_earlier_batch(self) -> None:
        first = self.case.record_facts(
            [proposal(quote=self.report_text[:40])]
        )
        target = first["results"][0]["id"]
        result = self.case.record_facts(
            [
                proposal(
                    quote=self.report_text[8:48], supersedes=target
                ),
                proposal(
                    quote=self.report_text[16:56], supersedes="F99"
                ),
            ]
        )
        self.assertTrue(result["results"][0]["ok"])
        stored = next(
            f for f in self.case.data["facts"]
            if f["id"] == result["results"][0]["id"]
        )
        self.assertEqual(target, stored["supersedes"])
        self.assertFalse(result["results"][1]["ok"])
        self.assertIn("no live fact", result["results"][1]["error"])

    def test_case_cap_fails_only_entries_past_the_cap(self) -> None:
        self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, self.report_text[:40], "c"
        )
        with mock.patch.object(case_record, "MAX_FACTS_PER_CASE", 2):
            result = self.case.record_facts(
                [
                    proposal(quote=self.report_text[8:48]),
                    proposal(quote=self.report_text[16:56]),
                ]
            )
        self.assertEqual(1, result["accepted"])
        self.assertEqual(1, result["errors"])
        self.assertTrue(result["results"][0]["ok"])
        self.assertIn("the most it takes", result["results"][1]["error"])
        self.assertEqual(2, len(self.case.data["facts"]))

    def test_nudge_counter_increments_and_resets(self) -> None:
        self.assertEqual(
            0, self.case.data.get("facts_since_assessment", 0)
        )
        self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, self.report_text[:40], "c"
        )
        self.case.record_facts(
            [
                proposal(quote=self.report_text[8:48]),
                proposal(quote="an invented quote that is nowhere"),
            ]
        )
        self.assertEqual(2, self.case.data["facts_since_assessment"])
        self.case.record_memo_check("claims", [], outcome="passed")
        self.assertEqual(0, self.case.data["facts_since_assessment"])
        self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, self.report_text[16:56], "c"
        )
        # Unavailable assessment leaves the evidence unassessed; memo
        # rules are not a claims assessment. Neither resets.
        self.case.record_memo_check("claims", ["x"], outcome="unavailable")
        self.case.record_memo_check("rules", [])
        self.assertEqual(1, self.case.data["facts_since_assessment"])
        self.case.record_memo_check("claims", ["x"], outcome="failed")
        self.assertEqual(0, self.case.data["facts_since_assessment"])

    def test_tool_path_returns_compact_result(self) -> None:
        result = tools.run(
            self.client,
            self.root,
            self.run_id,
            call(
                "record_facts",
                {
                    "facts": [
                        proposal(quote=self.report_text[:40]),
                        proposal(quote="an invented quote that is nowhere"),
                    ]
                },
            ),
        )
        recorded = result["recorded"]
        self.assertEqual(1, recorded["accepted"])
        self.assertEqual(1, recorded["errors"])
        ok_entry = recorded["results"][0]
        self.assertEqual({"index", "ok", "op", "id"}, set(ok_entry))
        # Every returned id resolves to full evidence in the record.
        stored = next(
            f for f in CaseRecord(self.root, self.run_id).data["facts"]
            if f["id"] == ok_entry["id"]
        )
        self.assertEqual(self.report_text[:40], stored["quote"])
        # A malformed batch is a correctable tool error, not a crash.
        error = tools.run(
            self.client,
            self.root,
            self.run_id,
            call("record_facts", {"facts": []}),
        )
        self.assertIn("non-empty list", error["error"])


if __name__ == "__main__":
    unittest.main()
