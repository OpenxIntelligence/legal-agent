"""Fail-closed quarantine for confirmed bad redactions.

A rectangle over live text that renders uniform hides text
from the eye but not from transcription: the model reads the
content layer. Advisory flags are insufficient — a confirmed
finding must quarantine the document before any transcript
exists, and the quarantine must hold across extraction,
search, page reads, and analysis until a human releases it.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agent_kit import document_text, redactions, tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from tests.test_redactions import SECRET, _bad_page, _clean_page, _pdf


class QuarantineStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def test_round_trip_with_audit_trail(self) -> None:
        self.assertFalse(redactions.is_quarantined(self.root, "D1"))
        entry = redactions.quarantine(
            self.root, "D1", reason="render-confirmed", findings=1
        )
        self.assertTrue(redactions.is_quarantined(self.root, "D1"))
        self.assertEqual("render-confirmed", entry["reason"])
        self.assertIn("at", entry)
        released = redactions.release(self.root, "D1", note="reviewed")
        self.assertFalse(redactions.is_quarantined(self.root, "D1"))
        self.assertEqual("reviewed", released["released"]["note"])
        # The release is recorded, not erased.
        store = redactions.quarantine_list(self.root)
        self.assertIn("released", store["D1"])

    def test_release_without_quarantine_refused(self) -> None:
        with self.assertRaises(ValueError):
            redactions.release(self.root, "D-NOPE", note="x")

    def test_confirmed_hidden_needs_render_basis(self) -> None:
        self.assertTrue(
            redactions.confirmed_hidden(
                {"findings": [{"basis": "render"}]}
            )
        )
        self.assertFalse(
            redactions.confirmed_hidden(
                {"findings": [{"basis": "dark-fill"}]}
            )
        )
        self.assertFalse(redactions.confirmed_hidden({"findings": []}))
        self.assertFalse(redactions.confirmed_hidden(None))
        self.assertFalse(
            redactions.confirmed_hidden({"error": "unreadable"})
        )


class ContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.fake = support.scripted(self.root, [])
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        (self.root / "workspace/inbox" / "DOC-BAD.pdf").write_bytes(
            _pdf([_bad_page()])
        )
        (self.root / "workspace/inbox" / "DOC-CLEAN.pdf").write_bytes(
            _pdf([_clean_page])
        )

    def _transcribe(self, doc_id: str, text: str) -> None:
        document = document_text.get_document(self.root, doc_id)
        document_text._save_text(
            self.root, document,
            [{"page": 1, "text": text}], "test",
        )

    def test_scan_marks_render_basis(self) -> None:
        data = (self.root / "workspace/inbox" / "DOC-BAD.pdf").read_bytes()
        (finding,) = redactions.scan_pdf_bytes(data)["findings"]
        self.assertEqual("render", finding["basis"])

    def test_confirmed_finding_quarantines_before_transcript(self) -> None:
        result = analyze(
            self.fake.client, self.root, ["DOC-BAD"], "x",
            run_id=self.run_id,
        )
        (entry,) = result["documents"]
        self.assertTrue(entry["note"].startswith("error"))
        self.assertIn("quarantine", entry["note"])
        self.assertEqual("quarantine", entry["error_kind"])
        self.assertTrue(redactions.is_quarantined(self.root, "DOC-BAD"))
        # No transcript was written: nothing for search to find.
        self.assertIsNone(
            document_text.cached_text(self.root, "DOC-BAD")
        )
        self.assertNotIn(SECRET, repr(result))
        # And nothing left the machine: no transcription call,
        # no file upload, no analysis prompt.
        self.assertEqual([], self.fake.calls)

    def test_quarantine_holds_across_search_and_reads(self) -> None:
        self._transcribe("DOC-BAD", f"Claimant {SECRET} filed first.")
        self._transcribe("DOC-CLEAN", "Nothing hidden on this page.")
        redactions.quarantine(self.root, "DOC-BAD", reason="test")
        found = document_text.search_corpus(self.root, "Claimant")
        self.assertEqual([], found["documents"])
        self.assertEqual(["DOC-BAD"], found["quarantined"])
        self.assertIsNone(document_text.page_text(self.root, "DOC-BAD", 1))
        self.assertIsNone(document_text.cached_text(self.root, "DOC-BAD"))
        with self.assertRaises(document_text.DocumentError):
            document_text.extract_text(
                self.fake.client, self.root, "DOC-BAD"
            )
        # The clean document is unaffected.
        found = document_text.search_corpus(self.root, "hidden")
        self.assertEqual(1, len(found["documents"]))

    def test_quarantined_quotes_refused_as_evidence(self) -> None:
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self._transcribe("DOC-BAD", f"Claimant {SECRET} filed first.")
        redactions.quarantine(self.root, "DOC-BAD", reason="test")
        item = support.CHECKLIST_ITEMS[0]["id"]
        with self.assertRaises(Exception) as raised:
            self.case.record_fact(
                item,
                "DOC-BAD",
                1,
                f"Claimant {SECRET} filed first.",
                "someone filed first",
            )
        self.assertIn("quarantined", str(raised.exception))

    def test_release_restores_reads(self) -> None:
        self._transcribe("DOC-BAD", "Claimant filed first.")
        redactions.quarantine(self.root, "DOC-BAD", reason="test")
        self.assertEqual([], document_text.search_corpus(
            self.root, "Claimant")["documents"])
        redactions.release(self.root, "DOC-BAD", note="reviewed")
        found = document_text.search_corpus(self.root, "Claimant")
        self.assertEqual(1, len(found["documents"]))

    def test_heuristic_only_stays_advisory(self) -> None:
        # Render unavailable: dark-fill findings advise but do
        # not quarantine, so an environment hiccup cannot seal
        # every scanned exhibit.
        with mock.patch.object(
            redactions, "_render_page", return_value=None
        ):
            data = (
                self.root / "workspace/inbox" / "DOC-BAD.pdf"
            ).read_bytes()
            (finding,) = redactions.scan_pdf_bytes(data)["findings"]
            self.assertEqual("dark-fill", finding["basis"])
            result = analyze(
                self.fake.client, self.root, ["DOC-BAD"], "x",
                run_id=self.run_id,
            )
        (entry,) = result["documents"]
        self.assertNotIn("quarantine", entry.get("note", ""))
        self.assertFalse(redactions.is_quarantined(self.root, "DOC-BAD"))
        # Advisory, not silent: the heuristic finding stays on the entry.
        self.assertEqual(1, len((entry.get("redactions") or {}).get("findings", [])))


if __name__ == "__main__":
    unittest.main()
