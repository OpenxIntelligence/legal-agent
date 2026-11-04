"""Increment 5: priority rubric, discovery ranking, corpus search.

Mechanical tests only: selection order, factor accounting,
deterministic ranking, coverage quotas, scope filtering, and
search grouping. None of this measures retrieval or reasoning
quality — that needs the held-out live evaluation, which this
brief does not authorize.
"""

from __future__ import annotations

import unittest

from agent_kit import discovery, document_text, scheduler, tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from tests.test_kit import call

ITEMS = [
    {"id": "alpha", "question": "Was alpha true?", "evidence_needed": "a record of alpha"},
    {"id": "beta", "question": "Was beta true?", "evidence_needed": "a record of beta"},
]


class RankingCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)

    def read(self, *ids: str) -> None:
        analyze(self.fake.client, self.root, list(ids), "x")

    def page(self, document_id: str) -> str:
        return support.INITIAL[document_id][0]


class RubricTest(RankingCase):
    def test_contested_outranks_lower_seq_fresh(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-MINUTES")
        text = self.page("DOC-MINUTES")
        self.case.record_fact("beta", "DOC-MINUTES", 1, text[:40], "c")
        self.case.record_fact(
            "beta", "DOC-MINUTES", 1, text[:40], "not c", stance="opposes"
        )
        picked = scheduler.select_next(self.case)
        self.assertEqual("T2", picked["task"])
        self.assertEqual(1, picked["factors"]["uncertainty"])
        self.assertIn("contested", picked["reason"])

    def test_unresolved_branch_adds_materiality(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.open_branch("beta", "ca", "disputed", "clause text unclear")
        picked = scheduler.select_next(self.case)
        self.assertEqual("T2", picked["task"])
        self.assertEqual(2, picked["factors"]["materiality"])
        self.assertIn("unresolved legal branch", picked["reason"])

    def test_resolved_branch_counts_less(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.open_branch("beta", "ca", "unknown", "performance in-state")
        self.case.resolve_branch("B1", "known", "counsel confirmed")
        task = self.case.task("T2")
        scored = scheduler.score_task(self.case, task)
        self.assertEqual(1, scored["factors"]["materiality"])

    def test_unblocking_names_dependents(self) -> None:
        items = ITEMS + [
            {
                "id": "gamma",
                "question": "Was gamma true?",
                "evidence_needed": "a record of gamma",
            }
        ]
        self.case.set_checklist(items)
        self.case.add_dependency("T2", "T1")
        picked = scheduler.select_next(self.case)
        self.assertEqual("T1", picked["task"])
        self.assertEqual(1, picked["factors"]["unblocking"])
        self.assertIn("unblocks T2", picked["reason"])

    def test_repeated_attempts_deprioritized(self) -> None:
        self.case.set_checklist(ITEMS)
        for _ in range(2):
            self.case.begin_attempt("T1", "worker")
            self.case.commit_attempt("T1", status="partial", summary="no luck")
        picked = scheduler.select_next(self.case)
        self.assertEqual("T2", picked["task"])
        scored = scheduler.score_task(self.case, self.case.task("T1"))
        self.assertEqual(-1, scored["factors"]["freshness"])

    def test_eligibility_beats_score(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = self.page("DOC-REPORT")
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.record_fact(
            "alpha", "DOC-REPORT", 1, text[:40], "not c", stance="opposes"
        )
        self.case.open_branch("alpha", "ca", "disputed", "clause text unclear")
        self.case.open_request("alpha", "the alpha record", "need", ["d1"])
        self.assertEqual("blocked", self.case.task("T1")["status"])
        picked = scheduler.select_next(self.case)
        self.assertEqual("T2", picked["task"])

    def test_stale_scores_uncertainty_two(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MINUTES")
        report = self.page("DOC-REPORT")
        minutes = self.page("DOC-MINUTES")
        self.case.record_fact("alpha", "DOC-REPORT", 1, report[:40], "c")
        self.case.record_fact("beta", "DOC-MINUTES", 1, minutes[:40], "c")
        self.case.mark_answered(["alpha", "beta"])
        self.case.record_fact("alpha", "DOC-REPORT", 1, report[8:48], "c2")
        picked = scheduler.select_next(self.case)
        self.assertEqual("T1", picked["task"])
        self.assertEqual(2, picked["factors"]["uncertainty"])
        self.assertIn("reassess", picked["reason"])

    def test_tie_breaks_on_task_order(self) -> None:
        self.case.set_checklist(ITEMS)
        first = scheduler.score_task(self.case, self.case.task("T1"))
        second = scheduler.score_task(self.case, self.case.task("T2"))
        self.assertEqual(first["score"], second["score"])
        picked = scheduler.select_next(self.case)
        self.assertEqual("T1", picked["task"])

    def test_selection_carries_factors_and_score(self) -> None:
        self.case.set_checklist(ITEMS)
        picked = scheduler.select_next(self.case)
        self.assertEqual(
            {"materiality", "uncertainty", "unblocking", "freshness"},
            set(picked["factors"]),
        )
        self.assertIn("score", picked["reason"])


class RankTest(RankingCase):
    GRAPH = {
        "nodes": {
            "issue:a": {"kind": "issue"},
            "issue:b": {"kind": "issue"},
            "fact:1": {"kind": "fact"},
            "doc:X": {"kind": "document"},
            "doc:Y": {"kind": "document"},
        },
        "edges": [
            ("fact:1", "issue:a", "supports"),
            ("fact:1", "doc:X", "extracted_from"),
        ],
    }

    def test_connected_outranks_isolated_for_seed(self) -> None:
        scores = discovery.rank(self.GRAPH, ["issue:a"])
        self.assertGreater(scores["doc:X"], scores["doc:Y"])
        self.assertGreater(scores["fact:1"], scores["issue:b"])

    def test_rank_is_deterministic(self) -> None:
        self.assertEqual(
            discovery.rank(self.GRAPH, ["issue:a"]),
            discovery.rank(self.GRAPH, ["issue:a"]),
        )

    def test_unknown_seeds_and_empty_graph(self) -> None:
        scores = discovery.rank(self.GRAPH, ["issue:nope"])
        self.assertTrue(all(value == 0.0 for value in scores.values()))
        self.assertEqual({}, discovery.rank({"nodes": {}, "edges": []}, ["issue:a"]))

    def test_dangling_seed_does_not_feed_disconnected_cluster(self) -> None:
        # An edgeless seed (an issue with no facts yet) holds
        # restart mass with nowhere to go; redistributing that
        # mass uniformly would top up an unrelated cluster the
        # seed cannot reach. Dangling mass must restart, not leak.
        graph = {
            "nodes": {
                "issue:s": {"kind": "issue"},
                "issue:a": {"kind": "issue"},
                "fact:1": {"kind": "fact"},
                "doc:X": {"kind": "document"},
            },
            "edges": [
                ("fact:1", "issue:a", "supports"),
                ("fact:1", "doc:X", "extracted_from"),
            ],
        }
        scores = discovery.rank(graph, ["issue:s"])
        for node in ("issue:a", "fact:1", "doc:X"):
            self.assertLess(scores[node], 0.05, node)
        self.assertGreater(scores["issue:s"], 0.9)

    def test_build_graph_types_opposition(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = self.page("DOC-REPORT")
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.record_fact(
            "alpha", "DOC-REPORT", 1, text[:40], "not c", stance="opposes"
        )
        self.case.open_branch("alpha", "ca", "unknown", "performance in-state")
        graph = discovery.build_graph(self.case)
        relations = {(src, dst, rel) for src, dst, rel in graph["edges"]}
        self.assertTrue(any(rel == "opposes" for _s, _d, rel in relations))
        self.assertIn(("issue:alpha", "branch:B1", "applicability"), relations)
        self.assertIn("doc:DOC-REPORT", graph["nodes"])


class SuggestTest(RankingCase):
    def test_suggestions_rank_and_cover(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = self.page("DOC-REPORT")
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        picks = discovery.suggest_inspections(self.case, "alpha", limit=2)
        by_id = {row["id"]: row for row in picks}
        self.assertIn("DOC-REPORT", by_id)
        unexamined = [row for row in picks if not row["examined"]]
        self.assertGreaterEqual(len(unexamined), 2)
        self.assertTrue(all("coverage pick" in row["reason"] for row in unexamined))
        self.assertTrue(
            all("discovery only" in row["reason"] for row in picks)
        )

    def test_scope_filter_excludes_other_region(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MEMO")
        self.case.note_source(
            "DOC-REPORT", "ca-leginfo", "Cal. Civ. Code § 1542", "2024 session"
        )
        self.case.note_source(
            "DOC-MEMO", "ew-legislation", "Consumer Rights Act 2015, s 11",
            "point-in-time 2024-01-01",
        )
        picks = discovery.suggest_inspections(self.case, "alpha", scope="California")
        ids = [row["id"] for row in picks]
        self.assertIn("DOC-REPORT", ids)
        self.assertNotIn("DOC-MEMO", ids)
        self.assertIn("DOC-MINUTES", ids)

    def test_unclear_scope_filters_nothing(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MEMO")
        self.case.note_source(
            "DOC-REPORT", "ca-leginfo", "Cal. Civ. Code § 1542", "2024 session"
        )
        self.case.note_source(
            "DOC-MEMO", "ew-legislation", "Consumer Rights Act 2015, s 11",
            "point-in-time 2024-01-01",
        )
        picks = discovery.suggest_inspections(self.case, "alpha", scope="UK")
        ids = [row["id"] for row in picks]
        self.assertIn("DOC-REPORT", ids)
        self.assertIn("DOC-MEMO", ids)

    def test_briefing_carries_suggestions(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = self.page("DOC-REPORT")
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        briefing = scheduler.build_task_context(self.case, "T1")
        self.assertTrue(briefing["suggested_inspections"])
        self.assertTrue(
            all("discovery only" in row["reason"] for row in briefing["suggested_inspections"])
        )


class SearchTest(RankingCase):
    def test_finds_planted_phrase_grouped(self) -> None:
        self.read("DOC-REPORT")
        found = document_text.search_corpus(self.root, "severity 1")
        self.assertEqual("severity 1", found["query"])
        self.assertEqual(1, len(found["documents"]))
        group = found["documents"][0]
        self.assertEqual("DOC-REPORT", group["document_id"])
        self.assertEqual(1, group["passages"][0]["page"])
        self.assertGreaterEqual(group["passages"][0]["matches_on_page"], 1)
        self.assertIn("Severity 1", group["passages"][0]["passage"])
        self.assertFalse(found["truncated"])

    def test_unindexed_and_restricted_listed(self) -> None:
        self.read("DOC-REPORT")
        self.case.flag_privileged("DOC-MEMO", "counsel email")
        found = document_text.search_corpus(
            self.root, "the", flagged={"DOC-MEMO"}
        )
        self.assertEqual(["DOC-MINUTES", "DOC-REF"], found["unindexed"])
        self.assertEqual(["DOC-MEMO"], found["restricted"])
        via_tool = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("search_corpus", {"query": "the"}),
        )
        self.assertEqual(["DOC-MEMO"], via_tool["restricted"])
        self.assertFalse(
            any(
                d["document_id"] == "DOC-MEMO" for d in via_tool["documents"]
            )
        )

    def test_unknown_doc_and_empty_query_refused(self) -> None:
        with self.assertRaises(document_text.DocumentError):
            document_text.search_corpus(self.root, "alpha", ["DOC-NOPE"])
        with self.assertRaises(document_text.DocumentError):
            document_text.search_corpus(self.root, " ")
        result = tools.run(
            self.fake.client, self.root, self.run_id, call("search_corpus", {"query": " "})
        )
        self.assertIn("two characters", result["error"])

    def test_limit_truncates(self) -> None:
        self.read("DOC-MEMO", "DOC-MINUTES")
        found = document_text.search_corpus(self.root, "the", limit=1)
        self.assertEqual(1, found["passage_count"])
        self.assertTrue(found["truncated"])

    def test_search_is_case_insensitive(self) -> None:
        self.read("DOC-REPORT")
        lower = document_text.search_corpus(self.root, "severity 1")
        upper = document_text.search_corpus(self.root, "SEVERITY 1")
        self.assertEqual(lower["documents"], upper["documents"])

    def test_tool_filters_to_named_documents(self) -> None:
        self.read("DOC-REPORT", "DOC-MINUTES")
        result = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("search_corpus", {"query": "the", "document_ids": ["DOC-MINUTES"]}),
        )
        self.assertEqual(
            ["DOC-MINUTES"], [d["document_id"] for d in result["documents"]]
        )
        self.assertEqual([], result["unindexed"])
        self.assertEqual([], result["restricted"])


if __name__ == "__main__":
    unittest.main()
