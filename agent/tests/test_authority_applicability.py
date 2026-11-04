"""Registered authorities must match the matter on jurisdiction and time.

A binding verified registration today passes the gate even when its
recorded jurisdiction differs from the matter's or its effective date
postdates the event it is cited for: no check in agent_kit,
verification, or quality compares authority jurisdiction/effective
against the matter. Both comparisons are structural cross-references
of recorded fields, not correctness judgments, so they belong in the
gate's contract (integration brief §6 applicability binding; Priority
E "genuine authority with wrong applicability/date").

The fix (peer-owned, reasoning.py): flag a legal-rule reliance whose
authority jurisdiction mismatches the matter jurisdiction, and one
whose effective date postdates the cited event date. Both marked
expectedFailure until that lands.
"""

from __future__ import annotations

import unittest

from tests.test_gate_adversarial import clean_research_case


def _with_authority(data: dict, authority: dict, fact_extra: dict) -> dict:
    fact = {"id": "F9", "kind": "legal_rule", "document_id": "DOC-A",
            "authority": "A1", "claim": "rule R governs"}
    fact.update(fact_extra)
    data["facts"].append(fact)
    entry = {"id": "A1", "document_id": "DOC-A",
             "level": "binding", "verification": "verified"}
    entry.update(authority)
    data["authorities"] = {"A1": entry}
    return data


class AuthorityApplicabilityTest(unittest.TestCase):
    def test_wrong_jurisdiction_authority_flagged(self) -> None:
        from agent_kit.reasoning import run_gate
        data, memo = clean_research_case()
        _with_authority(data, {"jurisdiction": "France"}, {})
        problems = run_gate(data, memo)["problems"]
        self.assertTrue(
            any("jurisdiction" in p for p in problems),
            f"no jurisdiction-mismatch problem: {problems}",
        )

    def test_authority_effective_after_event_flagged(self) -> None:
        from agent_kit.reasoning import run_gate
        data, memo = clean_research_case()
        _with_authority(
            data,
            {"jurisdiction": "California", "effective": "2024-01-01"},
            {"event_date": "2020-06-01"},
        )
        problems = run_gate(data, memo)["problems"]
        self.assertTrue(
            any("effective" in p for p in problems),
            f"no effective-date problem: {problems}",
        )


if __name__ == "__main__":
    unittest.main()
