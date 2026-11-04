"""Increment 4: jurisdiction-aware research and synthesis.

Source registry routing, legal branches, claim kinds, supplied-source
provenance, memo branch references with kind matching and dependency
coverage, and the research query journal. Retrieval is operator-fed;
no live fetching exists. All fixtures synthetic.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agent_kit import sources, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.sources import REGIONS, REPOS, normalize_scope, route
from agent_kit.tools.analyze_document import analyze
from tests import support
from tests.test_kit import call
from verification import verify_memo

ITEMS = [
    {"id": "alpha", "question": "Was alpha true?", "evidence_needed": "a record of alpha"},
    {"id": "beta", "question": "Was beta true?", "evidence_needed": "a record of beta"},
]


class JurisdictionCase(unittest.TestCase):
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

    def fresh(self) -> CaseRecord:
        return CaseRecord(self.root, self.run_id)

    def ctx(self):
        return tools.Context(self.fake.client, self.root, self.run_id, self.case, True)

    def quote(self, doc: str, start: int = 0, end: int = 40) -> str:
        return support.INITIAL[doc][0][start:end]


class ScopeRoutingTest(unittest.TestCase):
    def test_scopes_resolve_clarify_or_unknown(self) -> None:
        self.assertEqual("ew", normalize_scope("England and Wales")["region"])
        self.assertEqual("ew", normalize_scope("england")["region"])
        self.assertEqual("ca", normalize_scope("California")["region"])
        uk = normalize_scope("UK")
        self.assertIn("England and Wales", uk["clarify"])
        self.assertIn("Scotland", uk["clarify"])
        self.assertNotIn("region", uk)
        us = normalize_scope("United States")
        self.assertIn("the federal regime", us["clarify"])
        self.assertEqual("Atlantis", normalize_scope("Atlantis")["unknown"])

    def test_no_location_default_exists(self) -> None:
        for scope, options in sources.SCOPE_CLARIFICATIONS.items():
            self.assertGreaterEqual(
                len(options), 2, f"{scope} must offer a choice, not a default"
            )
            self.assertNotIn("region", normalize_scope(scope))

    def test_california_vs_florida_changes_routing(self) -> None:
        california = route("rule_exception", {"regime": "ca"})
        self.assertEqual(["ca-leginfo", "ca-opinions"], california["repos"])
        self.assertTrue(california["supported"])
        florida = route("rule_exception", {"regime": "us-fl"})
        self.assertEqual([], florida["repos"])
        self.assertFalse(florida["supported"])
        self.assertIn("request the text", florida["steps"][0])

    def test_federal_forum_warns_without_implying_substance(self) -> None:
        plan = route(
            "applicable_law", {"scope": "US federal", "connecting": True}
        )
        self.assertFalse(plan["blocked"])
        self.assertTrue(any("1652" in w for w in plan["warnings"]))
        federal = route("rule_exception", {"regime": "us-federal"})
        self.assertEqual(["uscode"], federal["repos"])
        self.assertIn("state", REGIONS["us-federal"]["note"])

    def test_governing_clause_recorded_not_decided(self) -> None:
        for query_class in ("applicable_law", "rule_exception"):
            plan = route(
                query_class,
                {"regime": "ca", "connecting": True, "clause": True},
            )
            self.assertTrue(
                any("validity and scope" in w for w in plan["warnings"]),
                query_class,
            )
        plain = route("rule_exception", {"regime": "ca"})
        self.assertFalse([w for w in plain["warnings"] if "validity" in w])

    def test_consumer_status_changes_the_plan(self) -> None:
        consumer = route(
            "rule_exception", {"regime": "ca", "transaction": "consumer"}
        )
        self.assertTrue(any("consumer" in w.lower() for w in consumer["warnings"]))
        commercial = route(
            "rule_exception", {"regime": "ca", "transaction": "commercial"}
        )
        self.assertFalse(commercial["warnings"])
        absent = route("rule_exception", {"regime": "ca"})
        self.assertFalse(absent["warnings"])

    def test_historical_versions_are_explicit(self) -> None:
        dated = route("rule_exception", {"regime": "ca", "events_date": "2021-03-04"})
        self.assertTrue(any("2021-03-04" in s for s in dated["steps"]))
        undated = route("rule_exception", {"regime": "ca"})
        self.assertTrue(any("event date" in s for s in undated["steps"]))
        with self.assertRaises(ValueError):
            route("rule_exception", {"regime": "ca", "events_date": "March 2021"})

    def test_stub_regions_keep_their_qualifications_visible(self) -> None:
        france = route("rule_exception", {"regime": "fr"})
        self.assertFalse(france["supported"])
        self.assertTrue(any("French" in w for w in france["warnings"]))
        saudi = route("rule_exception", {"regime": "sa"})
        self.assertTrue(any("Arabic" in w for w in saudi["warnings"]))
        self.assertTrue(any("Hijri" in w for w in saudi["warnings"]))

    def test_facts_only_work_needs_no_legal_context(self) -> None:
        for query_class in (
            "exact_lookup",
            "local_factual",
            "corpus_pattern",
            "chronology",
            "counterevidence",
        ):
            plan = route(query_class, {})
            self.assertFalse(plan["blocked"], query_class)
            self.assertEqual([], plan["needs"])

    def test_applicable_law_needs_connecting_facts(self) -> None:
        bare = route("applicable_law", {})
        self.assertTrue(bare["blocked"])
        self.assertTrue(any("connecting" in n for n in bare["needs"]))
        grounded = route("applicable_law", {"connecting": True})
        self.assertFalse(grounded["blocked"])

    def test_rule_exception_needs_a_regime(self) -> None:
        self.assertTrue(route("rule_exception", {})["blocked"])
        unknown = route("rule_exception", {"regime": "atlantis"})
        self.assertTrue(unknown["blocked"])
        self.assertTrue(any("atlantis" in n for n in unknown["needs"]))

    def test_ambiguous_scope_blocks_registry_classes(self) -> None:
        plan = route("rule_exception", {"regime": "ca", "scope": "UK"})
        self.assertTrue(plan["blocked"])
        self.assertTrue(any("England and Wales" in n for n in plan["needs"]))
        local = route("local_factual", {"scope": "UK"})
        self.assertFalse(local["blocked"])


class BranchTest(JurisdictionCase):
    def test_open_and_resolve_branch(self) -> None:
        self.case.set_checklist(ITEMS)
        branch = self.case.open_branch(
            "alpha", "ca", "unknown", "performance in Los Angeles"
        )
        self.assertEqual("B1", branch["id"])
        self.assertEqual("California", branch["regime_label"])
        self.assertEqual(1, self.case.branch_version())
        resolved = self.case.resolve_branch("B1", "known", "counsel confirmed")
        self.assertEqual("known", resolved["status"])
        self.assertEqual(2, len(resolved["history"]))
        self.assertEqual(2, self.case.branch_version())
        with self.assertRaises(CaseError):
            self.case.resolve_branch("B1", "known", "again")
        with self.assertRaises(CaseError):
            self.case.resolve_branch("B9", "known", "x")

    def test_branch_validation(self) -> None:
        self.case.set_checklist(ITEMS)
        with self.assertRaises(CaseError):
            self.case.open_branch("nope", "ca", "unknown", "x")
        with self.assertRaises(CaseError):
            self.case.open_branch("alpha", "atlantis", "unknown", "x")
        with self.assertRaises(CaseError):
            self.case.open_branch("alpha", "ca", "guessed", "x")
        with self.assertRaises(CaseError):
            self.case.open_branch("alpha", "ca", "unknown", "")
        with self.assertRaises(CaseError):
            self.case.open_branch("alpha", "ca", "unknown", "x", basis_kind="vibes")
        self.assertEqual(0, self.case.branch_version())

    def test_branches_stale_their_issue_tasks(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        self.case.record_fact("alpha", "DOC-REPORT", 1, self.quote("DOC-REPORT"), "c")
        self.case.mark_answered(["alpha"])
        self.case.open_branch("alpha", "ca", "unknown", "performance in LA")
        self.assertEqual("stale", self.fresh().task("T1")["status"])
        self.assertEqual("open", self.fresh().task("T2")["status"])
        self.case.mark_answered(["alpha"])
        self.assertEqual("completed", self.fresh().task("T1")["status"])
        self.case.resolve_branch("B1", "known", "confirmed")
        self.assertEqual("stale", self.fresh().task("T1")["status"])

    def test_branch_tools(self) -> None:
        self.case.set_checklist(ITEMS)
        opened = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call(
                "open_branch",
                {"issue": "alpha", "regime": "ca", "basis": "performance in LA"},
            ),
        )
        self.assertEqual("B1", opened["opened"]["id"])
        self.assertEqual("unknown", opened["opened"]["status"])
        resolved = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("resolve_branch", {"branch": "B1", "status": "known", "note": "ok"}),
        )
        self.assertEqual("known", resolved["resolved"]["status"])
        error = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("open_branch", {"issue": "alpha", "regime": "ca", "basis": ""}),
        )
        self.assertIn("basis", error["error"])


class KindTest(JurisdictionCase):
    def test_kinds_are_distinct_assertions(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        quote = self.quote("DOC-REPORT")
        first = self.case.record_fact("alpha", "DOC-REPORT", 1, quote, "c")
        second = self.case.record_fact(
            "alpha", "DOC-REPORT", 1, quote, "c", kind="legal_rule"
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual("legal_rule", second["kind"])
        retry = self.case.record_fact(
            "alpha", "DOC-REPORT", 1, quote, "c", kind="legal_rule"
        )
        self.assertEqual(second["id"], retry["id"])
        with self.assertRaises(CaseError):
            self.case.record_fact("alpha", "DOC-REPORT", 1, quote, "c", kind="hunch")

    def test_batch_entries_carry_kind(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        result = self.case.record_facts(
            [
                {
                    "item": "alpha",
                    "document_id": "DOC-REPORT",
                    "page": 1,
                    "quote": text[:40],
                    "claim": "c",
                    "kind": "legal_rule",
                },
                {
                    "item": "alpha",
                    "document_id": "DOC-REPORT",
                    "page": 1,
                    "quote": text[8:48],
                    "claim": "c",
                    "kind": "hunch",
                },
            ]
        )
        self.assertTrue(result["results"][0]["ok"])
        self.assertFalse(result["results"][1]["ok"])
        self.assertIn("kind", result["results"][1]["error"])


class ProvenanceTest(JurisdictionCase):
    def test_note_source(self) -> None:
        self.read("DOC-REPORT")
        noted = self.case.note_source(
            "DOC-REPORT", "ca-leginfo", "Cal. Civ. Code § 1542", "2024 session"
        )
        self.assertEqual("ca-leginfo", noted["repo"])
        self.assertEqual("model", noted["asserted_by"])
        with self.assertRaises(CaseError):
            self.case.note_source("DOC-MINUTES", "ca-leginfo", "c", "v")
        with self.assertRaises(CaseError):
            self.case.note_source("DOC-NOPE", "ca-leginfo", "c", "v")
        with self.assertRaises(CaseError):
            self.case.note_source("DOC-REPORT", "nope", "c", "v")
        with self.assertRaises(CaseError):
            self.case.note_source("DOC-REPORT", "ca-leginfo", "", "v")
        with self.assertRaises(CaseError):
            self.case.note_source("DOC-REPORT", "ca-leginfo", "c", "")

    def test_language_rule_for_non_english_originals(self) -> None:
        self.read("DOC-REPORT")
        fake_repos = dict(REPOS)
        fake_repos["xx-test"] = dict(REPOS["ca-leginfo"])
        fake_repos["xx-test"]["languages"] = ["French"]
        with mock.patch.object(sources, "REPOS", fake_repos):
            problems = sources.validate_provenance("xx-test", "c", "v", "")
            self.assertTrue(any("language" in p for p in problems))
            self.assertEqual([], sources.validate_provenance("xx-test", "c", "v", "French"))

    def test_note_source_tool(self) -> None:
        self.read("DOC-REPORT")
        result = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call(
                "note_source",
                {
                    "document_id": "DOC-REPORT",
                    "repo": "ca-leginfo",
                    "citation": "Cal. Civ. Code § 1542",
                    "version": "2024 session",
                },
            ),
        )
        self.assertEqual("DOC-REPORT", result["noted"]["document_id"])


class MemoBranchTest(JurisdictionCase):
    def setUp(self) -> None:
        super().setUp()
        self.case.set_checklist(ITEMS[:1])
        self.read("DOC-REPORT")
        self.text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, self.text[:40], "c")

    def memo(self, **overrides):
        memo = {
            "conclusion": "Yes.",
            "gaps": "",
            "findings": [
                {
                    "item": "alpha",
                    "status": "asserted",
                    "summary": "alpha holds",
                    "citations": [
                        {
                            "document_id": "DOC-REPORT",
                            "page": 1,
                            "quote": self.text[:40],
                        }
                    ],
                }
            ],
        }
        memo.update(overrides)
        return memo

    def test_pure_fact_memo_unaffected(self) -> None:
        result = verify_memo.run_verification(self.ctx(), self.memo())
        self.assertTrue(result["passed"])
        self.assertEqual([], self.case.memo_branch_conditions(self.memo()))

    def test_legal_rule_finding_needs_legal_rule_fact(self) -> None:
        memo = self.memo()
        memo["findings"][0]["kind"] = "legal_rule"
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertFalse(result["passed"])
        self.assertTrue(any("legal-rule fact" in p for p in result["feedback"]))
        self.case.record_fact(
            "alpha", "DOC-REPORT", 1, self.text[8:48], "c", kind="legal_rule"
        )
        memo["findings"][0]["citations"] = [
            {"document_id": "DOC-REPORT", "page": 1, "quote": self.text[8:48]}
        ]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(result["passed"])

    def test_fact_finding_needs_fact_kind(self) -> None:
        self.case.record_fact(
            "alpha", "DOC-REPORT", 1, self.text[8:48], "c", kind="legal_rule"
        )
        memo = self.memo()
        memo["findings"][0]["citations"] = [
            {"document_id": "DOC-REPORT", "page": 1, "quote": self.text[8:48]}
        ]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertFalse(result["passed"])
        self.assertTrue(any("fact-kind fact" in p for p in result["feedback"]))

    def test_applicability_finding_needs_a_branch(self) -> None:
        memo = self.memo()
        memo["findings"][0]["kind"] = "applicability"
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertFalse(result["passed"])
        self.assertTrue(any("names no branch" in p for p in result["feedback"]))
        branch = self.case.open_branch("alpha", "ca", "known", "performance in LA")
        memo["findings"][0]["branch"] = branch["id"]
        memo["depends_on"] = [branch["id"]]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(result["passed"])

    def test_branch_refs_validated(self) -> None:
        memo = self.memo()
        memo["findings"][0]["branch"] = "B9"
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(any("unknown branch" in p for p in result["feedback"]))
        self.case.set_checklist(ITEMS)
        branch = self.case.open_branch("beta", "ca", "unknown", "x")
        memo["findings"][0]["branch"] = branch["id"]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(any("which is for beta" in p for p in result["feedback"]))
        memo["findings"][0]["kind"] = "hunch"
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(any("kind 'hunch'" in p for p in result["feedback"]))

    def test_removed_issue_branch_flagged(self) -> None:
        self.case.set_checklist(ITEMS)
        branch = self.case.open_branch("beta", "ca", "unknown", "x")
        self.case.set_checklist(ITEMS[:1])
        memo = self.memo()
        memo["findings"][0]["item"] = "beta"
        memo["findings"][0]["branch"] = branch["id"]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(any("left the checklist" in p for p in result["feedback"]))

    def test_conclusion_keeps_finding_branches(self) -> None:
        branch = self.case.open_branch("alpha", "ca", "unknown", "x")
        memo = self.memo()
        memo["findings"][0]["branch"] = branch["id"]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertFalse(result["passed"])
        self.assertTrue(any("drops B1" in p for p in result["feedback"]))
        memo["depends_on"] = [branch["id"]]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(result["passed"])
        memo["depends_on"] = ["B9"]
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(any("unknown branch" in p for p in result["feedback"]))
        memo["depends_on"] = "B1"
        result = verify_memo.run_verification(self.ctx(), memo)
        self.assertTrue(any("depends_on must be" in p for p in result["feedback"]))

    def test_filed_memo_carries_conditions(self) -> None:
        branch = self.case.open_branch("alpha", "ca", "disputed", "clause §9")
        memo = self.memo()
        memo["findings"][0]["branch"] = branch["id"]
        memo["depends_on"] = [branch["id"]]
        filed = self.case.file_memo(memo, "awaiting_signoff")
        self.assertEqual(["B1 (California): disputed"], filed["conditions"])
        self.assertEqual("disputed", filed["branch_states"]["B1"]["status"])
        self.case.resolve_branch("B1", "known", "confirmed")
        self.assertEqual(
            [], self.fresh().memo_branch_conditions(self.fresh().data["memo"])
        )


class QueryJournalTest(JurisdictionCase):
    def plan(self, **args):
        base = {"query_class": "local_factual", "target": "the alpha record"}
        base.update(args)
        return tools.run(
            self.fake.client, self.root, self.run_id, call("plan_research", base)
        )

    def test_plan_and_resolve_query(self) -> None:
        self.case.set_checklist(ITEMS)
        result = self.plan(issue="alpha")
        self.assertEqual("Q1", result["query"])
        self.assertEqual("local-corpus", result["plan"]["route"])
        entry = self.fresh().data["queries"][0]
        self.assertEqual("open", entry["status"])
        self.assertEqual("the alpha record", entry["target"])
        resolved = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call(
                "resolve_query",
                {"query": "Q1", "outcome": "answered", "summary": "found it",
                 "coverage": "inbox only"},
            ),
        )
        self.assertEqual("closed", resolved["resolved"]["status"])
        self.assertEqual("inbox only", resolved["resolved"]["coverage"])
        error = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("resolve_query", {"query": "Q1", "outcome": "answered",
                                   "summary": "again"}),
        )
        self.assertIn("already resolved", error["error"])

    def test_applicable_law_plan_uses_matter_and_branches(self) -> None:
        self.case.set_checklist(ITEMS)
        bare = self.plan(query_class="applicable_law", target="which law governs")
        self.assertTrue(bare["plan"]["blocked"])
        self.assertTrue(any("connecting" in n for n in bare["plan"]["needs"]))
        self.case.note_context("performance", "Los Angeles", "known", "contract")
        self.case.note_context("transaction_type", "consumer", "known", "contract")
        branch = self.case.open_branch(
            "alpha", "ca", "unknown", "governing-law clause §9", basis_kind="clause"
        )
        routed = self.plan(
            query_class="applicable_law",
            target="which law governs",
            branch=branch["id"],
        )
        self.assertFalse(routed["plan"]["blocked"])
        warnings = " ".join(routed["plan"]["warnings"])
        self.assertIn("consumer", warnings)
        self.assertIn("validity and scope", warnings)

    def test_rule_plan_routes_through_branch_regime(self) -> None:
        self.case.set_checklist(ITEMS)
        branch = self.case.open_branch("alpha", "ca", "inferred", "performance in LA")
        result = self.plan(
            query_class="rule_exception",
            target="the limitations rule",
            branch=branch["id"],
            events_date="2021-03-04",
        )
        self.assertEqual(["ca-leginfo", "ca-opinions"], result["plan"]["repos"])
        entry = self.fresh().data["queries"][0]
        self.assertEqual("ca", entry["regime"])
        self.assertEqual("B1", entry["branch"])

    def test_close_in_one_call_and_validation(self) -> None:
        done = self.plan(
            outcome="unanswered", summary="no record found", coverage="full inbox"
        )
        self.assertEqual("closed", self.fresh().data["queries"][0]["status"])
        self.assertEqual("Q1", done["query"])
        error = self.plan(outcome="answered", summary="")
        self.assertIn("summary", error["error"])
        error = self.plan(query_class="divination")
        self.assertIn("query class", error["error"])
        error = self.plan(
            query_class="rule_exception", branch="B9"
        )
        self.assertIn("unknown branch", error["error"])
        error = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("resolve_query", {"query": "Q9", "outcome": "answered",
                                   "summary": "x"}),
        )
        self.assertIn("unknown query", error["error"])

    def test_work_status_lists_branches(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.open_branch("alpha", "ca", "disputed", "clause §9")
        result = tools.run(
            self.fake.client, self.root, self.run_id, call("work_status", {})
        )
        self.assertEqual(
            [{"id": "B1", "issue": "alpha", "regime": "California",
              "status": "disputed"}],
            result["branches"],
        )

    def test_prompt_covers_branches_kinds_and_research(self) -> None:
        prompt = support.instructions_file()
        self.assertIn("open_branch", prompt)
        self.assertIn("plan_research", prompt)
        self.assertIn("note_source", prompt)
        self.assertIn("never infer governing law from location alone", prompt)


if __name__ == "__main__":
    unittest.main()
