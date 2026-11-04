"""Instance hand notes: freehand lawyer annotation, version-neutral.

A note records anything worth remembering that fits no row,
check, or review field. Like feedback and marks, notes never
bump the version or reset review; blank notes are refused.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_kit import artifacts
from agent_kit.case_record import CaseError, CaseRecord


class NotesTest(unittest.TestCase):
    def test_add_lists_and_ignores_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = artifacts.build_demo(root)
            record = CaseRecord(root, run_id)
            instance = record.artifact_get("A1")
            versions = len(instance["versions"])
            first = artifacts.add_note(
                instance, "Call the clerk about sealing.",
                "Counsel")
            second = artifacts.add_note(instance, "Second thought.")
            self.assertEqual("N1", first["id"])
            self.assertEqual("N2", second["id"])
            self.assertEqual("Counsel", first["author"])
            self.assertEqual("", second["author"])
            self.assertEqual(2, len(instance["notes"]))
            self.assertEqual(versions, len(instance["versions"]))
            self.assertEqual("pending", instance["review"]["decision"])
            with self.assertRaises(CaseError):
                artifacts.add_note(instance, "   ")


if __name__ == "__main__":
    unittest.main()
