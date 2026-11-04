"""Citation extraction, registration storage, and gate advisory (Item 3, G1).

agent_kit.citations wraps eyecite in plain-data forms; registering
an authority stores the parsed forms with the extractor version;
the gate advises when a verified registration parsed as no legal
citation. The known-answer eval (eval/citation_cases.py +
citation_run.py) pins extraction behavior; this module pins the
registry/gate plumbing around it.

Honesty boundary, asserted here: parsing is structure, not
verification. "999 U.S. 999" parses (U.S. Reports is known);
whether that volume and page exist needs a corpus lookup (gap
G3) this module does not perform.
"""

from __future__ import annotations

import unittest

from agent_kit import citations, reasoning
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from eval import citation_run
from tests import support


class ExtractTest(unittest.TestCase):
    def test_full_case_groups(self) -> None:
        (form,) = citations.extract("See Bush v. Gore, 531 U.S. 98 (2000).")
        self.assertEqual("full_case", form["kind"])
        self.assertEqual("531 U.S. 98", form["text"])
        self.assertEqual("U.S.", form["reporter"])
        self.assertEqual("531", form["volume"])
        self.assertEqual("98", form["page"])
        self.assertEqual("2000", form["year"])
        self.assertEqual("scotus", form["court"])
        self.assertEqual("", form["antecedent"])

    def test_short_id_supra_resolve(self) -> None:
        forms = citations.extract(
            "Foo v. Bar, 1 U.S. 2 (1999). Id. at 3. Foo, supra, at 5. "
            "See 1 U.S., at 4."
        )
        kinds = [f["kind"] for f in forms]
        self.assertEqual(
            ["full_case", "id", "supra", "short_case"], kinds
        )
        self.assertEqual("", forms[0]["antecedent"])
        for form in forms[1:]:
            self.assertEqual("1 U.S. 2", form["antecedent"], form)

    def test_statute_and_journal(self) -> None:
        (statute,) = citations.extract("Liability under 42 U.S.C. § 1983.")
        self.assertEqual("law", statute["kind"])
        (journal,) = citations.extract("See 1 Minn. L. Rev. 1.")
        self.assertEqual("journal", journal["kind"])

    def test_unknown_reporter_silent(self) -> None:
        self.assertEqual([], citations.extract("See 22 Fake Reporter 1."))
        self.assertEqual([], citations.extract("No citations here."))
        self.assertEqual([], citations.extract(""))
        self.assertEqual([], citations.extract(None))

    def test_parsed_is_not_verified(self) -> None:
        # Known reporter, almost certainly nonexistent volume: the
        # extractor reports structure; existence needs gap G3.
        (form,) = citations.extract("See 999 U.S. 999 (2026).")
        self.assertEqual("full_case", form["kind"])
        self.assertEqual("U.S.", form["reporter"])

    def test_backend_pinned(self) -> None:
        self.assertTrue(
            citations.CITATION_BACKEND.startswith("eyecite/"),
            citations.CITATION_BACKEND,
        )
        self.assertIn("reporters-db/", citations.CITATION_BACKEND)


class RegistrationParseTest(unittest.TestCase):
    def setUp(self) -> None:
        from agent_kit.tools.analyze_document import analyze

        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        analyze(self.fake.client, self.root, ["DOC-REPORT"], "x")
        self.case.note_source("DOC-REPORT", "ca-leginfo", "Report", "v1")

    def _register(self, citation: str, verification: str = "verified") -> dict:
        return self.case.register_authority(
            proposition="Courts apply the rule.",
            document_id="DOC-REPORT", citation=citation,
            version="v1", effective_date="2026-01-01",
            level="persuasive", verification=verification,
            treatment_checked=True, treatment="none")

    def test_real_citation_stored_parsed(self) -> None:
        entry = self._register("Bush v. Gore, 531 U.S. 98 (2000)")
        self.assertEqual(1, len(entry["parsed_citations"]))
        self.assertEqual("full_case", entry["parsed_citations"][0]["kind"])
        self.assertEqual(
            citations.CITATION_BACKEND, entry["citation_backend"]
        )

    def test_fixture_citation_stored_empty(self) -> None:
        entry = self._register("Cal. Civ. Code (fixture excerpt)")
        self.assertEqual([], entry["parsed_citations"])


class CitationGateTest(unittest.TestCase):
    def _data(self, authority: dict) -> dict:
        return {
            "task_type": "research",
            "facts": [
                {"id": "F1", "kind": "legal_rule",
                 "document_id": "DOC-REPORT",
                 "claim": "the rule applies", "authority": "A1"}
            ],
            "sources": {"DOC-REPORT": {"repo": "ca-leginfo"}},
            "authorities": {"A1": authority},
        }

    def _authority(self, **kw) -> dict:
        entry = {
            "id": "A1", "document_id": "DOC-REPORT",
            "citation": "Bush v. Gore, 531 U.S. 98 (2000)",
            "level": "persuasive", "verification": "verified",
            "parsed_citations": [{"kind": "full_case"}],
        }
        entry.update(kw)
        return entry

    def test_parsed_verified_silent(self) -> None:
        result = reasoning.run_gate(self._data(self._authority()), {})
        self.assertFalse(
            [
                p
                for p in result["problems"]
                if "registered authority" in p or "provenance" in p
            ],
            result["problems"],
        )
        self.assertFalse(
            [a for a in result["advisory"] if "parsed as no legal" in a],
            result["advisory"],
        )

    def test_verified_unparseable_advises(self) -> None:
        result = reasoning.run_gate(
            self._data(self._authority(
                citation="Cal. Civ. Code (fixture excerpt)",
                parsed_citations=[],
            )), {}
        )
        self.assertTrue(
            any("parsed as no legal citation" in a
                for a in result["advisory"]),
            result["advisory"],
        )

    def test_legacy_entry_without_parse_key_silent(self) -> None:
        authority = self._authority()
        del authority["parsed_citations"]
        result = reasoning.run_gate(self._data(authority), {})
        self.assertFalse(
            [a for a in result["advisory"] if "parsed as no legal" in a],
            result["advisory"],
        )


class CitationEvalTest(unittest.TestCase):
    def test_floors_hold(self) -> None:
        reports = citation_run.run_all()
        summary = citation_run.summarize(reports)
        self.assertEqual(
            summary["extract"]["total"], summary["extract"]["hit"],
            summary["verdict"],
        )
        self.assertEqual(
            summary["silence"]["total"], summary["silence"]["hit"],
            summary["verdict"],
        )
        self.assertGreater(summary["stretch"]["total"], 0)
        for report in reports:
            self.assertEqual(
                citation_run.REPORT_KEYS, set(report),
                f"report schema drift: {report['case']}",
            )


if __name__ == "__main__":
    unittest.main()
