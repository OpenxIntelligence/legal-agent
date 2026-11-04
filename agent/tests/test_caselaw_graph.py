"""CourtListener citation-graph adapter: id parsing, edge hygiene, rank seam.

All records are hand-built fixture dicts shaped like future
API payloads (see the module docstring for what is verified
versus assumed). No network, no token.
"""

from __future__ import annotations

import unittest

from agent_kit import caselaw_graph, pagerank


class AdapterTest(unittest.TestCase):
    def test_ids_from_ints_strings_and_urls(self) -> None:
        report = caselaw_graph.cited_edges([
            {"id": 7, "cites": [8, "9", "https://example/api/opinions/10/"]},
        ])
        self.assertEqual(
            {"7": ["10", "8", "9"], "10": [], "8": [], "9": []},
            report["edges"],
        )
        self.assertEqual([], report["unresolved"])

    def test_self_cites_drop_and_bad_refs_report(self) -> None:
        report = caselaw_graph.cited_edges([
            {"id": "5", "cites": ["5", "nope", -3, None, True]},
        ])
        self.assertEqual({"5": []}, report["edges"])
        self.assertEqual(1, report["dropped_self"])
        self.assertEqual(4, len(report["unresolved"]))
        self.assertEqual("5", report["unresolved"][0]["citing"])

    def test_pairs_dedupe_and_sort(self) -> None:
        report = caselaw_graph.cited_edges([
            {"id": 2, "cites": [3, 3, 1]},
            {"id": 3, "cites": [1]},
            "junk",
            {"no": "id"},
        ])
        self.assertEqual(
            {"1": [], "2": ["1", "3"], "3": ["1"]},
            report["edges"],
        )
        self.assertEqual(["2", "3"], report["nodes"])

    def test_empty_in_empty_out(self) -> None:
        report = caselaw_graph.cited_edges([])
        self.assertEqual(
            {"edges": {}, "nodes": [], "unresolved": [],
             "dropped_self": 0},
            report,
        )

    def test_feeds_pagerank_for_discovery(self) -> None:
        # B and C both cite landmark A; seeded on B, the walk
        # must surface A (discovery), not treat it as binding.
        report = caselaw_graph.cited_edges([
            {"id": 11, "cites": [13]},
            {"id": 12, "cites": [13]},
            {"id": 13, "cites": []},
        ])
        scores = pagerank.personalized(report["edges"], ["11"])
        self.assertGreater(scores["13"], scores["12"])


if __name__ == "__main__":
    unittest.main()
