"""Graph-ranking experiment: determinism, schema, and decision pins.

The experiment (eval/graph_cases.py + graph_run.py) compares
discovery methods over synthetic citation networks. These tests
pin the run mechanics plus the measured facts the Phase C
adoption decision rests on: fused 8/8 with zero false-tops,
text blind to citation-only discovery, and each ablation
losing exactly the scenarios its signal owns. If fixtures or
methods change, these pins force a conscious re-examination
of the decision — they are decision evidence, not wallpaper.
"""

from __future__ import annotations

import unittest

from eval import graph_run


class GraphExperimentTest(unittest.TestCase):
    def test_deterministic_and_schematized(self) -> None:
        first = graph_run.run_all()
        second = graph_run.run_all()
        self.assertEqual(first, second)
        self.assertTrue(first)
        for report in first:
            self.assertEqual(
                graph_run.REPORT_KEYS, set(report),
                f"report schema drift: {report['case']}/{report['method']}",
            )

    def test_fused_passes_all_with_no_false_tops(self) -> None:
        methods = graph_run.summarize(graph_run.run_all())["methods"]
        fused = methods["fused"]
        self.assertEqual((8, 8), (fused["complete"], fused["total"]))
        self.assertEqual(0, fused["false_tops"])
        self.assertEqual(
            (1, 1), (fused["adverse_ok"], fused["adverse_total"])
        )

    def test_text_blind_to_citation_only(self) -> None:
        reports = graph_run.run_all()
        text_citation = [
            r for r in reports
            if r["case"] == "citation-only-discovery"
            and r["method"] == "text"
        ]
        (report,) = text_citation
        self.assertFalse(report["complete"])
        self.assertNotIn("L", report["ranked"])

    def test_ablations_isolate_each_signal(self) -> None:
        methods = graph_run.summarize(graph_run.run_all())["methods"]
        # Graph owns citation-only discovery and nothing else.
        no_graph = methods["fused_no_graph"]
        self.assertEqual((7, 8), (no_graph["complete"], no_graph["total"]))
        self.assertEqual(0, no_graph["false_tops"])
        # Filters own treatment, currency, and jurisdiction.
        no_filter = methods["fused_no_filter"]
        self.assertEqual(3, no_filter["false_tops"])
        self.assertEqual(0, no_filter["adverse_ok"])
        # Text owns seeding, pool recall, and the isolate backstop.
        no_text = methods["fused_no_text"]
        self.assertEqual((4, 8), (no_text["complete"], no_text["total"]))

    def test_pure_graph_fails_legal_dimensions(self) -> None:
        methods = graph_run.summarize(graph_run.run_all())["methods"]
        for method in ("indegree", "pagerank", "ppr", "hits"):
            with self.subTest(method=method):
                self.assertEqual(0, methods[method]["adverse_ok"])
                self.assertGreaterEqual(
                    methods[method]["false_tops"], 3
                )

    def test_split_reporting_balanced_and_disclosed(self) -> None:
        summary = graph_run.summarize(graph_run.run_all())
        for method, stats in summary["methods"].items():
            with self.subTest(method=method):
                self.assertEqual(
                    (4, 4),
                    (stats["by_split"]["dev"]["total"],
                     stats["by_split"]["heldout"]["total"]),
                    method,
                )
        fused = summary["methods"]["fused"]["by_split"]
        self.assertEqual((4, 4), (fused["heldout"]["complete"],
                                 fused["heldout"]["total"]))
        self.assertIn("post-hoc split", summary["verdict"])


if __name__ == "__main__":
    unittest.main()
