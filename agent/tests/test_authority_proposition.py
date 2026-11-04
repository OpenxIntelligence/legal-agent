"""Authority reliance is explicit, not document membership (§7/§11).

Landed mechanism (peer design change): a legal-rule fact
names the registered authority it relies on via its A-id;
the gate resolves the link. A real claim with no link
fails closed, so registering P1 can never validate a
different P2 from the same document; an explicit link to
a registered entry satisfies the gate. (Wording-equality
was rejected: claims legitimately summarize while
propositions state the rule.) Assessment links are
asserted: a "verified" status needs a supporting
assess_authority record (quote re-checked, sha pinned);
the gate resolves the link.
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support


class AuthorityPropositionTest(unittest.TestCase):
    def _case(self):
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        fake = support.scripted(root, [])
        run_id = new_run(root, instructions="i", objective="o",
                         verification=True)
        case = CaseRecord(root, run_id)
        analyze(fake.client, root, ["DOC-REPORT"], "x")
        case.set_checklist([support.checklist_item("item_x")])
        case.note_source("DOC-REPORT", "ca-leginfo", "Report", "v1")
        return case

    def _engage(self, case):
        case.set_task_type("investigation")
        for i, c in enumerate(
                reasoning.REQUIRED_CLASSES["investigation"]):
            case.data.setdefault("foundational", []).append(
                {"id": f"F{i + 1}", "class": c,
                 "question": f"q {c}?", "criterion": f"resolve {c}",
                 "status": "answered"})

    def _register(self, case):
        entry = case.register_authority(
            proposition="Courts apply the 21-defect rule.",
            document_id="DOC-REPORT", citation="Report p1",
            version="v1", effective_date="2026-01-01",
            level="persuasive", verification="provisional",
            treatment_checked=False, treatment="not checked")
        case.assess_authority(
            entry["id"], page=1,
            quote="Open Severity 1 defects: 21 at period end.",
            treatment_read=True, detail="fixture assessment")
        return case.data["authorities"][entry["id"]]

    def test_unlinked_claim_fails_closed(self) -> None:
        case = self._case()
        self._register(case)
        case.record_fact(
            "item_x", "DOC-REPORT", 1,
            "Open Severity 1 defects: 21 at period end.",
            "A DIFFERENT proposition about appeal windows.",
            kind="legal_rule")
        self._engage(case)
        out = reasoning.run_gate(case.data, {"findings": []})
        self.assertIn(
            "registered authority", " ".join(out["problems"]),
            "an unlinked claim was validated by document "
            "membership alone",
        )

    def test_explicit_link_satisfies_gate(self) -> None:
        case = self._case()
        entry = self._register(case)
        case.record_fact(
            "item_x", "DOC-REPORT", 1,
            "Open Severity 1 defects: 21 at period end.",
            "A DIFFERENT proposition about appeal windows.",
            kind="legal_rule", authority=entry["id"])
        self._engage(case)
        out = reasoning.run_gate(case.data, {"findings": []})
        self.assertNotIn(
            "registered authority", " ".join(out["problems"]),
        )


if __name__ == "__main__":
    unittest.main()
