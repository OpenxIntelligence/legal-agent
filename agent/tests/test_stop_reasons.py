"""Increment 1: structured stop reasons with permitted next actions.

Every loop-level stop records a machine-readable stop_reason plus a
next_action in the run's notes, distinct from tool-driven stops.
All fixtures synthetic; the model client is scripted.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    RateLimitError,
)

from agent_kit.progress import resume
from agent_kit.settings import verification_enabled
from legal_agent.loop import provider_stop, run_agent
from tests import support
from tests.fake_model import Refused


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://meta.example/responses")


class StopReasonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

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

    def test_turn_slice_exhausted_and_resume_carries_the_stop(self) -> None:
        fake = support.scripted(
            self.root, [support.PLAN, support.LIST, support.READ, support.MEMO]
        )
        result = run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertEqual("turn_slice_exhausted", notes["stop_reason"])
        self.assertIn("more turns", notes["stop_action"])
        message = resume(self.root, result["run_id"])["message"]
        self.assertIn("more turns", message)
        self.assertIn("Last stop: turn_slice_exhausted", message)

    def test_repeated_actions_record_stall_no_progress(self) -> None:
        fake = support.scripted(
            self.root, [support.LIST, support.LIST, support.LIST]
        )
        result = run_agent(root=self.root, client=fake.client, turns=10)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertIn("in a row", notes["stall"])
        self.assertEqual("stall_no_progress", notes["stop_reason"])
        self.assertIn("different action", notes["stop_action"])
        message = resume(self.root, result["run_id"])["message"]
        self.assertIn("different action", message)

    def test_budget_exhaustion_records_its_reason(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "1")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "0")
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=5)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertIn("token budget exceeded", notes["budget_exceeded"])
        self.assertEqual("budget_exhausted", notes["stop_reason"])
        self.assertIn("RUN_TOKEN_BUDGET", notes["stop_action"])
        self.assertEqual(1, len(support.agent_requests(fake)))

    def test_context_cap_records_its_reason(self) -> None:
        self.set_env("MAX_CONTEXT_BYTES", "10")
        fake = support.scripted(self.root, [support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertIn("context too large", notes["context_exceeded"])
        self.assertEqual("context_exceeded", notes["stop_reason"])
        self.assertIn("MAX_CONTEXT_BYTES", notes["stop_action"])
        self.assertEqual([], fake.calls)

    def test_fatal_provider_error_pauses_without_retry_advice(self) -> None:
        fake = support.scripted(self.root, [support.LIST, support.MEMO])
        original = fake.responder

        def outage(kind, payload):
            if kind == "respond" and "tools" in payload:
                raise Refused("service_overloaded")
            return original(kind, payload)

        fake.responder = outage
        result = run_agent(root=self.root, client=fake.client, turns=10)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertIn("service_overloaded", notes["error"])
        self.assertEqual("provider_error", notes["stop_reason"])
        self.assertIn("Inspect the error", notes["stop_action"])

    def test_transient_provider_failure_invites_one_resume_retry(self) -> None:
        fake = support.scripted(self.root, [support.LIST])
        original = fake.responder

        def outage(kind, payload):
            if kind == "respond" and "tools" in payload:
                raise APITimeoutError(_request())
            return original(kind, payload)

        fake.responder = outage
        result = run_agent(root=self.root, client=fake.client, turns=10)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertEqual("provider_transient", notes["stop_reason"])
        self.assertIn("retry", notes["stop_action"])

    def test_provider_stop_classifies_transient_and_fatal(self) -> None:
        response_429 = httpx.Response(429, request=_request())
        for error in (
            APITimeoutError(_request()),
            APIConnectionError(request=_request()),
            RateLimitError("slow down", response=response_429, body=None),
        ):
            reason, _ = provider_stop(error)
            self.assertEqual("provider_transient", reason)
        reason, action = provider_stop(
            APIError("bad", _request(), body=None)
        )
        self.assertEqual("provider_error", reason)
        self.assertIn("Inspect", action)

    def test_malformed_response_pauses_with_its_reason(self) -> None:
        fake = support.scripted(self.root, [support.LIST])
        with mock.patch.object(
            fake.client.responses,
            "create",
            return_value=SimpleNamespace(output=None),
        ):
            result = run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertEqual("malformed model response", notes["error"])
        self.assertEqual("malformed_response", notes["stop_reason"])
        self.assertIn("try again", notes["stop_action"])

    def test_no_tool_call_on_the_last_turn_pauses(self) -> None:
        fake = support.scripted(self.root, [])
        original = fake.responder

        def text_only(kind, payload):
            if kind == "respond" and "tools" in payload:
                return {"output_text": "Still thinking; no tool yet."}
            return original(kind, payload)

        fake.responder = text_only
        result = run_agent(root=self.root, client=fake.client, turns=1)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertEqual("no_tool_call", notes["stop_reason"])
        self.assertIn("more turns", notes["stop_action"])

    @unittest.skipIf(
        verification_enabled(),
        "memo filing without verification; on-mode covered by test_verification",
    )
    def test_tool_driven_stop_carries_no_loop_reason(self) -> None:
        fake = support.scripted(self.root, [support.MEMO])
        result = run_agent(root=self.root, client=fake.client, turns=5)
        self.assertEqual("answered", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertIsNone(notes.get("stop_reason"))
        self.assertIsNone(notes.get("stop_action"))


class StopReasonConsoleTest(unittest.TestCase):
    def test_stop_fields_reach_run_detail(self) -> None:
        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            progress = {
                "run_id": "run-test",
                "verification": False,
                "model": "scripted",
                "started": "2026-01-01T00:00:00+00:00",
                "status": "paused",
                "turn": 2,
                "messages": [],
                "turns": [],
                "notes": {
                    "stop_reason": "provider_transient",
                    "stop_action": "Resume to retry the model call.",
                },
            }
            (runs / "progress.json").write_text(json.dumps(progress))
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            (records / "run-test.json").write_text("{}")
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertEqual("provider_transient", detail["stop_reason"])
            self.assertIn("retry", detail["stop_action"])
            # Legacy paused runs without the new notes stay blank, not invented.
            progress["notes"] = {}
            (runs / "progress.json").write_text(json.dumps(progress))
            detail = console.run_detail("run-test")
            self.assertIsNone(detail["stop_reason"])
            self.assertIsNone(detail["stop_action"])


if __name__ == "__main__":
    unittest.main()
