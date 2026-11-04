"""Retrieval baseline floors (Item 4, G4).

The eval set (eval/retrieval_cases.py + retrieval_run.py) pins
keyword-search behavior over the tiny case: normalized variants
must hit, absent content must stay silent, restricted/unindexed
docs must be listed rather than searched, and paraphrases must
hit via the BM25 fallback. This test asserts the floors; the
runner stays standalone (not in the six-benchmark regression
gate).
"""

from __future__ import annotations

import unittest

from eval import retrieval_run


class RetrievalBaselineTest(unittest.TestCase):
    def test_floors_hold_and_paraphrase_gated(self) -> None:
        reports = retrieval_run.run_all()
        summary = retrieval_run.summarize(reports)
        self.assertEqual(
            summary["must_hit"]["total"], summary["must_hit"]["hit"],
            summary["verdict"],
        )
        self.assertEqual(0, summary["precision_fp"]["hit"], summary["verdict"])
        self.assertEqual(
            summary["paraphrase"]["total"], summary["paraphrase"]["hit"],
            summary["verdict"],
        )
        for report in reports:
            self.assertEqual(
                retrieval_run.REPORT_KEYS, set(report),
                f"report schema drift: {report['case']}",
            )


if __name__ == "__main__":
    unittest.main()
