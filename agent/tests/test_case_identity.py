"""Case storage identity checks also cover direct tool/delegation entry points."""
import json
import tempfile
import unittest
from pathlib import Path
from agent_kit.case_record import CaseRecord, CaseError

class CaseIdentityTests(unittest.TestCase):
    def test_invalid_identifiers_create_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in ("../escape", "/absolute", "run/child", "", None, [], "run\\escape"):
                with self.subTest(value=value), self.assertRaises(CaseError):
                    CaseRecord(root, value)
            self.assertEqual(list(root.iterdir()), [])

    def test_mismatched_record_is_not_loaded_or_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = CaseRecord(root, "probe-run")
            data = json.loads(case.path.read_text()); data['run_id'] = 'other-run'
            case.path.write_text(json.dumps(data)); original = case.path.read_bytes()
            with self.assertRaisesRegex(CaseError, 'does not match'):
                CaseRecord(root, "probe-run")
            self.assertEqual(case.path.read_bytes(), original)

    def test_symlinked_case_directory_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'workspace').mkdir(); outside = root/'outside'; outside.mkdir()
            (root/'workspace/case_records').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(CaseError, 'symbolic links'):
                CaseRecord(root, 'probe-run')
            self.assertEqual(list(outside.iterdir()), [])
