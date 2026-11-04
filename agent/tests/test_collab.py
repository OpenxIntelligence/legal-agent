"""C-0 collaboration backbone: drafts, threads, directives, context, edges.

Optimistic concurrency on drafts, append-only threads, a strict
directive lifecycle, quarantined-by-default context, and activity
edges written by the same mutation paths that write the objects.
Unknown refs and illegal transitions fail closed.
"""

from __future__ import annotations

import json
import os
import random
import re
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from agent_kit.artifacts.kernel import TEMPLATE_AUDIT, export_markdown
from agent_kit.case_record import (
    CaseError,
    CaseRecord,
    collaboration_export,
)
from agent_kit.progress import new_run, load_progress
from agent_kit.settings import verification_enabled
from legal_agent.loop import resume_run, run_agent, steering_messages
from scripts import collab
from tests import support


HUMAN = {"type": "human", "id": "associate"}
AGENT = {"type": "agent", "id": "researcher", "agent_role": "research"}
ATTORNEY = {"type": "human", "id": "supervisor", "role": "attorney"}
ADMIN = {"type": "human", "id": "ops", "role": "admin"}
CLIENT = {"type": "human", "id": "client", "role": "client"}


class ComposerDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))

    def test_create_and_version(self) -> None:
        first = self.case.composer_save("Clause 8", "first words", HUMAN)
        self.assertEqual("DR1", first["id"])
        self.assertEqual(1, first["version"])
        self.assertEqual("draft", first["state"])
        second = self.case.composer_save(
            "Clause 8", "revised words", HUMAN,
            draft_id="DR1", base_version=1)
        self.assertEqual(2, second["version"])
        self.assertEqual("revised words", second["body_md"])
        stored = self.case.composer_drafts()[0]
        self.assertEqual(2, len(stored["history"]))
        self.assertEqual("first words", stored["history"][0]["body_md"])

    def test_stale_base_fails_without_overwriting(self) -> None:
        self.case.composer_save("T", "v1", HUMAN)
        self.case.composer_save("T", "v2", HUMAN, draft_id="DR1", base_version=1)
        with self.assertRaises(CaseError):
            self.case.composer_save(
                "T", "stale write", HUMAN, draft_id="DR1", base_version=1)
        self.assertEqual("v2", self.case.composer_drafts()[0]["body_md"])

    def test_empty_and_unknown_drafts_rejected(self) -> None:
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "   ", HUMAN)
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "x", HUMAN, draft_id="DR9")

    def test_get_returns_full_history(self) -> None:
        self.case.composer_save("T", "v1", HUMAN)
        self.case.composer_save("T", "v2", HUMAN, draft_id="DR1", base_version=1)
        got = self.case.composer_get("DR1")
        self.assertEqual(2, len(got["history"]))
        self.assertEqual("v1", got["history"][0]["body_md"])
        self.assertEqual("v2", got["body_md"])
        with self.assertRaises(CaseError):
            self.case.composer_get("DR9")

    def test_sent_is_final(self) -> None:
        self.case.composer_save("T", "ready", HUMAN)
        sent = self.case.composer_send("DR1", HUMAN)
        self.assertEqual("sent", sent["state"])
        with self.assertRaises(CaseError):
            self.case.composer_send("DR1", HUMAN)
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "late edit", HUMAN, draft_id="DR1")


class ThreadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))
        self.case.composer_save("T", "argue this", HUMAN)

    def test_open_reply_resolve_reopen(self) -> None:
        thread = self.case.thread_open("draft", "DR1", "why?", HUMAN)
        self.assertEqual("TH1", thread["id"])
        self.assertEqual("M1", thread["messages"][0]["id"])
        replied = self.case.thread_reply("TH1", "because.", AGENT)
        self.assertEqual(2, len(replied["messages"]))
        self.assertEqual("M2", replied["messages"][1]["id"])
        resolved = self.case.thread_resolve("TH1", HUMAN, "settled")
        self.assertEqual("resolved", resolved["state"])
        with self.assertRaises(CaseError):
            self.case.thread_reply("TH1", "too late", HUMAN)
        with self.assertRaises(CaseError):
            self.case.thread_reopen("TH1", HUMAN, "")
        reopened = self.case.thread_reopen("TH1", HUMAN, "new letter")
        self.assertEqual("open", reopened["state"])

    def test_unknown_objects_and_bad_anchors_rejected(self) -> None:
        with self.assertRaises(CaseError):
            self.case.thread_open("draft", "DR9", "x", HUMAN)
        with self.assertRaises(CaseError):
            self.case.thread_open("memo", "M1", "x", HUMAN)
        with self.assertRaises(CaseError):
            self.case.thread_open(
                "draft", "DR1", "x", HUMAN,
                anchor={"start": 5, "end": 2})
        with self.assertRaises(CaseError):
            self.case.thread_reply("TH9", "x", HUMAN)

    def test_message_edit_is_stamped(self) -> None:
        self.case.thread_open("draft", "DR1", "first", HUMAN)
        self.case.thread_reply("TH1", "second", HUMAN)
        edited = self.case.thread_edit_message("TH1", "M2", "second, fixed", HUMAN)
        fixed = [m for m in edited["messages"] if m["id"] == "M2"][0]
        self.assertEqual("second, fixed", fixed["body_md"])
        self.assertTrue(fixed["edited_at"])
        self.assertEqual(2, len(edited["messages"]))
        with self.assertRaises(CaseError):
            self.case.thread_edit_message("TH1", "M9", "x", HUMAN)
        with self.assertRaises(CaseError):
            self.case.thread_edit_message("TH1", "M2", "   ", HUMAN)
        self.case.thread_resolve("TH1", HUMAN)
        with self.assertRaises(CaseError):
            self.case.thread_edit_message("TH1", "M2", "late fix", HUMAN)

    def test_anchor_version_and_agent_reply_surface(self) -> None:
        self.case.thread_open(
            "draft", "DR1", "about this span", HUMAN, version_id="v1",
            anchor={"start": 0, "end": 5, "quoted": "argue"})
        self.case.thread_reply("TH1", "on it.", AGENT)
        summary = self.case.collab_summary()
        thread = summary["threads"][0]
        self.assertEqual("v1", thread["version_id"])
        self.assertEqual(0, thread["anchor"]["start"])
        self.assertEqual(5, thread["anchor"]["end"])
        self.assertEqual("argue", thread["anchor"]["quoted"])
        self.assertTrue(thread["version_current"])
        self.assertTrue(thread["anchor_match"])
        reply = thread["messages"][1]
        self.assertEqual("agent", reply["author"]["type"])
        self.assertEqual("research", reply["author"]["agent_role"])

    def test_bad_versions_and_mismatched_quotes_rejected(self) -> None:
        with self.assertRaises(CaseError):
            self.case.thread_open("draft", "DR1", "x", HUMAN, version_id="v9")
        with self.assertRaises(CaseError):
            self.case.thread_open("draft", "DR1", "x", HUMAN, version_id="latest")
        with self.assertRaises(CaseError):
            self.case.thread_open(
                "draft", "DR1", "x", HUMAN,
                anchor={"start": 0, "end": 5, "quoted": "WRONG"})
        with self.assertRaises(CaseError):
            self.case.thread_open(
                "draft", "DR1", "x", HUMAN,
                anchor={"start": 0, "end": 999})

    def test_freshness_follows_later_edits(self) -> None:
        self.case.thread_open(
            "draft", "DR1", "about v1", HUMAN, version_id="v1",
            anchor={"start": 0, "end": 5, "quoted": "argue"})
        first = self.case.collab_summary()["threads"][0]
        self.assertTrue(first["version_current"])
        self.assertTrue(first["anchor_match"])
        self.case.composer_save(
            "T", "totally rewritten body", HUMAN,
            draft_id="DR1", base_version=1)
        stale = self.case.collab_summary()["threads"][0]
        self.assertFalse(stale["version_current"])
        self.assertFalse(stale["anchor_match"])
        # Unbound, unquoted threads report unknown, never stale.
        self.case.thread_open("draft", "DR1", "general", HUMAN)
        plain = self.case.collab_summary()["threads"][1]
        self.assertIsNone(plain["version_current"])
        self.assertIsNone(plain["anchor_match"])

    def test_challenge_records_an_edge(self) -> None:
        self.case.thread_open("draft", "DR1", "wrong", HUMAN, challenge=True)
        edges = self.case.activity_edges()
        self.assertEqual(1, len(edges))
        self.assertEqual("challenged", edges[0]["relation"])
        self.assertEqual({"type": "thread", "id": "TH1"}, edges[0]["src"])
        self.assertEqual({"type": "draft", "id": "DR1"}, edges[0]["dst"])


class DirectiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))
        self.case.composer_save("T", "argue this", HUMAN)
        self.case.thread_open("draft", "DR1", "check this", HUMAN)

    def test_submit_ack_apply(self) -> None:
        submitted = self.case.directive_submit(
            "redirect", "reassess clause 8", HUMAN,
            base_refs=[{"type": "draft", "id": "DR1"}],
            from_thread="TH1")
        self.assertEqual("DIR1", submitted["id"])
        self.assertEqual("proposed", submitted["state"])
        self.assertEqual("TH1", submitted["from_thread"])
        edges = self.case.activity_edges()
        self.assertEqual(1, len(edges))
        self.assertEqual("from", edges[0]["relation"])
        self.case.directive_ack("DIR1", AGENT)
        applied = self.case.directive_apply(
            "DIR1", AGENT,
            invalidation=[{"type": "draft", "id": "DR1"}])
        self.assertEqual("applied", applied["state"])
        self.assertEqual(
            [{"type": "draft", "id": "DR1"}], applied["invalidation"])
        relations = [e["relation"] for e in self.case.activity_edges()]
        self.assertIn("invalidated", relations)

    def test_bad_submits_rejected(self) -> None:
        with self.assertRaises(CaseError):
            self.case.directive_submit("nudge", "x", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_submit("redirect", "  ", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_submit(
                "redirect", "x", HUMAN,
                base_refs=[{"type": "draft", "id": "DR9"}])
        with self.assertRaises(CaseError):
            self.case.directive_submit(
                "redirect", "x", HUMAN, from_thread="TH9")

    def test_illegal_transitions_rejected(self) -> None:
        self.case.directive_submit("question", "what?", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_apply("DIR1", AGENT)
        self.case.directive_ack("DIR1", AGENT)
        with self.assertRaises(CaseError):
            self.case.directive_ack("DIR1", AGENT)
        with self.assertRaises(CaseError):
            self.case.directive_refuse("DIR1", AGENT, "")
        self.case.directive_apply("DIR1", AGENT)
        with self.assertRaises(CaseError):
            self.case.directive_refuse("DIR1", AGENT, "too late")

    def test_refuse_and_supersede(self) -> None:
        self.case.directive_submit("constrain", "stay narrow", HUMAN)
        refused = self.case.directive_refuse("DIR1", AGENT, "needs scope")
        self.assertEqual("refused", refused["state"])
        self.case.directive_submit("constrain", "narrower", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_supersede("DIR1", HUMAN, "DIR2")
        self.case.directive_submit("redirect", "revised", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_supersede("DIR3", HUMAN, "DIR3")
        done = self.case.directive_supersede("DIR2", HUMAN, "DIR3")
        self.assertEqual("superseded", done["state"])
        self.assertEqual("DIR3", done["superseded_by"])
        relations = [e["relation"] for e in self.case.activity_edges()]
        self.assertIn("superseded", relations)


class SteeringInjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_pending_skips_terminal_and_delivered(self) -> None:
        self.case.directive_submit("redirect", "steer me", HUMAN)
        self.case.directive_submit("question", "settled", HUMAN)
        self.case.directive_refuse("DIR2", AGENT, "moot")
        pending = self.case.pending_directives(self.run_id)
        self.assertEqual(["DIR1"], [d["id"] for d in pending])
        self.assertEqual([], self.case.mark_directives_injected([], self.run_id))
        self.case.mark_directives_injected(["DIR1"], self.run_id)
        self.assertEqual([], self.case.pending_directives(self.run_id))
        # Another run still needs it.
        self.assertEqual(["DIR1"], [
            d["id"] for d in self.case.pending_directives("run-other")])
        with self.assertRaises(CaseError):
            self.case.mark_directives_injected(["DIR9"], self.run_id)

    def test_steering_messages_are_attributed_and_once_only(self) -> None:
        self.case.directive_submit("constrain", "stay narrow", HUMAN)
        first = steering_messages(root=self.root, run_id=self.run_id)
        self.assertEqual(1, len(first))
        self.assertEqual("user", first[0]["role"])
        self.assertIn("Steering directive DIR1 (constrain)", first[0]["content"])
        self.assertIn("stay narrow", first[0]["content"])
        self.assertEqual([], steering_messages(root=self.root, run_id=self.run_id))
        # Delivery never advances the lifecycle.
        self.assertEqual("proposed", self.case.directives()[0]["state"])

    def test_failed_checkpoint_leaves_direction_pending(self) -> None:
        self.case.directive_submit("redirect", "inspect the dates", HUMAN)
        with patch("legal_agent.loop.checkpoint_steering", side_effect=OSError("disk fault")):
            with self.assertRaises(OSError):
                steering_messages(root=self.root, run_id=self.run_id)
        self.assertEqual(["DIR1"], [d["id"] for d in self.case.pending_directives(self.run_id)])
        self.assertEqual([], load_progress(self.root, self.run_id)["messages"])
        self.assertEqual(1, len(steering_messages(root=self.root, run_id=self.run_id)))

    def test_crash_after_checkpoint_preserves_payload_without_reinsertion(self) -> None:
        self.case.directive_submit("redirect", "inspect the dates", HUMAN)
        with patch.object(CaseRecord, "mark_directives_injected", side_effect=OSError("crash before receipt")):
            with self.assertRaises(OSError):
                steering_messages(root=self.root, run_id=self.run_id)
        saved = load_progress(self.root, self.run_id)
        self.assertIn("inspect the dates", saved["messages"][0]["content"])
        self.assertEqual(["DIR1"], saved["steering_directive_ids"])
        self.assertEqual(0, saved["turn"])
        self.assertEqual([], saved["turns"])
        self.assertEqual([], steering_messages(root=self.root, run_id=self.run_id))
        self.assertEqual(1, len(load_progress(self.root, self.run_id)["messages"]))
        self.assertEqual([], CaseRecord(self.root, self.run_id).pending_directives(self.run_id))


@unittest.skipIf(
    verification_enabled(), "loop slices covered by tests/test_verification.py when VERIFICATION=on"
)
class SteeringResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def _steering_texts(self, run_id: str) -> list:
        return [
            m.get("content", "")
            for m in support.progress(self.root, run_id)["messages"]
            if isinstance(m, dict)
            and str(m.get("content", "")).startswith("Steering directive")
        ]

    def test_resume_delivers_a_mid_run_directive_once(self) -> None:
        fake = support.scripted(self.root, [support.CHECKLIST])
        first = run_agent(root=self.root, client=fake.client, turns=1)
        self.assertEqual("paused", first["status"])
        run_id = first["run_id"]
        CaseRecord(self.root, run_id).directive_submit(
            "redirect", "recheck the dates", HUMAN)
        fake2 = support.scripted(self.root, [support.PLAN])
        resume_run(root=self.root, run_id=run_id, client=fake2.client, turns=2)
        texts = self._steering_texts(run_id)
        self.assertEqual(1, len(texts))
        self.assertIn("DIR1", texts[0])
        self.assertIn("recheck the dates", texts[0])
        # A further resume repeats nothing.
        fake3 = support.scripted(self.root, [support.LIST])
        resume_run(root=self.root, run_id=run_id, client=fake3.client, turns=2)
        self.assertEqual(1, len(self._steering_texts(run_id)))


class DraftVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))
        self.case.composer_save("T", "argue this", HUMAN)

    def _status(self) -> str:
        return self.case.collab_summary()["drafts"][0]["verification_status"]

    def test_verify_passed_and_challenged(self) -> None:
        self.assertEqual("pending", self._status())
        done = self.case.draft_verify("DR1", "passed", HUMAN)
        self.assertEqual("passed", done["verification_status"])
        with self.assertRaises(CaseError):
            self.case.draft_verify("DR1", "challenged", HUMAN, "")
        with self.assertRaises(CaseError):
            self.case.draft_verify("DR1", "maybe", HUMAN)
        with self.assertRaises(CaseError):
            self.case.draft_verify("DR9", "passed", HUMAN)
        flagged = self.case.draft_verify("DR1", "challenged", HUMAN, "quote is off")
        self.assertEqual("challenged", flagged["verification_status"])

    def test_new_version_resets_to_pending(self) -> None:
        self.case.draft_verify("DR1", "passed", HUMAN)
        self.case.composer_save("T", "revised", HUMAN, draft_id="DR1", base_version=1)
        self.assertEqual("pending", self._status())

    def test_challenge_marks_challenged_but_stale_wins(self) -> None:
        self.case.thread_open("draft", "DR1", "wrong", HUMAN, challenge=True)
        self.assertEqual("challenged", self._status())
        self.case.directive_submit("redirect", "redo", HUMAN)
        self.case.directive_ack("DIR1", AGENT)
        self.case.directive_apply(
            "DIR1", AGENT, invalidation=[{"type": "draft", "id": "DR1"}])
        self.assertEqual("stale", self._status())
        self.case.thread_open("draft", "DR1", "also wrong", HUMAN, challenge=True)
        self.assertEqual("stale", self._status())

    def test_reverify_clears_stale(self) -> None:
        self.case.directive_submit("redirect", "redo", HUMAN)
        self.case.directive_ack("DIR1", AGENT)
        self.case.directive_apply(
            "DIR1", AGENT, invalidation=[{"type": "draft", "id": "DR1"}])
        self.assertEqual("stale", self._status())
        done = self.case.draft_verify("DR1", "passed", HUMAN)
        self.assertEqual("passed", done["verification_status"])


class ContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))

    def test_quarantined_until_counsel_admits(self) -> None:
        staged = self.case.context_add("composer", HUMAN, body_md="new letter")
        self.assertEqual("CI1", staged["id"])
        self.assertEqual("quarantined", staged["state"])
        with self.assertRaises(CaseError):
            self.case.context_admit("CI1", AGENT)
        with self.assertRaises(CaseError):
            self.case.context_admit("CI1", HUMAN)
        with self.assertRaises(CaseError):
            self.case.context_admit("CI1", CLIENT)
        admitted = self.case.context_admit("CI1", ATTORNEY)
        self.assertEqual("admitted", admitted["state"])
        self.assertEqual("attorney", admitted["admitted_by"]["role"])
        with self.assertRaises(CaseError):
            self.case.context_admit("CI1", ATTORNEY)
        self.case.context_add("composer", HUMAN, body_md="second")
        admin_ok = self.case.context_admit("CI2", ADMIN)
        self.assertEqual("admitted", admin_ok["state"])

    def test_bad_staging_and_reject(self) -> None:
        with self.assertRaises(CaseError):
            self.case.context_add("rumor", HUMAN, body_md="x")
        with self.assertRaises(CaseError):
            self.case.context_add("composer", HUMAN)
        with self.assertRaises(CaseError):
            self.case.context_add(
                "composer", HUMAN, body_md="x", file_ref="f.pdf")
        self.case.context_add("upload", HUMAN, file_ref="letter.pdf")
        with self.assertRaises(CaseError):
            self.case.context_reject("CI1", HUMAN, "")
        rejected = self.case.context_reject("CI1", HUMAN, "duplicate")
        self.assertEqual("rejected", rejected["state"])


class EdgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))
        self.case.composer_save("T", "argue this", HUMAN)

    def test_explicit_edge_and_endpoint_validation(self) -> None:
        self.case.thread_open("draft", "DR1", "note", HUMAN)
        edge = self.case.edge_add(
            "thread", "TH1", "draft", "DR1", "supports", HUMAN)
        self.assertEqual("E1", edge["id"])
        with self.assertRaises(CaseError):
            self.case.edge_add("thread", "TH1", "draft", "DR1", "likes", HUMAN)
        with self.assertRaises(CaseError):
            self.case.edge_add("thread", "TH9", "draft", "DR1", "supports", HUMAN)
        with self.assertRaises(CaseError):
            self.case.edge_add("dream", "D1", "draft", "DR1", "supports", HUMAN)

    def test_participants_are_validated(self) -> None:
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "x", {"type": "human", "id": "  "})
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "x", {"type": "ghost", "id": "g"})
        with self.assertRaises(CaseError):
            self.case.composer_save(
                "T", "x", {"type": "agent", "id": "a", "agent_role": "oracle"})


class CollabSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_summary_counts_and_survives_reload(self) -> None:
        self.case.composer_save("T", "argue this", HUMAN)
        self.case.thread_open("draft", "DR1", "check", HUMAN, challenge=True)
        self.case.directive_submit("redirect", "redo", HUMAN, from_thread="TH1")
        self.case.context_add("composer", HUMAN, body_md="letter")
        summary = self.case.collab_summary()
        self.assertEqual(1, len(summary["drafts"]))
        self.assertEqual(1, len(summary["threads"]))
        self.assertEqual(1, len(summary["directives"]))
        self.assertEqual(1, len(summary["context"]))
        self.assertEqual(2, summary["edges"])
        self.assertEqual("DR1", summary["drafts"][0]["id"])
        self.assertEqual("argue this", summary["drafts"][0]["body_md"])
        self.assertEqual(1, len(summary["threads"][0]["messages"]))
        self.assertEqual("check", summary["threads"][0]["messages"][0]["body_md"])
        reloaded = CaseRecord(self.root, self.run_id)
        self.assertEqual(summary, reloaded.collab_summary())

    def test_cli_show_reports_the_same_summary(self) -> None:
        self.case.composer_save("T", "argue this", HUMAN)
        old = os.environ.get("LEGAL_AGENT_ROOT")
        os.environ["LEGAL_AGENT_ROOT"] = str(self.root)
        self.addCleanup(
            lambda: os.environ.__setitem__("LEGAL_AGENT_ROOT", old)
            if old is not None else os.environ.pop("LEGAL_AGENT_ROOT", None))
        out = StringIO()
        with redirect_stdout(out):
            code = collab.main([self.run_id, "show"])
        self.assertEqual(0, code)
        shown = json.loads(out.getvalue())
        self.assertEqual(1, len(shown["drafts"]))
        self.assertEqual("DR1", shown["drafts"][0]["id"])


class GovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.case = CaseRecord(self.root, new_run(self.root, instructions="i", objective="o"))
        self.case.directive_submit("redirect", "redo", HUMAN)

    def test_ack_and_apply_are_agent_only(self) -> None:
        with self.assertRaises(CaseError):
            self.case.directive_ack("DIR1", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_ack("DIR1", ATTORNEY)
        self.case.directive_ack("DIR1", AGENT)
        with self.assertRaises(CaseError):
            self.case.directive_apply("DIR1", ATTORNEY)
        done = self.case.directive_apply("DIR1", AGENT)
        self.assertEqual("applied", done["state"])

    def test_roles_default_and_validate(self) -> None:
        draft = self.case.composer_save("T", "x", {"type": "human", "id": "n"})
        self.assertEqual("associate", draft["author"]["role"])
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "x", {"type": "human", "id": "n", "role": "judge"})
        with self.assertRaises(CaseError):
            self.case.composer_save(
                "T", "x", {"type": "agent", "id": "a", "role": "attorney"})


class HardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_corrupt_version_fails_as_case_error(self) -> None:
        self.case.composer_save("T", "x", HUMAN)
        self.case.thread_open("draft", "DR1", "note", HUMAN)
        self.case.data["composer_drafts"][0]["version"] = "x"
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "y", HUMAN, draft_id="DR1")
        with self.assertRaises(CaseError):
            self.case.collab_summary()

    def test_oversize_text_fails_closed(self) -> None:
        with self.assertRaises(CaseError):
            self.case.composer_save("T", "x" * 200_001, HUMAN)
        with self.assertRaises(CaseError):
            self.case.composer_save("T" * 501, "x", HUMAN)
        with self.assertRaises(CaseError):
            self.case.directive_submit("question", "x" * 200_001, HUMAN)
        self.assertEqual([], self.case.composer_drafts())
        self.assertEqual([], self.case.directives())

    def test_runs_cannot_see_each_others_collab(self) -> None:
        other_id = new_run(self.root, instructions="i", objective="o")
        other = CaseRecord(self.root, other_id)
        self.case.composer_save("T", "private", HUMAN)
        self.case.thread_open("draft", "DR1", "secret", HUMAN)
        self.assertEqual([], other.composer_drafts())
        self.assertEqual([], other.threads())
        self.assertEqual([], other.collab_summary()["drafts"])
        with self.assertRaises(CaseError):
            other.thread_open("draft", "DR1", "x", HUMAN)
        with self.assertRaises(CaseError):
            other.edge_add("draft", "DR1", "draft", "DR1", "supports", HUMAN)

    def test_every_mutation_audits_actor_and_action(self) -> None:
        self.case.composer_save("T", "x", HUMAN)
        self.case.thread_open("draft", "DR1", "y", HUMAN)
        self.case.directive_submit("question", "z", HUMAN)
        self.case.context_add("composer", HUMAN, body_md="w")
        actions = [
            (e["action"], e["actor"]) for e in self.case.data["audit"]
        ]
        self.assertIn(("draft_created", "associate"), actions)
        self.assertIn(("thread_opened", "associate"), actions)
        self.assertIn(("directive_submitted", "associate"), actions)
        self.assertIn(("context_staged", "associate"), actions)


class CollaborationExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_empty_record_exports_none(self) -> None:
        self.assertIsNone(collaboration_export(self.case.data))
        md = export_markdown({"template": TEMPLATE_AUDIT})
        self.assertIn("## Collaboration", md)
        self.assertIn("none recorded", md)

    def test_full_record_exports_deliberation(self) -> None:
        self.case.composer_save("T", "argue this", HUMAN)
        self.case.thread_open(
            "draft", "DR1", "about v1", HUMAN, version_id="v1",
            anchor={"start": 0, "end": 5, "quoted": "argue"})
        self.case.directive_submit("question", "why?", HUMAN)
        self.case.context_add("composer", HUMAN, body_md="letter")
        self.case.composer_save(
            "T", "rewritten", HUMAN, draft_id="DR1", base_version=1)
        record = collaboration_export(self.case.data)
        self.assertEqual("rewritten", record["drafts"][0]["body_md"])
        self.assertEqual(2, len(record["drafts"][0]["history"]))
        self.assertNotIn("body_md", record["drafts"][0]["history"][0])
        self.assertFalse(record["threads"][0]["version_current"])
        self.assertFalse(record["threads"][0]["anchor_match"])
        self.assertEqual(1, len(record["threads"][0]["messages"]))
        self.assertEqual("DIR1", record["directives"][0]["id"])
        self.assertEqual("quarantined", record["context"][0]["state"])
        # Deep copies: mutating the export never touches the record.
        record["drafts"][0]["body_md"] = "tampered"
        self.assertEqual("rewritten", self.case.composer_get("DR1")["body_md"])
        md = export_markdown(
            {"template": TEMPLATE_AUDIT, "collaboration": record})
        self.assertIn("## Collaboration", md)
        self.assertIn("DR1", md)
        self.assertIn("TH1", md)
        self.assertIn("old version", md)
        self.assertIn("anchor drifted", md)
        self.assertIn("DIR1", md)


class CollabInvariantFuzzTest(unittest.TestCase):
    """Seeded random-op walk: only CaseError escapes, graph stays sound.

    Every op runs against tracked ids (sometimes deliberately
    wrong); after each op the record must satisfy the structural
    invariants, and the same seed must replay byte-identically
    modulo timestamps.
    """

    SEEDS = (11, 22, 33)
    OPS = 250
    ACTORS = (
        HUMAN, ATTORNEY, ADMIN, CLIENT, AGENT,
        {"type": "agent", "id": "nameless"},
        {"type": "human", "id": "judge judy", "role": "judge"},
        {"type": "ghost", "id": "spooky"},
        {"type": "human", "id": "  "},
    )

    def _op(self, rng, case, ids):
        which = rng.randrange(18)
        actor = rng.choice(self.ACTORS)
        known = lambda kind: rng.choice(ids[kind]) if ids[kind] and rng.random() < 0.8 else None
        maybe = lambda kind, prefix: known(kind) or f"{prefix}9"
        if which == 0:
            case.composer_save(f"T{rng.randrange(4)}", f"body {rng.randrange(9)}", actor)
            ids["draft"].append(f"DR{len(ids['draft']) + 1}")
        elif which == 1:
            case.composer_save("T", "edit", actor,
                               draft_id=maybe("draft", "DR"),
                               base_version=rng.choice([None, 1, 2, 3, 99]))
        elif which == 2:
            case.composer_send(maybe("draft", "DR"), actor)
        elif which == 3:
            case.draft_verify(maybe("draft", "DR"),
                              rng.choice(["passed", "challenged", "maybe"]),
                              actor, rng.choice(["", "because"]))
        elif which == 4:
            anchor = None
            roll = rng.random()
            if roll >= 0.7:
                anchor = {"start": 0, "end": 0, "quoted": ""}
            elif roll >= 0.5:
                anchor = {"start": rng.randrange(6), "end": rng.randrange(6),
                          "quoted": rng.choice(["", "body", "WRONG"])}
            case.thread_open("draft", maybe("draft", "DR"), f"note {rng.randrange(9)}",
                             actor, version_id=rng.choice(
                                 ["", "v1", "v1", "v2", "v9", "x"]),
                             anchor=anchor, challenge=rng.random() < 0.3)
            ids["thread"].append(f"TH{len(ids['thread']) + 1}")
        elif which == 5:
            case.thread_reply(maybe("thread", "TH"), f"reply {rng.randrange(9)}",
                              actor, challenge=rng.random() < 0.2)
        elif which == 6:
            case.thread_edit_message(maybe("thread", "TH"), f"M{rng.randrange(1, 5)}",
                                     f"fixed {rng.randrange(9)}", actor)
        elif which == 7:
            case.thread_resolve(maybe("thread", "TH"), actor, "done")
        elif which == 8:
            case.thread_reopen(maybe("thread", "TH"), actor, rng.choice(["", "oops"]))
        elif which == 9:
            refs = []
            if rng.random() < 0.6 and ids["draft"]:
                refs.append({"type": "draft", "id": rng.choice(ids["draft"])})
            if rng.random() < 0.2:
                refs.append({"type": "draft", "id": "DR9"})
            case.directive_submit(
                rng.choice(["redirect", "add-context", "constrain", "question", "nudge"]),
                f"do {rng.randrange(9)}", actor, base_refs=refs,
                from_thread=known("thread") or "")
            ids["directive"].append(f"DIR{len(ids['directive']) + 1}")
        elif which == 10:
            case.directive_ack(maybe("directive", "DIR"), actor)
        elif which == 11:
            inv = []
            if rng.random() < 0.5 and ids["draft"]:
                inv.append({"type": "draft", "id": rng.choice(ids["draft"])})
            case.directive_apply(maybe("directive", "DIR"), actor, invalidation=inv)
        elif which == 12:
            case.directive_refuse(maybe("directive", "DIR"), actor,
                                  rng.choice(["", "cannot"]))
        elif which == 13:
            case.directive_supersede(maybe("directive", "DIR"), actor,
                                     maybe("directive", "DIR"))
        elif which == 14:
            if rng.random() < 0.5:
                case.context_add("composer", actor, body_md=f"ctx {rng.randrange(9)}")
            else:
                case.context_add("upload", actor, file_ref="f.pdf")
            ids["context"].append(f"CI{len(ids['context']) + 1}")
        elif which == 15:
            case.context_admit(maybe("context", "CI"), actor)
        elif which == 16:
            case.context_reject(maybe("context", "CI"), actor, rng.choice(["", "dup"]))
        else:
            kind = rng.choice(["draft", "thread", "directive", "context", "dream"])
            pre = {"draft": "DR", "thread": "TH", "directive": "DIR",
                   "context": "CI"}.get(kind, "D")
            case.edge_add(kind, maybe(kind if kind in ids else "draft", pre),
                          "draft", maybe("draft", "DR"),
                          rng.choice(["supports", "challenged", "from", "likes"]), actor)

    def _assert_invariants(self, case, prior_states):
        from agent_kit.case_record import (
            CONTEXT_STATES,
            DIRECTIVE_STATES,
            DRAFT_STATES,
            THREAD_STATES,
            VERIFICATION_STATUSES,
        )
        data = case.data
        stores = {
            "draft": {d.get("id") for d in data.get("composer_drafts") or []},
            "thread": {t.get("id") for t in data.get("threads") or []},
            "directive": {d.get("id") for d in data.get("directives") or []},
            "context": {c.get("id") for c in data.get("context_items") or []},
            "objection": {o.get("id") for o in data.get("objections") or []},
            "artifact": set((data.get("artifacts") or {})),
        }
        for draft in data.get("composer_drafts") or []:
            self.assertIn(draft.get("state"), DRAFT_STATES)
            self.assertIn(draft.get("verification_status", "pending"),
                          VERIFICATION_STATUSES)
            hist = draft.get("history") or []
            self.assertEqual(draft.get("version"), len(hist))
            self.assertEqual(list(range(1, len(hist) + 1)),
                             [h.get("version") for h in hist])
        for thread in data.get("threads") or []:
            self.assertIn(thread.get("state"), THREAD_STATES)
            msgs = thread.get("messages") or []
            self.assertGreaterEqual(len(msgs), 1)
            self.assertEqual([f"M{i}" for i in range(1, len(msgs) + 1)],
                             [m.get("id") for m in msgs])
            self.assertIn(thread.get("object_id"),
                          stores.get(thread.get("object_type"), set()))
        for directive in data.get("directives") or []:
            self.assertIn(directive.get("state"), DIRECTIVE_STATES)
            old = prior_states.get(directive["id"])
            if old in ("refused", "superseded"):
                self.assertEqual(old, directive.get("state"))
            prior_states[directive["id"]] = directive.get("state")
        for item in data.get("context_items") or []:
            self.assertIn(item.get("state"), CONTEXT_STATES)
        for edge in data.get("activity_edges") or []:
            for end in ("src", "dst"):
                ref = edge.get(end) or {}
                self.assertIn(ref.get("id"), stores.get(ref.get("type"), set()))
        for entry in data.get("audit") or []:
            self.assertTrue(entry.get("action"))
            self.assertTrue(entry.get("actor"))
            self.assertTrue(entry.get("at"))

    def _replay(self, seed):
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o")
        case = CaseRecord(root, run_id)
        ids = {"draft": [], "thread": [], "directive": [], "context": []}
        rng = random.Random(seed)
        prior_states: dict = {}
        outcomes = []
        for _ in range(self.OPS):
            try:
                before = {k: len(v) for k, v in ids.items()}
                self._op(rng, case, ids)
                # Roll back id tracking when the op failed: only ids the
                # record actually holds may be reused as known refs.
                for kind, seq, pre in (("draft", "composer_drafts", "DR"),
                                       ("thread", "threads", "TH"),
                                       ("directive", "directives", "DIR"),
                                       ("context", "context_items", "CI")):
                    have = len(case.data.get(seq) or [])
                    ids[kind] = [f"{pre}{i}" for i in range(1, have + 1)]
                    void = len(ids[kind]) < before[kind]
                    self.assertFalse(void, f"{kind} ids shrank")
                outcomes.append("ok")
            except CaseError as error:
                outcomes.append(f"no:{error}")
            except Exception as error:  # noqa: BLE001 - the fuzzer names leaks
                self.fail(f"non-CaseError escaped: {type(error).__name__}: {error}")
            self._assert_invariants(case, prior_states)
        blob = json.dumps(case.data, sort_keys=True, default=str)
        blob = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\"']*",
                      "T", blob)
        blob = blob.replace(run_id, "RUN")
        counts = {
            key: len(case.data.get(key) or [])
            for key in ("composer_drafts", "threads", "directives",
                        "context_items", "activity_edges")
        }
        return outcomes, blob, counts

    def test_seeded_walks_hold_invariants_and_replay(self) -> None:
        for seed in self.SEEDS:
            first, blob_a, counts_a = self._replay(seed)
            second, blob_b, counts_b = self._replay(seed)
            self.assertEqual(first, second)
            self.assertEqual(blob_a, blob_b)
            self.assertEqual(counts_a, counts_b)
            # Non-vacuous: real objects were built, real rejections fired.
            self.assertGreater(counts_a["composer_drafts"], 0)
            self.assertGreater(counts_a["threads"], 0)
            self.assertGreater(counts_a["directives"], 0)
            self.assertGreater(
                sum(1 for o in first if o.startswith("no:")), 50)
        self.assertGreater(len(first), 0)


if __name__ == "__main__":
    unittest.main()
