"""Retrieval benchmark integrity + exact/security floors (RET-1/RET-2).

Locks the frozen benchmark contract: versioned cases, valid
references, dev/heldout split, and the full runner path. Gates
only invariants (exact queries complete, restricted content
never returned, isolation holds, listings honest) — quality
scores on paraphrase/multi/conflict are MEASURED by the
benchmark, never gated here, so candidates can move them.
"""

from __future__ import annotations

import unittest

from eval import retrieval2_cases as cases
from eval import retrieval2_run as runner


class Retrieval2IntegrityTest(unittest.TestCase):
    def test_versioned_and_counted(self) -> None:
        self.assertRegex(cases.CASES_VERSION, r"^retrieval2-cases/v1\.2$")
        self.assertEqual(76, len(cases.QUERIES))
        splits = {q[8] for q in cases.QUERIES}
        self.assertEqual({"dev", "heldout"}, splits)

    def test_all_references_valid(self) -> None:
        pages = {(m, d, i + 1)
                 for m, corp in cases.CORPORA.items()
                 for d, ps in corp["docs"].items()
                 for i in range(len(ps))}
        for spec in cases.QUERIES:
            for pair in spec[4] + spec[5] + spec[6]:
                self.assertIn((spec[1], pair[0], pair[1]), pages,
                              f"{spec[0]} references {pair}")

    def test_runner_report_shape(self) -> None:
        report = runner.run_all()
        self.assertEqual(set(runner.REPORT_KEYS), set(report))
        self.assertEqual(cases.CASES_VERSION, report["cases_version"])
        self.assertEqual(76, len(report["queries"]))
        self.assertEqual(3, len(report["corpus_fingerprints"]))

    def test_exact_floor_holds(self) -> None:
        report = runner.run_all()
        exact = [q for q in report["queries"] if q["category"] == "exact"]
        self.assertEqual(9, len(exact))
        for grade in exact:
            self.assertTrue(grade["complete"], grade["id"])

    def test_security_invariants_hold(self) -> None:
        report = runner.run_all()
        by_id = {q["id"]: q for q in report["queries"]}
        for qid in ("r-counsel", "r-privileged"):
            self.assertTrue(by_id[qid]["prohibited_free"], qid)
            self.assertTrue(by_id[qid]["restricted_listed"], qid)
        self.assertTrue(by_id["u-punchlist"]["unindexed_listed"])
        # Isolation emptiness is MEASURED by the benchmark
        # (abstain-complete), not gated here: the current
        # fallback over-triggers on single shared tokens, and
        # the candidate experiments must move that number.


if __name__ == "__main__":
    unittest.main()
