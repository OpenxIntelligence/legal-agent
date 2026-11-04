"""Authority assessment: verified must resolve to a supporting act.

P0: the filing gate must not accept a legal authority as verified
merely because the agent asserted the word. Registration records
the claim as stated; acceptance happens at the gate, which
resolves each relied-upon "verified" entry to a passing
assessment record (assess_authority) instead of trusting the
bare assertion.
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning
from agent_kit.case_record import CaseError

from tests.test_authority_proposition import AuthorityPropositionTest


QUOTE = "Open Severity 1 defects: 21 at period end."


class AuthorityAssessmentTest(unittest.TestCase):
    def _case(self):
        helper = AuthorityPropositionTest(
            "test_explicit_link_satisfies_gate")
        helper.addCleanup = self.addCleanup
        case = helper._case()
        helper._engage(case)
        self._helpers.append(helper)
        return case

    def setUp(self) -> None:
        self._helpers = []

    def _register(self, case, verification="provisional"):
        checked = verification == "verified"
        return case.register_authority(
            proposition="Courts apply the 21-defect rule.",
            document_id="DOC-REPORT",
            citation="Report 21 (2026)",
            version="fixture-v1",
            effective_date="2026-01-01",
            level="persuasive",
            verification=verification,
            treatment_checked=checked,
            treatment="none found" if checked else "not checked",
        )

    def _rely(self, case, entry):
        case.record_fact(
            "item_x",
            "DOC-REPORT",
            1,
            QUOTE,
            "Courts apply the 21-defect rule.",
            kind="legal_rule",
            authority=entry["id"],
        )

    def _gate_problems(self, case):
        return reasoning.run_gate(case.data, {"findings": []})["problems"]

    def test_bare_assertion_not_trusted_as_verified(self):
        case = self._case()
        entry = self._register(case, verification="verified")
        self._rely(case, entry)
        problems = self._gate_problems(case)
        self.assertTrue(
            any("assessment" in p for p in problems),
            problems,
        )

    def test_assessment_flow_verifies(self):
        case = self._case()
        entry = self._register(case)
        record = case.assess_authority(
            entry["id"],
            page=1,
            quote=QUOTE,
            treatment_read=True,
            detail="no adverse treatment on the page",
        )
        self.assertEqual("passed", record["outcome"])
        stored = case.data["authorities"][entry["id"]]
        self.assertEqual("verified", stored["verification"])
        self.assertEqual(record["id"], stored["assessment"])
        self.assertTrue(stored["treatment_checked"])
        self._rely(case, entry)
        self.assertFalse(
            [p for p in self._gate_problems(case)
             if "assessment" in p],
        )

    def test_miscopied_quote_refused(self):
        case = self._case()
        entry = self._register(case)
        with self.assertRaises(CaseError):
            case.assess_authority(
                entry["id"],
                page=1,
                quote="Open Severity 1 defects: 22 at period end.",
                treatment_read=True,
            )
        stored = case.data["authorities"][entry["id"]]
        self.assertEqual("provisional", stored["verification"])
        self.assertEqual([], reasoning.validate_authority(dict(stored)))

    def test_unread_treatment_refused(self):
        case = self._case()
        entry = self._register(case)
        with self.assertRaises(CaseError):
            case.assess_authority(
                entry["id"],
                page=1,
                quote=QUOTE,
                treatment_read=False,
            )
        stored = case.data["authorities"][entry["id"]]
        self.assertEqual("provisional", stored["verification"])

    def test_gate_blocks_forged_link(self):
        case = self._case()
        entry = self._register(case, verification="verified")
        case.data["authorities"][entry["id"]]["assessment"] = "AA999"
        self._rely(case, entry)
        problems = self._gate_problems(case)
        self.assertTrue(
            any("AA999" in p or "assessment" in p for p in problems),
            problems,
        )

    def test_provisional_needs_no_link(self):
        case = self._case()
        entry = self._register(case)
        self._rely(case, entry)
        self.assertFalse(
            [p for p in self._gate_problems(case)
             if "assessment" in p],
        )


if __name__ == "__main__":
    unittest.main()
