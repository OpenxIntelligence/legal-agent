"""Milestone 1 acceptance: whole-memo claims verification (claims-v2).

Offline commit eab799c assessed only proven findings with citations and let
unreadable judge output pass. These offline tests prove the new contract:
the conclusion, every finding, and gaps are assessed; failed checks are
distinguished from unavailable checks; and the result survives unchanged
through filing, manual verification, saved records, and UI events.

Synthetic memos, temporary workspaces, and mocked clients only. No test
contacts an external model service, and every test runs the same with
VERIFICATION=off and VERIFICATION=on.
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import submit_memo
from agent_kit.tools.analyze_document import analyze
from scripts import verify
from tests import support
from tests.fake_model import Refused
from tests.test_verification import AFTER_RELEASE, ALL_INITIAL, FACTS, GOOD_MEMO
from verification import verify_claims, verify_memo


def claims_inputs(fake) -> list:
    """Prompt text of every claims-judge request the fake transport received."""
    return [
        call[1].get("input")
        for call in fake.calls
        if call[0] == "respond" and "CLAIMS TO CHECK" in json.dumps(call[1])
    ]


def units_block(prompt: str) -> list:
    match = re.search(r"UNITS \(JSON\):\n(\[.*?\])\n\nINVESTIGATION", prompt, re.S)
    assert match, "judge prompt has no UNITS block"
    return json.loads(match.group(1))


def record_block(prompt: str) -> dict:
    match = re.search(
        r"INVESTIGATION STATE \(JSON;.*?\):\n(\{.*?\})\n\nRULES", prompt, re.S
    )
    assert match, "judge prompt has no INVESTIGATION STATE block"
    return json.loads(match.group(1))


def break_claims(fake, output_text=None, error=None):
    """Make the claims-judge request return output_text, or raise error."""
    original = fake.responder

    def responder(kind, payload):
        if kind == "respond" and "CLAIMS TO CHECK" in json.dumps(payload):
            if error is not None:
                raise error
            return {
                "output_text": output_text,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }
        return original(kind, payload)

    fake.responder = responder
    return original


def load_console_ui():
    return support.console_ui()


MEMO_ALL = {
    "conclusion": "The vendor likely knew the system was not ready when it declared readiness.",
    "findings": [
        {
            "item": "not_ready",
            "status": "asserted",
            "summary": "Internal records show 21 open Sev-1 defects at period end.",
            "citations": [
                {
                    "document_id": "DOC-REPORT",
                    "page": 1,
                    "quote": "Open Severity 1 defects: 21",
                }
            ],
        },
        {
            "item": "timing",
            "status": "unresolved",
            "summary": "No dated record ties the defect count to the readiness statement.",
            "citations": [],
        },
        {
            "item": "seniority",
            "status": "escalated",
            "summary": "Whether the speaker could bind the vendor is with the reviewer.",
            "citations": [],
        },
    ],
    "gaps": "Timing unknown: no dated assessment was produced.",
}


class ClaimsCase(unittest.TestCase):
    """A temp workspace, a prepared case, and contexts for direct calls."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def prepare_good_case(self) -> None:
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        support.satisfy_reasoning_gate(self.case)
        analyze(self.fake.client, self.root, ALL_INITIAL, "x")
        support.release(self.root)
        analyze(self.fake.client, self.root, ["DOC-DRAFT", "DOC-CERT"], "x")
        for _name, args in FACTS + [AFTER_RELEASE[1], AFTER_RELEASE[3]]:
            self.case.record_fact(
                args["item"],
                args["document_id"],
                args["page"],
                args["quote"],
                args["claim"],
            )
        self.case.add_signature_check(
            {
                "document_id": "DOC-CERT",
                "page": 1,
                "signer": "Dana Okafor",
                "verdict": "consistent",
            }
        )


class WholeMemoTest(ClaimsCase):
    """H1-H6: the whole memo is assessed, with per-unit evidence scoping."""

    def test_h1_valid_complete_response_passes(self) -> None:
        result = verify_claims.run_verification(self.ctx, MEMO_ALL)
        self.assertEqual(
            (True, "passed", "claims-v2"),
            (result["passed"], result["outcome"], result["verifier"]),
        )
        self.assertEqual([], result["feedback"])
        expected = [
            "conclusion",
            "finding:not_ready",
            "finding:timing",
            "finding:seniority",
            "gaps",
        ]
        self.assertEqual(expected, result["scope"]["expected"])
        self.assertEqual(expected, result["scope"]["assessed"])
        (prompt,) = claims_inputs(self.fake)
        self.assertIn(MEMO_ALL["conclusion"], prompt)
        for finding in MEMO_ALL["findings"]:
            self.assertIn(finding["summary"], prompt)
        self.assertIn(MEMO_ALL["gaps"], prompt)

    def test_h2_unsupported_conclusion_fails_despite_matching_citations(self) -> None:
        self.prepare_good_case()
        memo = GOOD_MEMO[1]
        self.assertTrue(verify_memo.run_verification(self.ctx, memo)["passed"])
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "conclusion", "reason": "goes beyond the cited quotes"}
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify_claims.run_verification(ctx, memo)
        self.assertEqual(
            (False, "failed"), (result["passed"], result["outcome"])
        )
        self.assertEqual(1, len(result["feedback"]))
        self.assertIn("conclusion", result["feedback"][0])

    def test_h3_unsupported_not_proven_finding_fails(self) -> None:
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:timing", "reason": "asserts an absence it cannot show"}
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify_claims.run_verification(ctx, MEMO_ALL)
        self.assertEqual("failed", result["outcome"])
        self.assertIn("timing", result["feedback"][0])
        (prompt,) = claims_inputs(fake)
        units = {u["id"]: u for u in units_block(prompt)}
        self.assertEqual("unresolved", units["finding:timing"]["status"])
        self.assertIn(
            "No dated record ties the defect count", units["finding:timing"]["text"]
        )

    def test_h4_unsupported_gaps_assertion_fails(self) -> None:
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "gaps", "reason": "states a fact with no support"}
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify_claims.run_verification(ctx, MEMO_ALL)
        self.assertEqual("failed", result["outcome"])
        self.assertIn("gaps", result["feedback"][0])
        (prompt,) = claims_inputs(fake)
        self.assertIn(MEMO_ALL["gaps"], prompt)

    def test_h5_qualified_uncertainty_can_pass(self) -> None:
        memo = {
            "conclusion": "The record suggests the system was not ready, but the exact defect count could not be confirmed.",
            "findings": [
                {
                    "item": "not_ready",
                    "status": "unresolved",
                    "summary": "It is unknown whether the defects predated the statement.",
                    "citations": [],
                }
            ],
            "gaps": "Timing remains unknown.",
        }
        result = verify_claims.run_verification(self.ctx, memo)
        self.assertEqual("passed", result["outcome"])
        self.assertIn("properly qualified statement of uncertainty", verify_claims.PROMPT)

    def test_h6_finding_cannot_borrow_unrelated_citations(self) -> None:
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:seniority", "reason": "no citation of its own"}
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify_claims.run_verification(ctx, MEMO_ALL)
        self.assertEqual("failed", result["outcome"])
        (prompt,) = claims_inputs(fake)
        units = {u["id"]: u for u in units_block(prompt)}
        self.assertEqual([], units["finding:seniority"]["citations"])
        self.assertEqual(
            ["Open Severity 1 defects: 21"],
            [c["quote"] for c in units["finding:not_ready"]["citations"]],
        )
        self.assertEqual(
            ["Open Severity 1 defects: 21"],
            [c["quote"] for c in units["conclusion"]["citations"]],
        )
        self.assertIn("use ONLY its own citations", verify_claims.PROMPT)

    def test_judge_prompt_contract(self) -> None:
        prompt = verify_claims.PROMPT
        self.assertIn("CLAIMS TO CHECK", prompt)
        self.assertIn("ASSESSMENT UNIT IDS", prompt)
        self.assertIn("untrusted evidence", prompt)
        self.assertIn(
            "does not by itself establish interpretation", prompt
        )
        self.assertIn(
            "absence of evidence in this material is not proof", prompt
        )
        for token in (
            "Lindgren",
            "Okafor",
            "DOC-",
            "go-live",
            "Severity",
            "UAT",
            "December 19",
        ):
            self.assertNotIn(token, prompt)

    def test_scoped_record_info_reaches_the_judge(self) -> None:
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", ALL_INITIAL
        )
        verify_claims.run_verification(self.ctx, MEMO_ALL)
        (prompt,) = claims_inputs(self.fake)
        record = record_block(prompt)
        self.assertEqual("escalated", record["checklist"]["timing"])
        self.assertIn(
            {"id": request["id"], "item": "timing"}, record["open_requests"]
        )


def full_results() -> list:
    return [
        {"id": "conclusion", "verdict": "supported"},
        {"id": "finding:ready_statement", "verdict": "supported"},
        {
            "id": "gaps",
            "verdict": "supported",
            "reason": "no assertions to check",
            "support": ["finding:ready_statement"],
        },
    ]


class JudgeContractTest(ClaimsCase):
    """H7-H8: incomplete or invalid judge output never passes."""

    def check_unavailable(self, output_text, snippet) -> None:
        break_claims(self.fake, output_text)
        result = verify_claims.run_verification(self.ctx, support.MEMO[1])
        self.assertEqual(
            (False, "unavailable"), (result["passed"], result["outcome"])
        )
        self.assertEqual([], result["scope"]["assessed"])
        self.assertTrue(result["feedback"])
        self.assertIn(snippet, result["feedback"][0])

    def test_h7_malformed_responses_are_unavailable(self) -> None:
        full = full_results()
        variants = [
            ("{not json, SENSITIVE-PAYLOAD", "not valid JSON"),
            ('{"results": [{"id": "conclusion", "verdict": "sup', "not valid JSON"),
            ('["results"]', "must be an object"),
            ('{"foo": []}', "missing 'results'"),
            ('{"results": {}}', "'results' must be a list"),
            ('{"results": ["x"]}', "must be an object"),
            ('{"results": [{"id": "conclusion"}]}', "missing required fields"),
            ('{"results": [{"id": 5, "verdict": "supported"}]}', "invalid id"),
            (
                json.dumps({"results": full[:2]}),
                "missing results for units: gaps",
            ),
            (
                json.dumps({"results": full + [full[0]]}),
                "duplicate result for unit 'conclusion'",
            ),
            (
                json.dumps(
                    {
                        "results": full
                        + [{"id": "finding:nope", "verdict": "supported"}]
                    }
                ),
                "unknown unit 'finding:nope'",
            ),
            (
                json.dumps(
                    {
                        "results": [
                            dict(full[0], verdict="maybe"),
                            full[1],
                            full[2],
                        ]
                    }
                ),
                "invalid verdict",
            ),
            (
                json.dumps(
                    {
                        "results": [
                            dict(full[0], reason=5),
                            full[1],
                            full[2],
                        ]
                    }
                ),
                "invalid reason",
            ),
            (
                json.dumps(
                    {
                        "results": [
                            dict(full[0], support="finding:x"),
                            full[1],
                            full[2],
                        ]
                    }
                ),
                "invalid support",
            ),
        ]
        for output_text, snippet in variants:
            with self.subTest(output=output_text[:60]):
                self.setUp()
                self.check_unavailable(output_text, snippet)

    def test_h7_feedback_never_echoes_the_raw_response(self) -> None:
        break_claims(self.fake, "{SENSITIVE-PAYLOAD not json")
        result = verify_claims.run_verification(self.ctx, support.MEMO[1])
        self.assertEqual(
            ["the judge response was not valid JSON"], result["feedback"]
        )

    def test_h8_provider_failures_are_unavailable(self) -> None:
        break_claims(self.fake, error=Refused("service overloaded"))
        result = verify_claims.run_verification(self.ctx, support.MEMO[1])
        self.assertEqual("unavailable", result["outcome"])
        self.assertFalse(result["passed"])
        self.assertIn("judge request failed", result["feedback"][0])
        self.assertLess(len(result["feedback"][0]), 300)

    def test_h8_timeout_is_unavailable(self) -> None:
        break_claims(self.fake, error=TimeoutError("timed out"))
        result = verify_claims.run_verification(self.ctx, support.MEMO[1])
        self.assertEqual("unavailable", result["outcome"])
        self.assertFalse(result["passed"])
        self.assertIn("judge request failed", result["feedback"][0])


class ReferenceValidationTest(ClaimsCase):
    """A3: support refs must resolve to supplied, in-scope evidence."""

    def test_supported_with_unknown_ref_is_unavailable(self) -> None:
        self.prepare_good_case()
        self.case.file_memo(copy.deepcopy(GOOD_MEMO[1]), "awaiting_signoff")
        fake = support.scripted(
            self.root, [], support=lambda text: {"conclusion": ["DOC-NOPE p9"]}
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify.verify_memo(ctx)
        self.assertFalse(result["passed"])
        self.assertEqual("unavailable", result["claims_outcome"])
        self.assertIn("DOC-NOPE", result["claims"][0])
        self.assertIn("no supplied evidence", result["claims"][0])

    def test_supported_with_empty_support_is_unavailable(self) -> None:
        self.prepare_good_case()
        self.case.file_memo(copy.deepcopy(GOOD_MEMO[1]), "awaiting_signoff")
        fake = support.scripted(
            self.root, [], support=lambda text: {"conclusion": []}
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify.verify_memo(ctx)
        self.assertEqual("unavailable", result["claims_outcome"])
        self.assertIn("no supporting references", result["claims"][0])

    def test_wrong_scope_ref_is_unavailable(self) -> None:
        units = [
            {
                "id": "finding:timing",
                "kind": "finding",
                "citations": [{"document_id": "DOC-A", "page": 1}],
            },
            {
                "id": "conclusion",
                "kind": "conclusion",
                "citations": [
                    {"document_id": "DOC-A", "page": 1},
                    {"document_id": "DOC-B", "page": 2},
                ],
            },
        ]
        verdicts = {
            "finding:timing": {"verdict": "supported", "support": ["DOC-B p2"]},
            "conclusion": {"verdict": "supported", "support": ["DOC-B p2"]},
        }
        problems = verify_claims._check_support(units, verdicts)
        self.assertEqual(1, len(problems))
        self.assertIn("finding:timing", problems[0])
        self.assertIn("DOC-B p2", problems[0])

    def test_unit_and_investigation_markers_resolve(self) -> None:
        units = [
            {"id": "finding:timing", "kind": "finding", "citations": []},
            {"id": "gaps", "kind": "gaps", "citations": []},
        ]
        verdicts = {
            "finding:timing": {
                "verdict": "supported",
                "support": ["finding:timing restates the checklist question"],
            },
            "gaps": {
                "verdict": "supported",
                "support": ["no open request covers this in the investigation state"],
            },
        }
        self.assertEqual([], verify_claims._check_support(units, verdicts))

    def test_unsupported_verdict_support_is_not_judged(self) -> None:
        units = [{"id": "conclusion", "kind": "conclusion", "citations": []}]
        verdicts = {
            "conclusion": {
                "verdict": "unsupported",
                "reason": "overstates the quote",
                "support": ["DOC-NOPE p9"],
            }
        }
        self.assertEqual([], verify_claims._check_support(units, verdicts))

    def test_scope_carries_memo_and_evidence_versions(self) -> None:
        from quality.records import evidence_fingerprint, memo_hash

        self.prepare_good_case()
        memo = copy.deepcopy(GOOD_MEMO[1])
        result = verify_claims.run_verification(self.ctx, memo)
        self.assertEqual("passed", result["outcome"])
        self.assertEqual(memo_hash(memo), result["scope"]["memo"]["hash"])
        self.assertEqual(
            evidence_fingerprint(self.case.data), result["scope"]["evidence"]
        )

    def test_half_sentence_support_stays_a_defect(self) -> None:
        self.prepare_good_case()
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {
                    "unit": "conclusion",
                    "reason": "the quote is exact but supports only the first clause",
                }
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify_claims.run_verification(ctx, copy.deepcopy(GOOD_MEMO[1]))
        self.assertEqual("failed", result["outcome"])
        self.assertIn("conclusion", result["feedback"][0])
        self.assertIn("first clause", result["feedback"][0])


class SubmitMemoIntegrationTest(ClaimsCase):
    """H9-H11, H13: unavailable pauses; failed keeps refusal/escalation."""

    def submit(self, memo_args) -> dict:
        return submit_memo.run(self.ctx, copy.deepcopy(memo_args))

    def test_h9_h10_unavailable_pauses_without_filing_or_refusal(self) -> None:
        self.prepare_good_case()
        break_claims(self.fake, error=Refused("service overloaded"))
        result = self.submit(GOOD_MEMO[1])
        self.assertEqual("paused", result["status"])
        self.assertEqual("unavailable", result["outcome"])
        self.assertEqual("verification_unavailable", result["reason"])
        self.assertEqual("claims", result["check"])
        self.assertIsNone(self.case.data["memo"])
        self.assertEqual(0, self.case.memo_refusals())
        (record,) = [
            c for c in self.case.data["memo_checks"] if c["check"] == "claims"
        ]
        self.assertEqual("unavailable", record["outcome"])
        self.assertTrue(record["problems"])
        self.assertEqual("claims-v2", record["verifier"])
        self.assertTrue(record["scope"]["expected"])

    def test_unavailable_retry_files_after_recovery(self) -> None:
        self.prepare_good_case()
        original = break_claims(self.fake, error=Refused("service overloaded"))
        paused = self.submit(GOOD_MEMO[1])
        self.assertEqual("paused", paused["status"])
        self.fake.responder = original
        filed = self.submit(GOOD_MEMO[1])
        self.assertEqual("awaiting_signoff", filed["status"])
        self.assertEqual("passed", filed["outcome"])
        self.assertEqual("awaiting_signoff", self.case.data["memo"]["status"])
        self.assertEqual(0, self.case.memo_refusals())

    def test_passed_check_preserves_support_refs(self) -> None:
        self.prepare_good_case()
        refs = {"conclusion": ["finding:not_ready citation DOC-REPORT p1"]}
        self.fake = support.scripted(self.root, [], support=lambda text: refs)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        filed = self.submit(GOOD_MEMO[1])
        self.assertEqual("passed", filed["outcome"])
        self.assertEqual(refs["conclusion"], filed["support"]["conclusion"])
        self.assertEqual(8, len(filed["support"]))
        (record,) = [
            c for c in self.case.data["memo_checks"] if c["check"] == "claims"
        ]
        self.assertEqual(refs["conclusion"], record["support"]["conclusion"])

    def test_unavailable_check_carries_empty_support(self) -> None:
        self.prepare_good_case()
        break_claims(self.fake, error=Refused("service overloaded"))
        result = self.submit(GOOD_MEMO[1])
        self.assertEqual("unavailable", result["outcome"])
        self.assertEqual({}, result["support"])

    def test_h11_unsupported_claims_keep_refusal_and_escalation(self) -> None:
        self.prepare_good_case()
        self.fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:timing", "reason": "the quote gives no date"}
            ],
        )
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        first = self.submit(GOOD_MEMO[1])
        self.assertEqual(
            ("memo refused", "claims", "failed"),
            (first["error"], first["check"], first["outcome"]),
        )
        self.assertTrue(first["scope"]["expected"])
        self.assertIsNone(self.case.data["memo"])
        second = self.submit(GOOD_MEMO[1])
        self.assertEqual("memo refused", second["error"])
        third = self.submit(GOOD_MEMO[1])
        self.assertTrue(third["escalated"])
        self.assertEqual("needs_input", third["status"])
        self.assertEqual("memo_refused", self.case.data["requests"][0]["rule"])
        self.assertEqual(3, self.case.memo_refusals())
        self.assertIsNone(self.case.data["memo"])

    def test_supported_then_unsupported_records_a_regression(self) -> None:
        self.prepare_good_case()
        calls = []

        def unsupported(text):
            calls.append(1)
            if len(calls) > 1:
                return [
                    {
                        "unit": "finding:timing",
                        "reason": "wording drifted past the quote",
                    }
                ]
            return []

        self.fake = support.scripted(self.root, [], unsupported=unsupported)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        filed = self.submit(GOOD_MEMO[1])
        self.assertEqual("passed", filed["outcome"])
        self.assertEqual([], filed["regressions"])
        second = self.submit(GOOD_MEMO[1])
        self.assertEqual("failed", second["outcome"])
        self.assertEqual(
            [{"unit": "finding:timing", "was": "supported", "now": "unsupported"}],
            second["regressions"],
        )
        records = [
            c for c in self.case.data["memo_checks"] if c["check"] == "claims"
        ]
        self.assertEqual(2, len(records))
        self.assertEqual([], records[0]["regressions"])
        self.assertEqual(second["regressions"], records[1]["regressions"])

    def test_unsupported_then_supported_is_not_a_regression(self) -> None:
        self.prepare_good_case()
        calls = []

        def unsupported(text):
            calls.append(1)
            if len(calls) > 1:
                return []
            return [{"unit": "finding:timing", "reason": "the quote gives no date"}]

        self.fake = support.scripted(self.root, [], unsupported=unsupported)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        first = self.submit(GOOD_MEMO[1])
        self.assertEqual("failed", first["outcome"])
        self.assertEqual([], first["regressions"])
        filed = self.submit(GOOD_MEMO[1])
        self.assertEqual("passed", filed["outcome"])
        self.assertEqual([], filed["regressions"])

    def test_supported_uncertainty_answers_with_its_polarity_recorded(self) -> None:
        self.prepare_good_case()
        memo = copy.deepcopy(GOOD_MEMO[1])
        timing = next(f for f in memo["findings"] if f["item"] == "timing")
        timing["status"] = "unresolved"
        timing["summary"] = "No dated record ties the recommendation to the statement."
        filed = self.submit(memo)
        self.assertEqual("passed", filed["outcome"])
        checklist = self.case.data["checklist"]
        self.assertEqual("answered", checklist["timing"]["status"])
        self.assertEqual("unresolved", checklist["timing"]["resolution"])
        self.assertEqual("answered", checklist["not_ready"]["status"])
        self.assertEqual("asserted", checklist["not_ready"]["resolution"])

    def test_unsupported_uncertainty_leaves_the_item_open(self) -> None:
        self.prepare_good_case()
        self.fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:timing", "reason": "the quote gives no date"}
            ],
        )
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        memo = copy.deepcopy(GOOD_MEMO[1])
        timing = next(f for f in memo["findings"] if f["item"] == "timing")
        timing["status"] = "unresolved"
        result = self.submit(memo)
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("open", self.case.data["checklist"]["timing"]["status"])
        self.assertNotIn("resolution", self.case.data["checklist"]["timing"])

    def test_h13_verification_off_unchanged(self) -> None:
        ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, False
        )
        result = submit_memo.run(ctx, copy.deepcopy(support.MEMO[1]))
        self.assertEqual(
            (True, "unverified", "answered"),
            (result["filed"], result["memo"], result["status"]),
        )
        self.assertEqual([], self.case.data["memo_checks"])
        self.assertEqual([], claims_inputs(self.fake))


class StalenessTest(ClaimsCase):
    """C7: material changes stale dependent verdicts; the rest stands."""

    def submit(self, memo_args) -> dict:
        return submit_memo.run(self.ctx, copy.deepcopy(memo_args))

    def test_fresh_filing_is_current(self) -> None:
        self.prepare_good_case()
        filed = self.submit(GOOD_MEMO[1])
        self.assertEqual("passed", filed["outcome"])
        self.assertEqual(
            {"stale": {}, "unassessed": [], "unknown": False},
            self.case.claims_staleness(),
        )

    def test_new_fact_on_cited_doc_stales_dependent_units(self) -> None:
        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        new = self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Weekly status report", "late arrival"
        )
        stale = self.case.claims_staleness()
        self.assertEqual(
            {
                "conclusion",
                "finding:not_ready",
                "finding:timing",
                "finding:counterevidence",
                "gaps",
            },
            set(stale["stale"]),
        )
        self.assertEqual([new["id"]], stale["unassessed"])
        self.assertFalse(stale["unknown"])

    def test_uncited_doc_leaves_conclusion_and_others_current(self) -> None:
        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        new = self.case.record_fact(
            "counterevidence",
            "DOC-MEMO",
            1,
            "Attorney work product for the review team",
            "late arrival",
        )
        stale = self.case.claims_staleness()
        # Only the finding for the fact's own item: the document is
        # cited nowhere, so the conclusion and every other unit stand.
        self.assertEqual({"finding:counterevidence"}, set(stale["stale"]))
        self.assertEqual([new["id"]], stale["unassessed"])

    def test_withdrawn_cited_fact_stales_citing_units(self) -> None:
        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        self.case.flag_privileged("DOC-DRAFT", "attorney work product")
        stale = self.case.claims_staleness()
        self.assertIn("finding:timing", stale["stale"])
        self.assertIn("conclusion", stale["stale"])
        self.assertNotIn("finding:signatures", stale["stale"])
        self.assertNotIn("finding:ready_statement", stale["stale"])

    def test_revised_memo_stales_every_unit(self) -> None:
        from quality.staleness import assess

        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        (record,) = [
            c for c in self.case.data["memo_checks"] if c["check"] == "claims"
        ]
        revised = dict(GOOD_MEMO[1], conclusion="No.")
        stale = assess(record, self.case.data["facts"], revised)
        self.assertEqual(set(record["scope"]["assessed"]), set(stale["stale"]))
        self.assertTrue(
            all("revised" in reason for reason in stale["stale"].values())
        )

    def test_legacy_check_without_fact_index_stales_broadly(self) -> None:
        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        (record,) = [
            c for c in self.case.data["memo_checks"] if c["check"] == "claims"
        ]
        del record["fact_index"]
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Weekly status report", "late arrival"
        )
        stale = self.case.claims_staleness()
        self.assertEqual(set(record["scope"]["assessed"]), set(stale["stale"]))
        self.assertTrue(
            all("legacy" in reason for reason in stale["stale"].values())
        )

    def test_review_carries_claims_staleness(self) -> None:
        from quality import controller

        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Weekly status report", "late arrival"
        )
        review = controller.review(self.ctx, copy.deepcopy(GOOD_MEMO[1]))
        self.assertIn("finding:not_ready", review["review"]["claims_stale"]["stale"])

    def test_case_record_snapshots_refresh_on_every_mutation(self) -> None:
        self.prepare_good_case()
        self.submit(GOOD_MEMO[1])
        self.assertEqual(
            {"stale": {}, "unassessed": [], "unknown": False},
            self.case.data["claims_staleness"],
        )
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Weekly status report", "late arrival"
        )
        self.assertIn("finding:not_ready", self.case.data["claims_staleness"]["stale"])
        reloaded = CaseRecord(self.root, self.run_id)
        self.assertIn(
            "finding:not_ready", reloaded.data["claims_staleness"]["stale"]
        )


class ManualVerifyTest(ClaimsCase):
    """H12: the manual path preserves outcome and scope."""

    def test_h12_manual_pass_preserves_outcome_and_scope(self) -> None:
        self.prepare_good_case()
        self.case.file_memo(copy.deepcopy(GOOD_MEMO[1]), "awaiting_signoff")
        result = verify.verify_memo(self.ctx)
        self.assertEqual(
            (True, "passed", "claims-v2"),
            (result["passed"], result["claims_outcome"], result["claims_verifier"]),
        )
        self.assertEqual([], result["rules"])
        self.assertEqual([], result["claims"])
        self.assertEqual(8, len(result["claims_scope"]["expected"]))
        self.assertEqual(
            result["claims_scope"]["expected"], result["claims_scope"]["assessed"]
        )

    def test_h12_manual_failure_preserves_outcome_and_scope(self) -> None:
        self.prepare_good_case()
        self.case.file_memo(copy.deepcopy(GOOD_MEMO[1]), "awaiting_signoff")
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:timing", "reason": "the quote gives no date"}
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify.verify_memo(ctx)
        self.assertEqual((False, "failed"), (result["passed"], result["claims_outcome"]))
        self.assertIn("timing", result["claims"][0])
        self.assertEqual(8, len(result["claims_scope"]["assessed"]))

    def test_invalid_memo_gets_no_semantic_success(self) -> None:
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self.case.file_memo(
            {"conclusion": "Yes.", "findings": [], "gaps": ""}, "unverified"
        )
        result = verify.verify_memo(self.ctx)
        self.assertFalse(result["passed"])
        self.assertTrue(result["rules"])
        self.assertEqual(
            ["claims check not run: memo rules failed"], result["claims"]
        )
        self.assertEqual("unavailable", result["claims_outcome"])
        self.assertIsNone(result["claims_scope"])
        self.assertEqual({}, result["claims_support"])
        self.assertEqual([], claims_inputs(self.fake))

    def test_manual_pass_preserves_support_refs(self) -> None:
        self.prepare_good_case()
        self.case.file_memo(copy.deepcopy(GOOD_MEMO[1]), "awaiting_signoff")
        refs = {"conclusion": ["finding:not_ready citation DOC-REPORT p1"]}
        fake = support.scripted(self.root, [], support=lambda text: refs)
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        result = verify.verify_memo(ctx)
        self.assertEqual("passed", result["claims_outcome"])
        self.assertEqual(refs["conclusion"], result["claims_support"]["conclusion"])
        self.assertEqual(8, len(result["claims_support"]))

    def test_format_memo_shows_outcome_scope_and_support(self) -> None:
        failed = {
            "passed": False,
            "rules": [],
            "claims": ["timing: the finding is not supported"],
            "claims_outcome": "failed",
            "claims_scope": SCOPE,
            "claims_verifier": "claims-v2",
            "claims_support": {"conclusion": ["DOC-REPORT p1"]},
        }
        text = verify.format_memo(failed)
        self.assertIn("MEMO fails", text)
        self.assertIn(
            "claims check: failed (assessed 3 of 3 units · verifier claims-v2)",
            text,
        )
        self.assertIn("claim: timing: the finding is not supported", text)
        self.assertIn("support conclusion: DOC-REPORT p1", text)
        unavailable = dict(
            failed,
            claims=["the judge request failed: overloaded"],
            claims_outcome="unavailable",
            claims_scope=EMPTY_SCOPE,
            claims_support={},
        )
        text = verify.format_memo(unavailable)
        self.assertIn("MEMO incomplete: claims check unavailable", text)
        self.assertNotIn("MEMO fails", text)
        self.assertIn("claims check: unavailable (assessed 0 of 3 units", text)
        self.assertNotIn("support conclusion", text)
        legacy = {"passed": True, "rules": [], "claims": []}
        self.assertNotIn("claims check:", verify.format_memo(legacy))

    def test_format_memo_primary_labels(self) -> None:
        self.assertEqual(
            "MEMO passes",
            verify.format_memo(
                {"passed": True, "rules": [], "claims_outcome": "passed"}
            ).splitlines()[0],
        )
        self.assertEqual(
            "MEMO fails",
            verify.format_memo(
                {
                    "passed": False,
                    "rules": [],
                    "claims": ["x"],
                    "claims_outcome": "failed",
                }
            ).splitlines()[0],
        )
        self.assertEqual(
            "MEMO fails",
            verify.format_memo(
                {
                    "passed": False,
                    "rules": ["no finding for timing"],
                    "claims_outcome": "unavailable",
                }
            ).splitlines()[0],
        )
        self.assertEqual(
            "MEMO incomplete: claims check unavailable",
            verify.format_memo(
                {
                    "passed": False,
                    "rules": [],
                    "claims": ["the judge request failed"],
                    "claims_outcome": "unavailable",
                }
            ).splitlines()[0],
        )

    def test_manual_result_carries_aggregate_state(self) -> None:
        self.prepare_good_case()
        self.case.file_memo(copy.deepcopy(GOOD_MEMO[1]), "awaiting_signoff")
        passed = verify.verify_memo(self.ctx)
        self.assertEqual("ready-for-review", passed["state"])
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:timing", "reason": "no date"}
            ],
        )
        ctx = tools.Context(
            fake.client, self.root, self.run_id, self.case, True
        )
        failed = verify.verify_memo(ctx)
        self.assertEqual("revision-required", failed["state"])
        break_claims(self.fake, error=Refused("service overloaded"))
        incomplete = verify.verify_memo(self.ctx)
        self.assertEqual("unavailable", incomplete["claims_outcome"])
        self.assertEqual("assessment-incomplete", incomplete["state"])


class LegacyRecordsTest(ClaimsCase):
    """H14: legacy records stay readable and gain no invented coverage."""

    LEGACY_CHECKS = [
        {
            "check": "memo",
            "passed": False,
            "problems": ["no finding for timing"],
            "at": "2026-01-01T00:00:00+00:00",
        },
        {
            "check": "claims",
            "passed": False,
            "problems": ["timing: the finding is not supported by its quotes"],
            "at": "2026-01-01T00:01:00+00:00",
        },
        {
            "check": "claims",
            "passed": True,
            "problems": [],
            "at": "2026-01-01T00:02:00+00:00",
        },
    ]

    def legacy_case(self) -> CaseRecord:
        path = self.root / "workspace/case_records" / f"{self.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "created": "2026-01-01T00:00:00+00:00",
                    "checklist": {},
                    "checklist_checks": [],
                    "memo_checks": copy.deepcopy(self.LEGACY_CHECKS),
                    "read_ids": [],
                    "facts": [],
                    "rejected_facts": [],
                    "flags": [],
                    "signature_checks": [],
                    "requests": [],
                    "memo": None,
                    "audit": [],
                }
            )
        )
        return CaseRecord(self.root, self.run_id)

    def test_h14_legacy_records_readable_without_invented_coverage(self) -> None:
        case = self.legacy_case()
        self.assertEqual(2, case.memo_refusals())
        for record in case.data["memo_checks"]:
            self.assertNotIn("outcome", record)
            self.assertNotIn("scope", record)
            self.assertNotIn("verifier", record)
        before = copy.deepcopy(case.data["memo_checks"])
        case.record_memo_check(
            "claims",
            ["the judge request failed"],
            outcome="unavailable",
            scope={"expected": ["conclusion"], "assessed": []},
            verifier="claims-v2",
        )
        self.assertEqual(2, case.memo_refusals())
        self.assertEqual(before, case.data["memo_checks"][:3])
        self.assertEqual("unavailable", case.data["memo_checks"][3]["outcome"])
        case.record_memo_check("claims", ["timing: unsupported"], outcome="failed")
        self.assertEqual(3, case.memo_refusals())


def synthetic_progress(tool_name, call_args, result, status, turn=1):
    call_id = "call-1"
    return {
        "run_id": "run-test",
        "verification": True,
        "model": "scripted",
        "started": "2026-01-01T00:00:00+00:00",
        "status": status,
        "turn": turn,
        "messages": [
            {"role": "user", "content": "objective"},
            {
                "type": "function_call",
                "call_id": call_id,
                "name": tool_name,
                "arguments": json.dumps(call_args),
            },
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result),
            },
        ],
        "turns": [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "status": status,
                "end": 3,
                "turn": turn,
                "saw": 0,
                "output": 2,
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ],
        "notes": {},
    }


SCOPE = {
    "expected": ["conclusion", "finding:timing", "gaps"],
    "assessed": ["conclusion", "finding:timing", "gaps"],
}
EMPTY_SCOPE = {
    "expected": ["conclusion", "finding:timing", "gaps"],
    "assessed": [],
}


class UIEventsTest(unittest.TestCase):
    """H15: UI event data distinguishes unavailable from failed, with scope."""

    def setUp(self) -> None:
        self.ui = load_console_ui()

    def claims_events(self, events):
        return [
            e
            for e in events
            if e["event_type"] == "verification"
            and e["payload"].get("check") == "claims"
        ]

    def test_h15_failed_event_carries_outcome_and_scope(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "error": "memo refused",
                "check": "claims",
                "problems": ["timing: the finding is not supported"],
                "outcome": "failed",
                "scope": SCOPE,
                "verifier": "claims-v2",
            },
            "running",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (claims,) = self.claims_events(events)
        self.assertEqual(
            (False, "failed", SCOPE, "claims-v2"),
            (
                claims["payload"]["passed"],
                claims["payload"]["outcome"],
                claims["payload"]["scope"],
                claims["payload"]["verifier"],
            ),
        )
        self.assertEqual(1, self.ui.metrics(events, None)["rejected"])

    def test_h15_unavailable_event_distinct_with_pause_reason(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "status": "paused",
                "reason": "verification_unavailable",
                "check": "claims",
                "outcome": "unavailable",
                "scope": EMPTY_SCOPE,
                "verifier": "claims-v2",
                "problems": ["the judge request failed: overloaded"],
                "error": "claims check unavailable: the judge request failed.",
            },
            "paused",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (claims,) = self.claims_events(events)
        self.assertEqual("unavailable", claims["payload"]["outcome"])
        self.assertFalse(claims["payload"]["passed"])
        self.assertEqual(EMPTY_SCOPE, claims["payload"]["scope"])
        (finished,) = [
            e for e in events if e["event_type"] == "run_finished"
        ]
        self.assertEqual(
            ("paused", "verification_unavailable"),
            (finished["payload"]["status"], finished["payload"].get("reason")),
        )
        self.assertIn("claims check unavailable", finished["payload"]["detail"])
        self.assertEqual(0, self.ui.metrics(events, None)["rejected"])

    def test_h15_filed_event_carries_passed_scope(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "filed": True,
                "memo": "awaiting_signoff",
                "status": "awaiting_signoff",
                "check": "claims",
                "outcome": "passed",
                "scope": SCOPE,
                "verifier": "claims-v2",
            },
            "awaiting_signoff",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (claims,) = self.claims_events(events)
        self.assertEqual(
            (True, "passed", SCOPE),
            (
                claims["payload"]["passed"],
                claims["payload"]["outcome"],
                claims["payload"]["scope"],
            ),
        )

    def test_failed_event_carries_support_refs(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "error": "memo refused",
                "check": "claims",
                "problems": ["timing: the finding is not supported"],
                "outcome": "failed",
                "scope": SCOPE,
                "verifier": "claims-v2",
                "support": {"conclusion": ["DOC-REPORT p1"]},
            },
            "running",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (claims,) = self.claims_events(events)
        self.assertEqual(
            {"conclusion": ["DOC-REPORT p1"]}, claims["payload"]["support"]
        )

    def test_filed_event_carries_support_refs(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "filed": True,
                "memo": "awaiting_signoff",
                "status": "awaiting_signoff",
                "check": "claims",
                "outcome": "passed",
                "scope": SCOPE,
                "verifier": "claims-v2",
                "support": {"conclusion": ["DOC-REPORT p1"]},
            },
            "awaiting_signoff",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (claims,) = self.claims_events(events)
        self.assertEqual(
            {"conclusion": ["DOC-REPORT p1"]}, claims["payload"]["support"]
        )

    def test_h15_legacy_events_gain_no_invented_scope(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "error": "memo refused",
                "check": "claims",
                "problems": ["timing: not supported"],
            },
            "running",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (claims,) = self.claims_events(events)
        self.assertFalse(claims["payload"]["passed"])
        self.assertIsNone(claims["payload"]["outcome"])
        self.assertIsNone(claims["payload"]["scope"])
        self.assertIsNone(claims["payload"]["verifier"])
        self.assertIsNone(claims["payload"]["support"])
        filed = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {"filed": True, "memo": "awaiting_signoff", "status": "awaiting_signoff"},
            "awaiting_signoff",
        )
        (filed_claims,) = self.claims_events(
            self.ui.build_events(filed, {"requests": []}, None)
        )
        self.assertTrue(filed_claims["payload"]["passed"])
        self.assertIsNone(filed_claims["payload"]["scope"])

    def test_pause_reason_reaches_run_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            paused = synthetic_progress(
                "submit_memo",
                {"conclusion": "x"},
                {
                    "status": "paused",
                    "reason": "verification_unavailable",
                    "check": "claims",
                    "outcome": "unavailable",
                    "scope": EMPTY_SCOPE,
                    "verifier": "claims-v2",
                    "problems": ["the judge request failed"],
                    "error": "claims check unavailable: the judge request failed.",
                },
                "paused",
            )
            (runs / "progress.json").write_text(json.dumps(paused))
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            (records / "run-test.json").write_text("{}")
            console = self.ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertEqual("verification_unavailable", detail["pause_reason"])
            self.assertIn("claims check unavailable", detail["pause_detail"])
            limited = synthetic_progress(
                "update_plan", {"plan": ["a"]}, {"plan": ["a"]}, "paused"
            )
            (runs / "progress.json").write_text(json.dumps(limited))
            detail = console.run_detail("run-test")
            self.assertIsNone(detail["pause_reason"])


if __name__ == "__main__":
    unittest.main()

