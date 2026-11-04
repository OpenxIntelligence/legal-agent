"""Runtime commands keep matter data separate from their code checkout."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))


def load_script(name):
    path = CODE_ROOT / ("legal_agent/__main__.py" if name == "runtime" else f"scripts/{name}.py")
    module_name = "legal_agent.test_entrypoint" if name == "runtime" else f"matter_root_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MatterRuntimeRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {"LEGAL_AGENT_ROOT": str(self.root), "CASE_DIR": str(self.root / "case")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_all_scoped_entrypoints_resolve_data_root_and_preserve_code_root(self):
        for name in ("runtime", "review", "verify", "branch", "describe", "quarantine", "preflight", "audit"):
            with self.subTest(name=name):
                module = load_script(name)
                self.assertEqual(self.root, module.ROOT)
                self.assertEqual(CODE_ROOT, module.CODE_ROOT)

    def test_no_override_preserves_original_checkout_default(self):
        with patch.dict(os.environ, {"LEGAL_AGENT_ROOT": ""}):
            for name in ("runtime", "review", "verify", "branch", "describe", "quarantine", "preflight", "audit"):
                with self.subTest(name=name):
                    self.assertEqual(CODE_ROOT, load_script(name).ROOT)

    def test_bad_override_refuses_instead_of_falling_back_to_checkout(self):
        with patch.dict(os.environ, {"LEGAL_AGENT_ROOT": str(self.root / "missing")}):
            for name in ("runtime", "review", "verify", "branch", "describe", "quarantine", "preflight", "audit"):
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    load_script(name)

    def test_run_and_resume_receive_only_the_selected_matter_root(self):
        module = load_script("runtime")
        client = object()
        for args, function in [(["agent", "--turns", "3"], "run_agent"),
                               (["agent", "--resume", "run-synthetic", "--turns", "2"], "resume_run")]:
            with self.subTest(function=function), patch.object(module, "make_client", return_value=client), \
                    patch.object(module, function, return_value={"run_id": "run-synthetic", "status": "paused"}) as invoke, \
                    patch.object(sys, "argv", args), redirect_stdout(StringIO()):
                module.main()
                self.assertEqual(self.root, invoke.call_args.kwargs["root"])
                self.assertIs(client, invoke.call_args.kwargs["client"])

    def test_review_selects_matter_case_record(self):
        path = self.root / "workspace/case_records/run-synthetic.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        module = load_script("review")
        record = MagicMock()
        record.review_memo.return_value = {"status": "approved"}
        with patch.object(module, "CaseRecord", return_value=record) as case, \
                patch.object(sys, "argv", ["review", "run-synthetic", "--approve"]), redirect_stdout(StringIO()):
            module.main()
        case.assert_called_once_with(self.root, "run-synthetic")

    def test_verify_loads_context_and_saves_result_in_matter(self):
        path = self.root / "workspace/runs/run-synthetic/progress.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        module = load_script("verify")
        client, ctx = object(), object()
        result = {"what": "memo", "passed": False}
        with patch.object(module, "make_client", return_value=client), \
                patch.object(module, "context", return_value=ctx) as context, \
                patch.object(module, "verify_memo", return_value=result), \
                patch.object(module, "save", return_value=result) as save, \
                patch.object(sys, "argv", ["verify", "run-synthetic", "--memo", "--json"]), redirect_stdout(StringIO()):
            module.main()
        context.assert_called_once_with(client, self.root, "run-synthetic")
        save.assert_called_once_with(self.root, "run-synthetic", result)

    def test_branch_reads_selected_matter(self):
        module = load_script("branch")
        client = object()
        with patch.object(module, "make_client", return_value=client), \
                patch.object(module, "branch", return_value="run-new") as branch, \
                patch.object(sys, "argv", ["branch", "run-synthetic"]), redirect_stdout(StringIO()):
            module.main()
        branch.assert_called_once_with(client, self.root, "run-synthetic")

    def test_quarantine_list_and_release_use_matter_root(self):
        module = load_script("quarantine")
        with patch.object(module.redactions, "quarantine_list", return_value={}) as listing, \
                patch.object(sys, "argv", ["quarantine", "--list"]), redirect_stdout(StringIO()):
            module.main()
        listing.assert_called_once_with(self.root)
        with patch.object(module.redactions, "release", return_value={}) as release, \
                patch.object(sys, "argv", ["quarantine", "DOC", "--release", "--notes", "inspected"]), redirect_stdout(StringIO()):
            module.main()
        release.assert_called_once_with(self.root, "DOC", note="inspected")

    def test_audit_uses_override_but_preserves_cwd_without_override(self):
        module = load_script("audit")
        with patch.object(module, "audit_run", return_value={"passed": True}) as audit, redirect_stdout(StringIO()):
            module.main(["run-synthetic", "--json"])
        audit.assert_called_once_with(self.root, "run-synthetic")
        with patch.dict(os.environ, {"LEGAL_AGENT_ROOT": ""}), \
                patch.object(module, "audit_run", return_value={"passed": True}) as audit, redirect_stdout(StringIO()):
            module.main(["run-synthetic", "--json"])
        audit.assert_called_once_with(Path.cwd(), "run-synthetic")

    def test_preflight_defaults_to_selected_matter(self):
        module = load_script("preflight")
        with patch.object(module, "case_dir", side_effect=RuntimeError("stop before reading fixtures")) as case:
            with self.assertRaisesRegex(RuntimeError, "stop before"):
                module.preflight(expect_release="initial")
        case.assert_called_once_with(self.root)

    def test_describe_reads_matter_assignment_and_checkout_build_status(self):
        (self.root / "case").mkdir()
        (self.root / "case/objective.md").write_text("# Synthetic matter\nQuestion from the selected matter.")
        inbox = self.root / "workspace/inbox"
        inbox.mkdir(parents=True)
        (inbox / "example.txt").write_text("synthetic")
        module = load_script("describe")
        with patch.object(module, "built", return_value=True) as built:
            data = module.describe()
        self.assertEqual("Synthetic matter", data["title"])
        self.assertEqual(1, data["documents"])
        self.assertTrue(all(call.args[0].is_relative_to(CODE_ROOT) for call in built.call_args_list))


if __name__ == "__main__":
    unittest.main()
