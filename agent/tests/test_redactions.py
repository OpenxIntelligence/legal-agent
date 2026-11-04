"""Bad-redaction scanning for inbox PDFs (X-Ray method, clean-room build).

Rectangles drawn over live text look redacted but copy out —
and transcription reads the content layer, so covered text can
leak into transcripts, facts, and memos. The scanner reports
page, box, and covered-character counts; the covered text
itself never leaves the scan. All fixtures synthetic PDFs
built with reportlab; judges scripted, never live.
"""

from __future__ import annotations

import io
import unittest

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from agent_kit import redactions, tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import scan_redactions
from agent_kit.tools.analyze_document import analyze
from tests import support

SECRET = "SSN 078-05-1120"
PAGE_W, PAGE_H = letter


def _pdf(pages: list) -> bytes:
    """Build a PDF: pages is a list of draw callables(canvas)."""
    buf = io.BytesIO()
    doc = canvas.Canvas(buf, pagesize=letter)
    for index, draw in enumerate(pages):
        if index:
            doc.showPage()
        draw(doc)
    doc.save()
    return buf.getvalue()


def _bad_page(secret: str = SECRET):
    def draw(doc) -> None:
        doc.drawString(100, 700, f"Claimant {secret} filed first.")
        doc.setFillColorRGB(0, 0, 0)
        doc.rect(100, 688, 220, 16, stroke=0, fill=1)

    return draw


def _clean_page(doc) -> None:
    doc.drawString(100, 700, "Nothing hidden on this page.")


def _proper_page(doc) -> None:
    # A proper redaction: the rectangle sits over blank paper,
    # the end state of text actually removed.
    doc.drawString(100, 700, "Claimant filed first.")
    doc.setFillColorRGB(0, 0, 0)
    doc.rect(100, 640, 220, 16, stroke=0, fill=1)


def _highlight_page(doc) -> None:
    doc.drawString(100, 700, "Highlighted but readable text.")
    doc.setFillColorRGB(1, 1, 0)
    doc.setFillAlpha(0.4)
    doc.rect(100, 688, 200, 16, stroke=0, fill=1)


def _whiteout_page(doc) -> None:
    doc.drawString(100, 700, f"Claimant {SECRET} filed first.")
    doc.setFillColorRGB(1, 1, 1)
    doc.rect(100, 688, 220, 16, stroke=0, fill=1)


def _table_cell_page(doc) -> None:
    # White cell fill with text drawn visibly on top: the
    # render shows strokes, so this is not a redaction.
    doc.setFillColorRGB(1, 1, 1)
    doc.rect(100, 688, 220, 16, stroke=0, fill=1)
    doc.setFillColorRGB(0, 0, 0)
    doc.drawString(100, 700, "Cell value in plain sight.")


def _tiny_page(doc) -> None:
    doc.drawString(100, 700, "Text under a speck.")
    doc.setFillColorRGB(0, 0, 0)
    doc.rect(100, 690, 3, 3, stroke=0, fill=1)


def _header_page(doc) -> None:
    doc.drawString(100, 760, "Header text in a box.")
    doc.setFillColorRGB(0, 0, 0)
    doc.rect(100, 750, 150, 14, stroke=0, fill=1)


class ScannerTest(unittest.TestCase):
    def test_bad_redaction_found_without_its_text(self) -> None:
        report = redactions.scan_pdf_bytes(_pdf([_bad_page()]))
        self.assertEqual(1, report["pages"])
        (finding,) = report["findings"]
        self.assertEqual(1, finding["page"])
        self.assertGreater(finding["chars"], 0)
        self.assertEqual(4, len(finding["bbox"]))
        self.assertNotIn("text", finding)
        self.assertNotIn(SECRET, repr(report))

    def test_clean_page_silent(self) -> None:
        report = redactions.scan_pdf_bytes(_pdf([_clean_page]))
        self.assertEqual([], report["findings"])
        self.assertFalse(report["truncated"])

    def test_proper_redaction_silent(self) -> None:
        report = redactions.scan_pdf_bytes(_pdf([_proper_page]))
        self.assertEqual([], report["findings"])

    def test_highlight_not_a_redaction(self) -> None:
        # Bright fills read through: the dark-fill gate keeps
        # ordinary highlighting out of the findings.
        report = redactions.scan_pdf_bytes(_pdf([_highlight_page]))
        self.assertEqual([], report["findings"])

    def test_whiteout_detected_by_render(self) -> None:
        # A white rectangle over text renders uniform: the
        # render check catches what a color rule must miss.
        report = redactions.scan_pdf_bytes(_pdf([_whiteout_page]))
        self.assertEqual(1, len(report["findings"]))
        self.assertNotIn(SECRET, repr(report))

    def test_white_table_cell_silent(self) -> None:
        # Same white fill, but the text is drawn visibly on
        # top: strokes in the render, no finding.
        report = redactions.scan_pdf_bytes(_pdf([_table_cell_page]))
        self.assertEqual([], report["findings"])

    def test_specks_and_headers_skipped(self) -> None:
        report = redactions.scan_pdf_bytes(_pdf([_tiny_page]))
        self.assertEqual([], report["findings"])
        report = redactions.scan_pdf_bytes(_pdf([_header_page]))
        self.assertEqual([], report["findings"])

    def test_truncation_caps_findings(self) -> None:
        rects = []
        for n in range(redactions.MAX_FINDINGS + 1):
            def draw(doc, slot=n) -> None:
                y = 700 - (slot % 30) * 22
                doc.drawString(100, y, f"line {slot} {SECRET}")
                doc.setFillColorRGB(0, 0, 0)
                doc.rect(100, y - 12, 200, 16, stroke=0, fill=1)

            rects.append(draw)
        pages = [
            (lambda group: lambda doc: [fn(doc) for fn in group])(rects[i:i + 30])
            for i in range(0, len(rects), 30)
        ]
        report = redactions.scan_pdf_bytes(_pdf(pages))
        self.assertTrue(report["truncated"])
        self.assertEqual(redactions.MAX_FINDINGS, len(report["findings"]))
        self.assertNotIn(SECRET, repr(report))

    def test_garbage_rejected(self) -> None:
        with self.assertRaises(redactions.RedactionError):
            redactions.scan_pdf_bytes(b"not a pdf at all")
        with self.assertRaises(redactions.RedactionError):
            redactions.scan_pdf_bytes(b"")


class ScanToolTest(unittest.TestCase):
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

    def _inbox_pdf(self, doc_id: str, data: bytes) -> None:
        (self.root / "workspace/inbox" / f"{doc_id}.pdf").write_bytes(data)

    def test_scan_flags_without_leaking_text(self) -> None:
        self._inbox_pdf("DOC-BAD", _pdf([_bad_page()]))
        result = scan_redactions.run(self.ctx, {"document_id": "DOC-BAD"})
        self.assertEqual("DOC-BAD", result["scanned"])
        self.assertEqual(1, len(result["findings"]))
        self.assertNotIn(SECRET, repr(result))
        flags = [
            f for f in self.case.data["flags"]
            if f.get("kind") == "bad_redaction"
        ]
        self.assertEqual(1, len(flags))
        self.assertEqual("pending", flags[0]["review"])
        self.assertNotIn(SECRET, repr(flags))
        # Flagged, not excluded: the visible content stays usable.
        self.assertFalse(self.case.flagged("DOC-BAD"))

    def test_rescan_refreshes_one_flag(self) -> None:
        self._inbox_pdf("DOC-BAD", _pdf([_bad_page()]))
        scan_redactions.run(self.ctx, {"document_id": "DOC-BAD"})
        scan_redactions.run(self.ctx, {"document_id": "DOC-BAD"})
        flags = [
            f for f in self.case.data["flags"]
            if f.get("kind") == "bad_redaction"
        ]
        self.assertEqual(1, len(flags))
        self.assertEqual(1, flags[0]["findings"])

    def test_clean_scan_records_nothing(self) -> None:
        self._inbox_pdf("DOC-CLEAN", _pdf([_clean_page]))
        result = scan_redactions.run(self.ctx, {"document_id": "DOC-CLEAN"})
        self.assertEqual([], result["findings"])
        self.assertEqual([], self.case.data["flags"])

    def test_non_pdf_and_unknown_refused(self) -> None:
        with self.assertRaises(Exception) as raised:
            scan_redactions.run(self.ctx, {"document_id": "DOC-REPORT"})
        self.assertIn("not a PDF", str(raised.exception))
        with self.assertRaises(Exception):
            scan_redactions.run(self.ctx, {"document_id": "DOC-NOPE"})

    def test_analyze_quarantines_confirmed_bad_redaction(self) -> None:
        # Mission-ordered behavior change: a render-confirmed
        # finding fails the document closed (see
        # tests/test_quarantine.py) instead of analyzing with
        # an advisory. Heuristic-only findings stay advisory.
        self._inbox_pdf("DOC-BAD", _pdf([_bad_page()]))
        result = analyze(
            self.fake.client, self.root, ["DOC-BAD"], "x",
            run_id=self.run_id,
        )
        (entry,) = result["documents"]
        self.assertIn("quarantin", entry["note"])
        self.assertEqual("quarantine", entry["error_kind"])
        self.assertEqual([], entry["evidence"])
        self.assertNotIn(SECRET, repr(entry))
        self.assertTrue(redactions.is_quarantined(self.root, "DOC-BAD"))

    def test_analyze_ignores_text_named_pdf(self) -> None:
        # The text fixtures wear .pdf names; they are not PDFs,
        # so no redaction key is attached and nothing changes.
        result = analyze(
            self.fake.client, self.root, ["DOC-REPORT"], "x",
            run_id=self.run_id,
        )
        (entry,) = result["documents"]
        self.assertNotIn("redactions", entry)


if __name__ == "__main__":
    unittest.main()
