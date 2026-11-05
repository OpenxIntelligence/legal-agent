"""Adaptive steering: branch coverage, priority, budgets, stop.

Each fixture pins one directive action to the ranked policy
output; the wrapper tests pin budget halts, the stop signal,
determinism, and the never-suggest-approval invariant.
"""

from __future__ import annotations

import unittest

from agent_kit import steering, tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.reasoning import suggest_next_actions
from agent_kit.tools import suggest_next_action
from tests import support


def _chain(**overrides):
    """A complete quiet chain: identified, decomposed, answered,
    reviewed, planned, declared — nothing left to investigate."""
    data = {
        "identification": {"task": "memo"},
        "decompositions": [
            {"verdict": "pass",
             "questions": [{"class": "facts", "issue": "shot"}]}
        ],
        "foundational": [
            {"id": "FQ1", "class": "facts", "issue": "shot",
             "status": "resolved"},
        ],
        "adversarial": [{"at": "2026-01-02"}],
        "facts": [{"id": "F0", "document_id": "D0", "quote": "Exact source passage.", "at": "2026-01-01"}],
        "workflow": {"components": ["research"]},
        "work_product": {"kind": "memo"},
    }
    data.update(overrides)
    return data


class BranchTest(unittest.TestCase):
    def test_open_contradiction_investigated(self) -> None:
        data = _chain(orchestration={
            "questions": {"Q1": {"issue": "shot", "status": "open"}},
            "contradictions": [
                {"id": "C1", "about": "Q1", "status": "open"},
                {"id": "C0", "about": "Q1", "status": "resolved"},
            ],
        })
        actions = suggest_next_actions(data, limit=5)
        self.assertEqual("adversarial_review", actions[0]["tool"])
        self.assertEqual("C1", actions[0]["target"])
        self.assertIn("shot", actions[0]["reason"])

    def test_unverified_authority_verified(self) -> None:
        data = _chain(authorities={
            "A1": {"verification": "unverified", "citation": "1 U.S. 1",
                   "treatment_checked": False, "document_id": "D1"},
        })
        actions = suggest_next_actions(data)
        self.assertEqual("analyze_document", actions[0]["tool"])
        self.assertEqual("D1", actions[0]["target"])
        self.assertIn("1 U.S. 1", actions[0]["reason"])

    def test_untreated_authority_checked_before_overlooked(self) -> None:
        data = _chain(authorities={
            "A2": {"verification": "verified", "citation": "2 U.S. 2",
                   "treatment_checked": False, "document_id": "D2"},
        })
        actions = suggest_next_actions(data)
        self.assertEqual("plan_research", actions[0]["tool"])
        self.assertIn("good law", actions[0]["reason"])
        self.assertIn("cannot confirm", actions[0]["reason"])

    def test_overlooked_authority_inspected(self) -> None:
        data = _chain(
            authorities={
                "A3": {"verification": "verified", "citation": "3 U.S. 3",
                       "treatment_checked": True, "document_id": "D9"},
            },
            facts=[{"id": "F1", "authority": ""}],
        )
        actions = suggest_next_actions(data)
        self.assertEqual("analyze_document", actions[0]["tool"])
        self.assertEqual("D9", actions[0]["target"])
        self.assertIn("A3", actions[0]["reason"])

    def test_relied_authority_draws_nothing(self) -> None:
        data = _chain(
            authorities={
                "A4": {"verification": "verified", "citation": "4 U.S. 4",
                       "treatment_checked": True, "document_id": "D4"},
            },
            facts=[{"id": "F1", "authority": "A4", "document_id": "D4", "quote": "Rule."}],
        )
        self.assertEqual(
            ["submit_memo"], [a["tool"] for a in suggest_next_actions(data)]
        )

    def test_ask_user_gap_surfaced(self) -> None:
        data = _chain(
            foundational=[
                {"id": "FQ1", "class": "facts", "issue": "shot",
                 "status": "unresolved"},
            ],
            triages=[{"id": "T1", "ref": "FQ1", "decision": "ask_user",
                      "detail": "who signed?"}],
        )
        tools = [a["tool"] for a in suggest_next_actions(data, limit=5)]
        self.assertIn("request_evidence", tools)

    def test_malformed_state_never_crashes(self) -> None:
        for data in (
            {},
            {"orchestration": {"contradictions": ["nope", None, {}]}},
            {"authorities": {"A1": None, "A2": []}},
            {"authorities": ["not-a-dict"]},
            {"triages": [{"ref": "FQ1"}]},
            {"foundational": ["nope"]},
            None,
        ):
            with self.subTest(data=data):
                actions = suggest_next_actions(data, limit=5)
                self.assertIsInstance(actions, list)


class ResearchProgressTest(unittest.TestCase):
    def test_bookkeeping_without_evidence_is_not_complete(self) -> None:
        result = steering.recommend(_chain(facts=[]))
        self.assertFalse(result["investigation_complete"])
        self.assertNotIn("submit_memo", [a["tool"] for a in result["actions"]])
        self.assertEqual("search_public_sources", result["actions"][0]["tool"])

    def test_empty_run_acquires_sources_before_identification(self) -> None:
        action = suggest_next_actions({})[0]
        self.assertEqual("search_public_sources", action["tool"])
        self.assertIn("Federal Register", action["reason"])

    def test_open_alternative_requires_assessment_but_blocked_can_be_disclosed(self) -> None:
        node = {"id": "R1", "question": "Alternative interpretation?", "status": "open"}
        self.assertFalse(steering.recommend(_chain(research_tree={"R1": node}))["investigation_complete"])
        node = dict(node, status="blocked", summary="Source unavailable", next_action="Reviewer to obtain order")
        self.assertTrue(steering.recommend(_chain(research_tree={"R1": node}))["investigation_complete"])

    def test_retrieved_source_precedes_intake_forms(self) -> None:
        data = {"public_sources": {"D1": {"url": "https://uscode.house.gov/"}}}
        action = suggest_next_actions(data)[0]
        self.assertEqual(("analyze_document", "D1"), (action["tool"], action["target"]))

    def test_search_candidate_is_fetched_before_planning(self) -> None:
        data = {"public_research_searches": [{"results": [{"url": "https://www.federalregister.gov/doc"}]}]}
        action = suggest_next_actions(data)[0]
        self.assertEqual("fetch_public_source", action["tool"])
        self.assertEqual("https://www.federalregister.gov/doc", action["target"])

    def test_fetch_budget_does_not_recommend_another_fetch(self) -> None:
        data = _chain(public_research_requests={"fetch": 12},
                      public_research_searches=[{"results": [{"url": "https://www.federalregister.gov/doc"}]}])
        self.assertNotIn("fetch_public_source", [a["tool"] for a in suggest_next_actions(data)])

    def test_open_query_blocks_completion_and_demands_coverage(self) -> None:
        result = steering.recommend(_chain(queries=[{"id": "Q1", "status": "open", "target": "Contrary rule?"}]))
        self.assertFalse(result["investigation_complete"])
        self.assertEqual("resolve_query", result["actions"][0]["tool"])
        self.assertIn("Contrary rule?", result["actions"][0]["reason"])

    def test_closed_blocked_route_allows_qualified_synthesis(self) -> None:
        result = steering.recommend(_chain(queries=[{"id": "Q1", "status": "closed", "outcome": "blocked", "coverage": "Court source unavailable"}]))
        self.assertTrue(result["investigation_complete"])

    def test_open_checklist_and_new_evidence_prevent_completion(self) -> None:
        for overrides in ({"checklist": {"rule": {"status": "open"}}},
                          {"facts": [{"id": "F2", "document_id": "D2", "quote": "Contrary text", "at": "2026-02-01"}]},
                          {"withdrawn_facts": [{"id": "F0"}]}):
            with self.subTest(overrides=overrides):
                self.assertFalse(steering.recommend(_chain(**overrides))["investigation_complete"])


class WrapperTest(unittest.TestCase):
    def test_budget_exhaustion_halts_with_escalation(self) -> None:
        for cap, used in (
            ({"max_calls": 10}, {"calls": 10}),
            ({"max_input_tokens": 5}, {"calls": 1, "input_tokens": 9}),
            ({"max_output_tokens": 5}, {"output_tokens": 5}),
        ):
            with self.subTest(cap=cap, used=used):
                result = steering.recommend(
                    _chain(), usage=used, budgets=cap
                )
                self.assertTrue(result["halt"])
                self.assertEqual(
                    ["request_approval"],
                    [a["tool"] for a in result["actions"]],
                )

    def test_budget_headroom_passes_through(self) -> None:
        result = steering.recommend(
            _chain(), usage={"calls": 3}, budgets={"max_calls": 10}
        )
        self.assertFalse(result["halt"])
        self.assertEqual(
            ["submit_memo"], [a["tool"] for a in result["actions"]]
        )

    def test_quiet_chain_reports_investigation_complete(self) -> None:
        result = steering.recommend(_chain())
        self.assertFalse(result["halt"])
        self.assertTrue(result["investigation_complete"])

    def test_busy_chain_not_complete(self) -> None:
        data = _chain(orchestration={
            "questions": {}, "contradictions": [{"id": "C1", "status": "open"}],
        })
        result = steering.recommend(data)
        self.assertFalse(result["investigation_complete"])
        self.assertEqual("adversarial_review",
                         result["actions"][0]["tool"])

    def test_deterministic(self) -> None:
        data = _chain(
            orchestration={
                "questions": {"Q1": {"issue": "b", "status": "open"}},
                "contradictions": [
                    {"id": "C2", "about": "Q1", "status": "open"},
                    {"id": "C1", "about": "Q1", "status": "open"},
                ],
            },
            authorities={
                "A9": {"verification": "provisional", "citation": "",
                       "treatment_checked": False, "document_id": ""},
            },
        )
        first = steering.recommend(data)
        second = steering.recommend(dict(data))
        self.assertEqual(first, second)
        self.assertEqual(
            ["C1", "C2"],
            [a["target"] for a in first["actions"]
             if a["tool"] == "adversarial_review"],
        )

    def test_never_suggests_approval(self) -> None:
        states = [
            _chain(),
            _chain(orchestration={"contradictions": [
                {"id": "C1", "status": "open"}]}),
            _chain(authorities={"A1": {"verification": "unverified"}}),
            {},
        ]
        for data in states:
            with self.subTest(data=data):
                verbs = [
                    a["tool"]
                    for a in steering.recommend(data)["actions"]
                ]
                self.assertNotIn("decide_approval", verbs)


class ToolWiringTest(unittest.TestCase):
    def _ctx(self, data: dict):
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(
            root, instructions="i", objective="o", verification=True
        )
        case = CaseRecord(root, run_id)
        case.data.update(data)
        fake = support.scripted(root, [])
        return tools.Context(
            fake.client, root, run_id, case, True
        )

    def test_tool_reports_stop_signal(self) -> None:
        ctx = self._ctx(_chain())
        result = suggest_next_action.run(ctx, {})
        self.assertTrue(result["investigation_complete"])
        self.assertFalse(result["halt"])
        self.assertEqual(
            ["submit_memo"],
            [a["tool"] for a in result["suggestions"]],
        )

    def test_tool_reports_open_work(self) -> None:
        ctx = self._ctx(_chain(orchestration={
            "questions": {},
            "contradictions": [{"id": "C1", "status": "open"}],
        }))
        result = suggest_next_action.run(ctx, {})
        self.assertFalse(result["investigation_complete"])
        self.assertEqual(
            "adversarial_review", result["suggestions"][0]["tool"]
        )


if __name__ == "__main__":
    unittest.main()
