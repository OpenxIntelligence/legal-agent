"""Evidence assembly: coverage completion + per-issue obligations.

The retrieval baseline returns a single top passage for
multi-obligation questions (multi 2/8, sufficiency 0/8): the
BM25 ratio cutoff keeps only near-top pages. Coverage
completion adds the best page for each otherwise-uncovered
content term (bounded); the substring path is untouched.
Obligations let an issue freeze what it needs, and the gate
holds asserted findings to those needs unless gaps disclose
the shortfall — the conclusion layer where abstention lives.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from agent_kit import document_text, reasoning, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from tests.test_kit import call


def _write(root, doc_id: str, pages: list[str]) -> None:
    inbox = root / "workspace/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    blob = "\n".join(pages).encode()
    (inbox / f"{doc_id}.pdf").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    cache = root / "workspace/cache/text"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sha}.json").write_text(
        json.dumps(
            {"pages": [{"page": n + 1, "text": text}
                       for n, text in enumerate(pages)]}
        )
    )


class CompletionTest(unittest.TestCase):
    def _root(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_uncovered_term_pulls_its_page(self) -> None:
        root = self._root()
        _write(root, "D1", ["alpha alpha alpha beta beta"])
        _write(root, "D2", ["zebra qux qux qux qux qux"])
        result = document_text.search_corpus(
            root, "alpha beta zebra summary request")
        got = {
            (d["document_id"], p["page"])
            for d in result["documents"]
            for p in d["passages"]
        }
        # D2 scores below the 0.7 ratio cutoff on its lone
        # diluted term, but "zebra" is otherwise uncovered.
        self.assertEqual({("D1", 1), ("D2", 1)}, got)

    def test_silence_preserved(self) -> None:
        root = self._root()
        _write(root, "D1", ["alpha alpha alpha beta beta"])
        result = document_text.search_corpus(root, "xylophone quasar")
        self.assertEqual([], result["documents"])

    def test_completion_bounded(self) -> None:
        root = self._root()
        _write(root, "D1", ["anchor anchor anchor anchor"])
        for i, term in enumerate(["t1", "t2", "t3", "t4", "t5"]):
            filler = " ".join(f"pad{i}{j}" for j in range(9))
            _write(root, f"D{i + 2}", [f"{term} {filler}"])
        result = document_text.search_corpus(
            root, "anchor t1 t2 t3 t4 t5")
        self.assertLessEqual(result["passage_count"], 4)


class ObligationsTest(unittest.TestCase):
    def _case(self) -> CaseRecord:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        fake = support.scripted(root, [])
        run_id = new_run(root, instructions="i", objective="o",
                         verification=True)
        case = CaseRecord(root, run_id)
        analyze(fake.client, root, ["DOC-REPORT", "DOC-MINUTES"], "x")
        case.set_checklist([support.checklist_item("item_x")])
        case.set_task_type("investigation")
        for i, c in enumerate(
                reasoning.REQUIRED_CLASSES["investigation"]):
            case.data.setdefault("foundational", []).append(
                {"id": f"F{i + 1}", "class": c,
                 "question": f"q {c}?", "criterion": f"resolve {c}",
                 "status": "answered", "issue": "item_x"})
        return case

    def test_status_tracks_recorded_facts(self) -> None:
        case = self._case()
        case.set_evidence_needs(
            "item_x",
            [{"label": "count",
              "passages": [["DOC-REPORT", 1]]},
             {"label": "statement",
              "passages": [["DOC-MINUTES", 1]]}],
        )
        status = case.evidence_status("item_x")
        self.assertFalse(status["sufficient"])
        self.assertEqual(2, len(status["missing"]))
        case.record_fact(
            "item_x", "DOC-REPORT", 1,
            "Open Severity 1 defects: 21 at period end.",
            "records show 21 open sev-1 defects")
        status = case.evidence_status("item_x")
        self.assertFalse(status["sufficient"])
        self.assertEqual("statement", status["missing"][0]["label"])
        text = support.INITIAL["DOC-MINUTES"][0]
        case.record_fact(
            "item_x", "DOC-MINUTES", 1, text[:60],
            "minutes state readiness")
        self.assertTrue(case.evidence_status("item_x")["sufficient"])

    def test_gate_blocks_undisclosed_shortfall(self) -> None:
        case = self._case()
        case.set_evidence_needs(
            "item_x",
            [{"label": "statement",
              "passages": [["DOC-MINUTES", 1]]}])
        memo = {"findings": [{"item": "item_x", "status": "asserted",
                              "summary": "s"}], "gaps": ""}
        problems = reasoning.run_gate(case.data, memo)["problems"]
        self.assertTrue(
            any("statement" in p for p in problems), problems)

    def test_gate_passes_disclosed_shortfall(self) -> None:
        case = self._case()
        case.set_evidence_needs(
            "item_x",
            [{"label": "statement",
              "passages": [["DOC-MINUTES", 1]]}])
        memo = {"findings": [{"item": "item_x", "status": "asserted",
                              "summary": "s"}],
                "gaps": "statement passage never retrieved"}
        problems = reasoning.run_gate(case.data, memo)["problems"]
        self.assertFalse(
            [p for p in problems if "statement" in p], problems)

    def test_needs_refused_without_item(self) -> None:
        case = self._case()
        with self.assertRaises(CaseError):
            case.set_evidence_needs("nope", [])
        with self.assertRaises(CaseError):
            case.evidence_status("item_x")

    def test_needs_script_lists_status(self) -> None:
        from scripts import needs as needs_script

        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        fake = support.scripted(root, [])
        run_id = new_run(root, instructions="i", objective="o",
                         verification=True)
        case = CaseRecord(root, run_id)
        case.set_checklist([support.checklist_item("item_x")])
        report = needs_script.needs_for(root, run_id)
        self.assertEqual("undeclared", report["items"][0]["state"])
        case.set_evidence_needs(
            "item_x",
            [{"label": "x", "passages": [["DOC-REPORT", 1]]}])
        report = needs_script.needs_for(root, run_id)
        self.assertEqual("insufficient", report["items"][0]["state"])
        with self.assertRaises(RuntimeError):
            needs_script.needs_for(root, "run-nope")

    def test_malformed_passages_refused(self) -> None:
        case = self._case()
        for bad in (["DOC-REPORT"], ["", 1], ["DOC-REPORT", 0],
                    ["DOC-REPORT", "p1"], "DOC-REPORT", None):
            with self.assertRaises(CaseError, msg=repr(bad)):
                case.set_evidence_needs(
                    "item_x",
                    [{"label": "x", "passages": [bad]}])
        self.assertNotIn("evidence_needs", case.data)

    def test_refreeze_keeps_history(self) -> None:
        case = self._case()
        case.set_evidence_needs(
            "item_x",
            [{"label": "a", "passages": [["DOC-REPORT", 1]]},
             {"label": "b", "passages": [["DOC-MINUTES", 1]]}])
        narrowed = case.set_evidence_needs(
            "item_x",
            [{"label": "a", "passages": [["DOC-REPORT", 1]]}])
        self.assertEqual(1, len(narrowed["required"]))
        self.assertEqual(1, len(narrowed["history"]))
        self.assertEqual(2, len(narrowed["history"][0]["required"]))

    def test_corrupt_shapes_fail_closed(self) -> None:
        case = self._case()
        case.set_evidence_needs(
            "item_x",
            [{"label": "a", "passages": [["DOC-REPORT", 1]]}])
        case.data["evidence_needs"]["item_x"]["required"] = "junk"
        with self.assertRaises(CaseError):
            case.evidence_status("item_x")
        memo = {"findings": [{"item": "item_x", "status": "asserted",
                              "summary": "s"}], "gaps": ""}
        problems = reasoning.run_gate(case.data, memo)["problems"]
        self.assertTrue(
            any("corrupt evidence needs" in p for p in problems),
            problems)

    def test_uncountable_fact_keeps_need_open(self) -> None:
        case = self._case()
        case.set_evidence_needs(
            "item_x",
            [{"label": "a", "passages": [["DOC-REPORT", 1]]}])
        case.data.setdefault("facts", []).append(
            {"item": "item_x", "document_id": "DOC-REPORT",
             "page": None})
        status = case.evidence_status("item_x")
        self.assertFalse(status["sufficient"])
        self.assertEqual("a", status["missing"][0]["label"])

    def test_tool_declares_needs(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        fake = support.scripted(root, [])
        run_id = new_run(root, instructions="i", objective="o",
                         verification=True)
        case = CaseRecord(root, run_id)
        case.set_checklist([support.checklist_item("item_x")])
        out = tools.run(
            fake.client, root, run_id,
            call("set_evidence_needs",
                 {"item": "item_x",
                  "required": [{"label": "count",
                                "passages": [["DOC-REPORT", 1]]}]}))
        self.assertEqual("item_x", out["needs"]["item"])
        self.assertIn("set_evidence_needs", tools.names(True))
        self.assertIn("set_evidence_needs", tools.names(False))


if __name__ == "__main__":
    unittest.main()
