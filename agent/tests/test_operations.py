"""Milestone 7: usage accounting, budgets, config, context guard.

Every tool-level model call lands in a per-run usage ledger; token
budgets and context caps pause runs before the next model call; new
runs snapshot their configuration; and the console surfaces ledger
tokens and the new pause reasons. Offline only: synthetic fixtures,
temporary workspaces, mocked clients.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_kit import settings, tools, usage
from agent_kit.case_record import CaseRecord
from agent_kit.progress import (
    ContextMeasureError,
    config_snapshot,
    context_bytes,
    load_progress,
    new_run,
    tokens_spent,
)
from agent_kit.settings import ConfigError
from agent_kit.tools import analyze_document, check_signatures
from legal_agent.loop import resume_run, run_agent
from scripts import describe
from tests import support
from verification import verify_checklist, verify_claims


class EnvCase(unittest.TestCase):
    def set_env(self, key, value):
        sentinel = object()
        old = os.environ.get(key, sentinel)

        def restore():
            if old is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class LedgerTest(EnvCase):
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

    def entries(self):
        path = self.root / "workspace/runs" / self.run_id / "usage.json"
        return json.loads(path.read_text())

    def test_analyze_records_extract_and_analyze(self) -> None:
        analyze_document.run(
            self.ctx, {"document_ids": ["DOC-REPORT"], "request": "x"}
        )
        by_purpose = {e["purpose"]: e for e in self.entries()}
        self.assertEqual(
            (3000, 900),
            (
                by_purpose["extract_text"]["input_tokens"],
                by_purpose["extract_text"]["output_tokens"],
            ),
        )
        self.assertEqual(
            (3500, 300),
            (
                by_purpose["analyze_document"]["input_tokens"],
                by_purpose["analyze_document"]["output_tokens"],
            ),
        )
        totals = usage.totals(self.root, self.run_id)
        self.assertEqual(
            (2, 6500, 1200),
            (totals["calls"], totals["input_tokens"], totals["output_tokens"]),
        )

    def test_cached_reads_record_no_new_calls(self) -> None:
        analyze_document.run(
            self.ctx, {"document_ids": ["DOC-REPORT"], "request": "x"}
        )
        analyze_document.run(
            self.ctx, {"document_ids": ["DOC-REPORT"], "request": "x"}
        )
        self.assertEqual(2, len(self.entries()))

    def test_judges_and_segmentation_record(self) -> None:
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        verify_checklist.run_verification(self.ctx, support.CHECKLIST_ITEMS)
        verify_claims.run_verification(self.ctx, support.MEMO[1])
        support.release(self.root)
        check_signatures.run(
            self.ctx,
            {
                "document_id": "DOC-CERT",
                "page": 1,
                "signer": "Dana Okafor",
                "reference_document_id": "DOC-REF",
                "reference_page": 1,
            },
        )
        purposes = sorted(e["purpose"] for e in self.entries())
        self.assertEqual(
            [
                "checklist_judge",
                "claims_judge",
                "sam_segment",
                "sam_segment",
                "signature_judge",
            ],
            purposes,
        )
        self.assertTrue(all(e["model"] == "scripted" for e in self.entries()))

    def test_totals_without_a_ledger_are_zero(self) -> None:
        self.assertEqual(
            (0, 0, 0, {}),
            (
                usage.totals(self.root, self.run_id)["calls"],
                usage.totals(self.root, self.run_id)["input_tokens"],
                usage.totals(self.root, self.run_id)["output_tokens"],
                usage.totals(self.root, self.run_id)["by_purpose"],
            ),
        )
        self.assertEqual(0, tokens_spent(self.root, self.run_id))

    def test_success_entries_carry_latency_and_a_clean_flag(self) -> None:
        analyze_document.run(
            self.ctx, {"document_ids": ["DOC-REPORT"], "request": "x"}
        )
        for entry in self.entries():
            self.assertIsInstance(entry["latency_ms"], float)
            self.assertGreaterEqual(entry["latency_ms"], 0.0)
            self.assertFalse(entry["failed"])
            self.assertEqual("", entry["error"])
        totals = usage.totals(self.root, self.run_id)
        self.assertEqual(0, totals["failures"])
        self.assertEqual(totals["calls"], totals["latency_calls"])

    def test_failed_calls_keep_null_tokens_and_count_separately(self) -> None:
        entry = usage.record_failure(
            self.root, self.run_id, "claims_judge", ValueError("boom" * 100)
        )
        self.assertTrue(entry["failed"])
        self.assertIsNone(entry["input_tokens"])
        self.assertIsNone(entry["output_tokens"])
        self.assertLessEqual(len(entry["error"]), 161)
        self.assertIn("boom", entry["error"])
        totals = usage.totals(self.root, self.run_id)
        self.assertEqual((1, 1, 0, 0), (totals["calls"], totals["failures"], totals["input_tokens"], totals["output_tokens"]))
        self.assertEqual(1, totals["by_purpose"]["claims_judge"]["failures"])

    def test_legacy_entries_without_new_fields_still_total(self) -> None:
        path = self.root / "workspace/runs" / self.run_id / "usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "at": "2026-01-01T00:00:00+00:00",
                        "purpose": "extract_text",
                        "model": "m",
                        "input_tokens": 10,
                        "output_tokens": 5,
                    }
                ]
            )
        )
        totals = usage.totals(self.root, self.run_id)
        self.assertEqual((1, 0, 10, 5), (totals["calls"], totals["failures"], totals["input_tokens"], totals["output_tokens"]))
        self.assertEqual(0, totals["latency_calls"])

    def test_claims_judge_failure_is_ledgered_as_failed(self) -> None:
        from tests.fake_model import Refused

        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "CLAIMS TO CHECK" in json.dumps(payload):
                raise Refused("judge overloaded")
            return original(kind, payload)

        self.fake.responder = broken
        result = verify_claims.run_verification(self.ctx, support.MEMO[1])
        self.assertEqual("unavailable", result["outcome"])
        (entry,) = self.entries()
        self.assertEqual("claims_judge", entry["purpose"])
        self.assertTrue(entry["failed"])
        self.assertIsNone(entry["input_tokens"])
        self.assertIn("overloaded", entry["error"])


class RecoveryTest(unittest.TestCase):
    """D6: a result committed before a missed checkpoint returns once."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([support.checklist_item("timing")])

    def call(self, name, args):
        return {"name": name, "arguments": json.dumps(args), "call_id": "c1"}

    def read_all(self) -> None:
        from agent_kit.tools import analyze_document

        ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze_document.run(
            ctx, {"document_ids": list(support.INITIAL), "request": "x"}
        )

    def requests(self):
        return CaseRecord(self.root, self.run_id).data["requests"]

    def test_committed_result_returned_once_after_a_missed_checkpoint(self) -> None:
        self.read_all()
        call = self.call("request_evidence", {"item": "timing", "need": "x"})
        first = tools.run(self.fake.client, self.root, self.run_id, call, op_seq=1)
        self.assertTrue(first.get("escalated"))
        # No conversation checkpoint is saved: the crash window.
        second = tools.run(self.fake.client, self.root, self.run_id, call, op_seq=1)
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.requests()))

    def test_next_sequence_reexecutes_instead_of_matching(self) -> None:
        self.read_all()
        call = self.call("request_evidence", {"item": "timing", "need": "x"})
        tools.run(self.fake.client, self.root, self.run_id, call, op_seq=1)
        again = tools.run(self.fake.client, self.root, self.run_id, call, op_seq=2)
        self.assertIn("still open", again.get("error", ""))
        self.assertEqual(1, len(self.requests()))

    def test_error_results_are_never_recorded(self) -> None:
        self.read_all()
        bad = self.call("request_evidence", {"item": "nope", "need": "x"})
        result = tools.run(self.fake.client, self.root, self.run_id, bad, op_seq=1)
        self.assertIn("error", result)
        slot = self.root / "workspace/runs" / self.run_id / "pending_op.json"
        self.assertFalse(slot.exists())

    def test_loop_writes_the_slot_per_turn(self) -> None:
        from legal_agent.loop import run_agent

        fake = support.scripted(self.root, [support.CHECKLIST])
        result = run_agent(root=self.root, client=fake.client, turns=1)
        slot = json.loads(
            (self.root / "workspace/runs" / result["run_id"] / "pending_op.json")
            .read_text()
        )
        self.assertEqual(1, slot["seq"])
        self.assertEqual("set_checklist", slot["name"])
        before = len(CaseRecord(self.root, result["run_id"]).data["audit"])
        repeated = tools.run(
            fake.client,
            self.root,
            result["run_id"],
            self.call("set_checklist", support.CHECKLIST[1]),
            op_seq=1,
        )
        self.assertEqual(slot["result"], repeated)
        after = len(CaseRecord(self.root, result["run_id"]).data["audit"])
        self.assertEqual(before, after)


class BudgetTest(EnvCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def test_budget_pauses_before_the_next_model_call(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "1")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "0")
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=3)
        self.assertEqual("paused", result["status"])
        self.assertIn("token budget exceeded", result["notes"]["budget_exceeded"])
        self.assertEqual(1, len(support.agent_requests(fake)))

    def test_resume_blocked_until_budget_raised(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "1")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "0")
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=3)
        stuck = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=2
        )
        self.assertEqual("paused", stuck["status"])
        self.assertEqual(1, len(support.agent_requests(fake)))
        self.set_env("RUN_TOKEN_BUDGET", "1000000")
        resumed = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=1
        )
        self.assertEqual(2, len(support.agent_requests(fake)))
        self.assertEqual("paused", resumed["status"])

    def test_budget_unset_is_unlimited(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", None)
        self.assertIsNone(settings.run_token_budget())
        fake = support.scripted(self.root, [support.LIST, support.MEMO])
        result = run_agent(root=self.root, client=fake.client, turns=2)
        self.assertIsNone(result["notes"].get("budget_exceeded"))

    def test_invalid_budget_is_a_config_error(self) -> None:
        for value in ("not-a-number", "0", "-5"):
            self.set_env("RUN_TOKEN_BUDGET", value)
            with self.assertRaises(ConfigError) as raised:
                settings.run_token_budget()
            self.assertIn("RUN_TOKEN_BUDGET", str(raised.exception))

    def test_invalid_budget_refuses_the_run(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "0")
        fake = support.scripted(self.root, [support.LIST, support.MEMO])
        with self.assertRaises(ConfigError):
            run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual([], fake.calls)
        runs = self.root / "workspace" / "runs"
        self.assertFalse(runs.is_dir() and any(runs.iterdir()))

    def test_invalid_budget_refuses_resume(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "1")
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=3)
        self.set_env("RUN_TOKEN_BUDGET", "bogus")
        with self.assertRaises(ConfigError):
            resume_run(
                root=self.root, run_id=result["run_id"], client=fake.client, turns=1
            )


class ContextTest(EnvCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def test_context_cap_pauses_before_any_model_call(self) -> None:
        self.set_env("MAX_CONTEXT_BYTES", "10")
        fake = support.scripted(self.root, [support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual("paused", result["status"])
        self.assertIn("context too large", result["notes"]["context_exceeded"])
        self.assertEqual([], fake.calls)

    def test_resume_blocked_until_cap_raised(self) -> None:
        self.set_env("MAX_CONTEXT_BYTES", "10")
        fake = support.scripted(self.root, [support.LIST, support.MEMO])
        result = run_agent(root=self.root, client=fake.client, turns=2)
        stuck = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=2
        )
        self.assertEqual("paused", stuck["status"])
        self.assertEqual([], fake.calls)
        self.set_env("MAX_CONTEXT_BYTES", None)
        resumed = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=2
        )
        self.assertTrue(support.agent_requests(fake))
        self.assertIn(resumed["status"], ("running", "paused", "answered"))

    def test_context_default_is_generous(self) -> None:
        self.set_env("MAX_CONTEXT_BYTES", None)
        self.assertEqual(1500000, settings.max_context_bytes())

    def test_invalid_context_cap_is_a_config_error(self) -> None:
        for value in ("bogus", "0", "-10"):
            self.set_env("MAX_CONTEXT_BYTES", value)
            with self.assertRaises(ConfigError) as raised:
                settings.max_context_bytes()
            self.assertIn("MAX_CONTEXT_BYTES", str(raised.exception))

    def test_invalid_context_cap_refuses_the_run(self) -> None:
        self.set_env("MAX_CONTEXT_BYTES", "bogus")
        fake = support.scripted(self.root, [support.LIST])
        with self.assertRaises(ConfigError):
            run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual([], fake.calls)

    def test_unserializable_context_raises_a_measure_error(self) -> None:
        with self.assertRaises(ContextMeasureError):
            context_bytes([{"role": "user", "content": object()}])

    def test_unmeasurable_context_pauses_with_an_explicit_note(self) -> None:
        fake = support.scripted(self.root, [support.LIST])
        with mock.patch(
            "legal_agent.loop.context_bytes",
            side_effect=ContextMeasureError("cannot serialize context"),
        ):
            result = run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual("paused", result["status"])
        self.assertIn("unknown", result["notes"]["context_exceeded"])
        self.assertEqual([], fake.calls)

    def test_resume_reports_unknown_saved_context(self) -> None:
        self.set_env("MAX_CONTEXT_BYTES", "10")
        fake = support.scripted(self.root, [support.LIST, support.MEMO])
        result = run_agent(root=self.root, client=fake.client, turns=2)
        with mock.patch(
            "agent_kit.progress.context_bytes",
            side_effect=ContextMeasureError("cannot serialize context"),
        ):
            stuck = resume_run(
                root=self.root, run_id=result["run_id"], client=fake.client, turns=2
            )
        self.assertEqual("paused", stuck["status"])
        self.assertIn("unknown", stuck["note"])
        self.assertEqual([], fake.calls)


class ConfigTest(EnvCase):
    def test_new_runs_snapshot_their_config(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("RUN_TOKEN_BUDGET", None)
        self.set_env("SYNTHESIS_RESERVE_TOKENS", None)
        self.set_env("MAX_CONTEXT_BYTES", None)
        run_id = new_run(root, instructions="i", objective="o", verification=True)
        config = load_progress(root, run_id)["config"]
        self.assertEqual(
            {
                "config_version": 3,
                "model": settings.model(),
                "verification": True,
                "run_token_budget": None,
                "synthesis_reserve_tokens": 20000,
                "max_context_bytes": 1500000,
                "errors": [],
            },
            config,
        )

    def test_snapshot_records_config_errors_without_raising(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "bogus")
        self.set_env("MAX_CONTEXT_BYTES", "0")
        snapshot = config_snapshot(True)
        self.assertIsNone(snapshot["run_token_budget"])
        self.assertEqual(1500000, snapshot["max_context_bytes"])
        self.assertEqual(2, len(snapshot["errors"]))
        self.assertTrue(any("RUN_TOKEN_BUDGET" in e for e in snapshot["errors"]))
        self.assertTrue(any("MAX_CONTEXT_BYTES" in e for e in snapshot["errors"]))

    def test_legacy_progress_without_config_loads(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o", verification=False)
        path = root / "workspace/runs" / run_id / "progress.json"
        data = json.loads(path.read_text())
        del data["config"]
        path.write_text(json.dumps(data))
        self.assertIsNone(load_progress(root, run_id).get("config"))

    def test_describe_reports_config(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        payload = describe.describe(root)
        self.assertEqual(4, payload["documents"])
        self.assertEqual(3, payload["config"]["config_version"])
        self.assertEqual(settings.model(), payload["config"]["model"])

    def test_resume_records_a_model_change_and_warns(self) -> None:
        from agent_kit.progress import resume, save_progress

        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("MODEL_API_MODEL", "model-one")
        run_id = new_run(root, instructions="i", objective="o", verification=True)
        save_progress(root, run_id, [], status="paused")
        self.set_env("MODEL_API_MODEL", "model-two")
        step = resume(root, run_id)
        self.assertIn("model-one", step["message"])
        self.assertIn("model-two", step["message"])
        self.assertIn("reassessment", step["message"])
        (record,) = load_progress(root, run_id)["notes"]["config_changes"]
        self.assertEqual(
            {"was": "model-one", "now": "model-two"},
            record["changes"]["model"],
        )
        audit = CaseRecord(root, run_id).data["audit"]
        self.assertTrue(any(a["action"] == "config_changed" for a in audit))

    def test_model_change_marks_prior_claims_stale(self) -> None:
        from agent_kit.progress import resume, save_progress
        from quality.records import evidence_fingerprint

        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("MODEL_API_MODEL", "model-one")
        run_id = new_run(root, instructions="i", objective="o", verification=True)
        case = CaseRecord(root, run_id)
        case.record_memo_check(
            "claims",
            [],
            outcome="passed",
            scope={
                "expected": ["conclusion"],
                "assessed": ["conclusion"],
                "evidence": evidence_fingerprint({"facts": []}),
            },
            fact_index={},
        )
        save_progress(root, run_id, [], status="paused")
        self.set_env("MODEL_API_MODEL", "model-two")
        resume(root, run_id)
        stale = CaseRecord(root, run_id).claims_staleness()["stale"]
        self.assertEqual(["conclusion"], list(stale))
        self.assertIn("different model", stale["conclusion"])

    def test_budget_change_records_without_staling_claims(self) -> None:
        from agent_kit.progress import resume, save_progress
        from quality.records import evidence_fingerprint

        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("MODEL_API_MODEL", "model-one")
        self.set_env("RUN_TOKEN_BUDGET", None)
        run_id = new_run(root, instructions="i", objective="o", verification=True)
        case = CaseRecord(root, run_id)
        case.record_memo_check(
            "claims",
            [],
            outcome="passed",
            scope={
                "expected": ["conclusion"],
                "assessed": ["conclusion"],
                "evidence": evidence_fingerprint({"facts": []}),
            },
            fact_index={},
        )
        save_progress(root, run_id, [], status="paused")
        self.set_env("RUN_TOKEN_BUDGET", "1000000")
        step = resume(root, run_id)
        self.assertIn("run_token_budget", step["message"])
        stale = CaseRecord(root, run_id).claims_staleness()
        self.assertEqual({}, stale["stale"])


class OpsConsoleTest(unittest.TestCase):
    def test_context_bytes_in_run_state(self) -> None:
        ui = support.console_ui()
        messages = [{"role": "user", "content": "hello"}]
        state = ui.run_state({"messages": messages, "notes": {}}, [])
        self.assertEqual(len(json.dumps(messages)), state["context_bytes"])

    def test_unmeasurable_context_in_run_state_is_unknown(self) -> None:
        ui = support.console_ui()
        state = ui.run_state(
            {"messages": [{"role": "user", "content": object()}], "notes": {}}, []
        )
        self.assertIsNone(state["context_bytes"])

    def test_tool_tokens_and_pause_notes_reach_run_detail(self) -> None:
        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            (runs / "progress.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-test",
                        "verification": True,
                        "model": "scripted",
                        "started": "2026-01-01T00:00:00+00:00",
                        "status": "paused",
                        "turn": 1,
                        "messages": [],
                        "turns": [],
                        "notes": {"budget_exceeded": "token budget exceeded: 5 of 1."},
                    }
                )
            )
            (runs / "usage.json").write_text(
                json.dumps(
                    [
                        {
                            "purpose": "extract_text",
                            "model": "scripted",
                            "input_tokens": 3000,
                            "output_tokens": 900,
                        }
                    ]
                )
            )
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            (records / "run-test.json").write_text("{}")
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertEqual(3900, detail["metrics"]["tool_tokens"])
            self.assertIn("token budget", detail["budget_exceeded"])
            self.assertIsNone(detail["context_exceeded"])
            (runs / "usage.json").unlink()
            detail = console.run_detail("run-test")
            self.assertIsNone(detail["metrics"]["tool_tokens"])


if __name__ == "__main__":
    unittest.main()
