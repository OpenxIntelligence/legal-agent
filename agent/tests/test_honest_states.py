"""Milestone 3: honest signature and document-processing states.

Signatures get a real SAM-plus-judge comparison with a strict verdict
contract (visual consistency only, never authentication); attempted
reads are distinguished from successful reads throughout the case
record, escalation, and UI. Offline only: synthetic case, temporary
workspaces, mocked clients.
"""

from __future__ import annotations

import json
import unittest

from agent_kit import tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import analyze_document, check_signatures
from tests import support
from tests.fake_model import Refused
from verification import verify_evidence_request


def signature_inputs(fake) -> list:
    return [
        call[1]
        for call in fake.calls
        if call[0] == "respond" and "SIGNATURE COMPARISON" in json.dumps(call[1])
    ]


def break_signature(fake, output_text=None, error=None, segments=False):
    original = fake.responder

    def responder(kind, payload):
        if segments and kind == "segment":
            raise error
        if kind == "respond" and "SIGNATURE COMPARISON" in json.dumps(payload):
            if error is not None and not segments:
                raise error
            return {
                "output_text": output_text,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }
        return original(kind, payload)

    fake.responder = responder
    return original


class SignatureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        support.release(self.root)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def check(self, questioned="DOC-CERT", reference="DOC-REF", **kw):
        args = {
            "document_id": questioned,
            "page": 1,
            "signer": "Dana Okafor",
            "reference_document_id": reference,
            "reference_page": 1,
            **kw,
        }
        return check_signatures.run(self.ctx, args)


class SignatureTest(SignatureCase):
    def test_verdicts_record_with_boxes_and_honest_reason(self) -> None:
        for verdict in ("consistent", "inconsistent", "unclear"):
            with self.subTest(verdict=verdict):
                self.setUp()
                self.fake = support.scripted(
                    self.root,
                    [],
                    signature=lambda text, v=verdict: {
                        "verdict": v,
                        "reason": "scripted shapes",
                    },
                )
                self.ctx = tools.Context(
                    self.fake.client, self.root, self.run_id, self.case, True
                )
                result = self.check()
                self.assertEqual(verdict, result["verdict"])
                self.assertIn("visual consistency only", result["reason"])
                (record,) = self.case.data["signature_checks"]
                self.assertEqual(1, len(record["boxes"]))
                self.assertEqual(1, len(record["reference"]["boxes"]))
                flags = [
                    f
                    for f in self.case.data["flags"]
                    if f["kind"] == "signature"
                ]
                self.assertEqual(verdict != "consistent", bool(flags))

    def test_unsigned_when_the_questioned_page_has_no_ink(self) -> None:
        result = self.check(questioned="DOC-REPORT")
        self.assertEqual("unsigned", result["verdict"])
        self.assertEqual([], signature_inputs(self.fake))
        (record,) = self.case.data["signature_checks"]
        self.assertEqual([], record["boxes"])
        self.assertTrue(record["reference"]["boxes"])
        self.assertEqual("signature", self.case.data["flags"][0]["kind"])

    def test_empty_reference_page_is_refused_with_guidance(self) -> None:
        result = self.check(reference="DOC-REPORT")
        self.assertIn("no signature-like ink", result["error"])
        self.assertIn("reference page", result["error"])
        self.assertEqual([], self.case.data["signature_checks"])

    def test_invalid_judge_output_is_an_error_not_a_verdict(self) -> None:
        break_signature(self.fake, "{not json")
        result = self.check()
        self.assertIn("invalid JSON", result["error"])
        self.assertEqual([], self.case.data["signature_checks"])
        self.setUp()
        self.fake = support.scripted(
            self.root,
            [],
            signature=lambda text: {"verdict": "forged", "reason": "x"},
        )
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        result = self.check()
        self.assertIn("invalid verdict", result["error"])
        self.assertEqual([], self.case.data["signature_checks"])

    def test_provider_failures_are_errors_never_verdicts(self) -> None:
        break_signature(
            self.fake, error=Refused("sam overloaded"), segments=True
        )
        result = self.check()
        self.assertIn("segmentation failed", result["error"])
        self.assertEqual([], self.case.data["signature_checks"])
        self.setUp()
        break_signature(self.fake, error=Refused("judge overloaded"))
        result = self.check()
        self.assertIn("comparison unavailable", result["error"])
        self.assertEqual([], self.case.data["signature_checks"])

    def test_prompt_contract_and_honest_definition(self) -> None:
        prompt = check_signatures.PROMPT
        self.assertIn("SIGNATURE COMPARISON", prompt)
        self.assertIn("visual-consistency check only", prompt)
        self.assertIn("not forgery detection or authentication", prompt)
        for token in ("Lindgren", "Okafor", "DOC-", "go-live", "Severity", "UAT"):
            self.assertNotIn(token, prompt)
        self.assertIn(
            "visual consistency only",
            check_signatures.DEFINITION["description"],
        )


class ReadStatesTest(unittest.TestCase):
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

    def break_memo(self, error=None):
        import openai

        original = self.fake.responder
        failure = error if error is not None else Refused("simulated outage")

        def broken(kind, payload):
            if kind == "respond" and "DOC-MEMO" in json.dumps(payload):
                raise failure
            return original(kind, payload)

        self.fake.responder = broken
        return original

    def break_memo_transient(self):
        import openai

        return self.break_memo(openai.APITimeoutError("simulated timeout"))

    def test_failed_reads_do_not_count_as_read(self) -> None:
        self.break_memo()
        result = analyze_document.run(
            self.ctx,
            {"document_ids": ["DOC-REPORT", "DOC-MEMO"], "request": "x"},
        )
        by_id = {d["document_id"]: d for d in result["documents"]}
        self.assertTrue(by_id["DOC-REPORT"]["evidence"])
        self.assertTrue(by_id["DOC-MEMO"]["note"].startswith("error"))
        self.assertEqual(["DOC-REPORT"], self.case.data["read_ids"])
        self.assertIn("DOC-MEMO", self.case.data["failed_reads"])
        entry = self.case.data["failed_reads"]["DOC-MEMO"]
        self.assertIn("outage", entry["note"])
        self.assertEqual(1, entry["attempts"])

    def test_successful_retry_clears_the_failure(self) -> None:
        original = self.break_memo()
        analyze_document.run(
            self.ctx, {"document_ids": ["DOC-MEMO"], "request": "x"}
        )
        self.assertIn("DOC-MEMO", self.case.data["failed_reads"])
        self.fake.responder = original
        analyze_document.run(
            self.ctx, {"document_ids": ["DOC-MEMO"], "request": "x"}
        )
        self.assertEqual({}, self.case.data["failed_reads"])
        self.assertIn("DOC-MEMO", self.case.data["read_ids"])

    def test_escalation_names_failed_reads_distinctly(self) -> None:
        self.case.set_checklist([support.checklist_item("timing")])
        self.break_memo_transient()
        analyze_document.run(
            self.ctx,
            {"document_ids": ["DOC-REPORT", "DOC-MEMO"], "request": "x"},
        )
        self.case.mark_read(["DOC-MINUTES", "DOC-REF"])
        self.assertEqual(
            "transient", self.case.failure_class("DOC-MEMO")
        )
        result = verify_evidence_request.run_verification(
            self.ctx, {"item": "timing", "need": "x"}
        )
        self.assertFalse(result["passed"])
        self.assertIn("could not be read", result["feedback"])
        self.assertIn("DOC-MEMO", result["feedback"])
        self.assertIn("Re-read them", result["feedback"])

    def test_internal_failures_demand_diagnosis_not_rereads(self) -> None:
        self.case.set_checklist([support.checklist_item("timing")])
        self.break_memo()
        analyze_document.run(
            self.ctx,
            {"document_ids": ["DOC-REPORT", "DOC-MEMO"], "request": "x"},
        )
        self.assertEqual("internal", self.case.failure_class("DOC-MEMO"))
        result = verify_evidence_request.run_verification(
            self.ctx, {"item": "timing", "need": "x"}
        )
        self.assertFalse(result["passed"])
        self.assertIn("Diagnose", result["feedback"])
        self.assertIn("DOC-MEMO", result["feedback"])
        self.assertNotIn("Re-read them", result["feedback"])

    def test_legacy_records_without_failed_reads_still_work(self) -> None:
        path = self.root / "workspace/case_records" / f"{self.run_id}.json"
        before = json.loads(path.read_text())
        del before["failed_reads"]
        path.write_text(json.dumps(before))
        case = CaseRecord(self.root, self.run_id)
        case.set_checklist([support.checklist_item("timing")])
        ctx = tools.Context(
            self.fake.client, self.root, self.run_id, case, True
        )
        result = verify_evidence_request.run_verification(
            ctx, {"item": "timing", "need": "x"}
        )
        self.assertIn("read every visible document", result["feedback"])
        case.mark_read_failed({"DOC-MEMO": "error: outage"})
        self.assertEqual(
            {
                "DOC-MEMO": {
                    "note": "error: outage",
                    "attempts": 1,
                    "class": "unknown",
                }
            },
            case.data["failed_reads"],
        )
        self.assertEqual(1, case.read_attempts("DOC-MEMO"))
        self.assertEqual(0, case.read_attempts("DOC-REPORT"))
        self.assertEqual("unknown", case.failure_class("DOC-MEMO"))

    def test_malformed_failure_entries_raise_loudly(self) -> None:
        path = self.root / "workspace/case_records" / f"{self.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"run_id": self.run_id, "failed_reads": {"DOC-X": {"attempts": "many"}}}))
        case = CaseRecord(self.root, self.run_id)
        with self.assertRaises(CaseError):
            case.read_attempts("DOC-X")

    def test_ui_analyzed_set_excludes_failed_reads(self) -> None:
        ui = support.console_ui()
        events = [
            {
                "event_type": "tool_call",
                "actor": "agent",
                "timestamp": "",
                "payload": {
                    "arguments": {"document_ids": ["DOC-REPORT", "DOC-MEMO"]}
                },
            },
            {
                "event_type": "tool_result",
                "actor": "tool",
                "timestamp": "",
                "payload": {
                    "tool_name": "analyze_document",
                    "result": {
                        "documents": [
                            {
                                "document_id": "DOC-REPORT",
                                "evidence": [],
                                "note": "",
                            },
                            {
                                "document_id": "DOC-MEMO",
                                "evidence": [],
                                "note": "error: outage",
                            },
                        ]
                    },
                },
            },
        ]
        state = ui.run_state({"notes": {}}, events)
        self.assertEqual(["DOC-REPORT"], state["analyzed_ids"])

    def test_ui_analyzed_ignores_an_omitted_result(self) -> None:
        ui = support.console_ui()
        events = [
            {
                "event_type": "tool_call",
                "actor": "agent",
                "timestamp": "",
                "payload": {
                    "arguments": {"document_ids": ["DOC-REPORT", "DOC-MEMO"]}
                },
            },
            {
                "event_type": "tool_result",
                "actor": "tool",
                "timestamp": "",
                "payload": {
                    "tool_name": "analyze_document",
                    "result": {
                        "documents": [
                            {
                                "document_id": "DOC-REPORT",
                                "evidence": [],
                                "note": "",
                            }
                        ]
                    },
                },
            },
        ]
        state = ui.run_state({"notes": {}}, events)
        self.assertEqual(["DOC-REPORT"], state["analyzed_ids"])

    def test_ui_analyzed_ignores_a_failed_tool_call(self) -> None:
        ui = support.console_ui()
        events = [
            {
                "event_type": "tool_call",
                "actor": "agent",
                "timestamp": "",
                "payload": {"arguments": {"document_ids": ["DOC-REPORT"]}},
            },
            {
                "event_type": "tool_result",
                "actor": "tool",
                "timestamp": "",
                "payload": {
                    "tool_name": "analyze_document",
                    "result": {"error": "confidential documents cannot be read: DOC-X"},
                },
            },
        ]
        state = ui.run_state({"notes": {}}, events)
        self.assertEqual([], state["analyzed_ids"])


if __name__ == "__main__":
    unittest.main()
