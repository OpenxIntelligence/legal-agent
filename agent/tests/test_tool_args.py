"""Tool argument validation: model-supplied args fail closed.

Tool DEFINITION schemas are model-facing only — nothing enforces
them before run(). Malformed args must therefore raise CaseError
(a clean tool refusal), never leak TypeError/AttributeError or
silently coerce junk into a vacuous search.
"""

from __future__ import annotations

import unittest

from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.tools import (
    check_signatures,
    record_fact,
    search_corpus,
    set_evidence_needs,
)
from tests import support


class _Ctx:
    def __init__(self, root, case):
        self.root = root
        self.case = case


class SearchCorpusArgsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.ctx = _Ctx(self.root, CaseRecord(self.root, "probe-run"))

    def test_non_string_query_refused(self) -> None:
        for query in (123, None, ["a"], {"q": "x"}):
            with self.subTest(query=query), self.assertRaises(CaseError):
                search_corpus.run(self.ctx, {"query": query})

    def test_non_list_document_ids_refused(self) -> None:
        with self.assertRaises(CaseError):
            search_corpus.run(
                self.ctx, {"query": "defects", "document_ids": 123})

    def test_non_string_document_id_refused(self) -> None:
        with self.assertRaises(CaseError):
            search_corpus.run(
                self.ctx, {"query": "defects", "document_ids": ["A", 5]})

    def test_empty_query_refused(self) -> None:
        with self.assertRaises(CaseError):
            search_corpus.run(self.ctx, {"query": "  "})


class SetNeedsArgsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        case = CaseRecord(self.root, "probe-run")
        case.data.setdefault("checklist", {})["item1"] = {}
        self.ctx = _Ctx(self.root, case)

    def test_non_list_required_refused(self) -> None:
        with self.assertRaises(CaseError):
            set_evidence_needs.run(
                self.ctx, {"item": "item1", "required": 123})

    def test_non_list_adverse_refused(self) -> None:
        with self.assertRaises(CaseError):
            set_evidence_needs.run(
                self.ctx, {"item": "item1", "required": [],
                           "adverse": 123})

    def test_non_list_superseded_refused(self) -> None:
        with self.assertRaises(CaseError):
            set_evidence_needs.run(
                self.ctx, {"item": "item1", "required": [],
                           "superseded": {"a": 1}})


class PageArgsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.ctx = _Ctx(self.root, CaseRecord(self.root, "probe-run"))

    def test_record_fact_bad_page_refused(self) -> None:
        base = {"item": "i1", "document_id": "D", "quote": "q",
                "claim": "c"}
        for page in ("abc", [1], {"n": 1}, True, 1.5):
            with self.subTest(page=page):
                with self.assertRaises(CaseError) as ctx:
                    record_fact.run(self.ctx, {**base, "page": page})
                self.assertIn("integer page", str(ctx.exception))

    def test_record_fact_accepts_int_and_digits(self) -> None:
        base = {"item": "i1", "document_id": "D", "quote": "q",
                "claim": "c"}
        for page in (3, "3"):
            with self.subTest(page=page):
                with self.assertRaises(CaseError) as ctx:
                    record_fact.run(self.ctx, {**base, "page": page})
                self.assertNotIn("integer page", str(ctx.exception))

    def test_signatures_bad_pages_refused(self) -> None:
        base = {"document_id": "D", "reference_document_id": "R",
                "signer": "S", "claim": "c"}
        for args in ({"page": "abc", "reference_page": 1},
                     {"page": 1, "reference_page": [2]},
                     {"page": True, "reference_page": 1}):
            with self.subTest(args=args):
                with self.assertRaises(CaseError) as ctx:
                    check_signatures.run(self.ctx, {**base, **args})
                self.assertIn("integer page", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
