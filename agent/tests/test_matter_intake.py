"""Persisted matter intake contracts; no provider or private configuration."""
from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legal_matter_intake", ROOT / "ui/matters.py")
matters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matters)


class MatterIntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "matters"
        self.calls = []

        def ingest(root, document):
            self.calls.append((root, document))
            inbox = root / "workspace/inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / document["filename"]).write_text(document.get("text", ""))
            return {"document_id": Path(document["filename"]).stem,
                    "file": document["filename"], "state": "transcribed", "pages": 1}

        self.ingest = ingest
        self.store = matters.MatterStore(self.root, ingest)
        self.body = {"question": "Which passages contradict the asserted delivery acceptance?",
                     "jurisdiction": "Not established", "deliverable": "Evidence memo",
                     "documents": [{"filename": "DELIVERY.txt", "text": "Fictional delivery record."}]}

    def test_create_is_persisted_and_only_calls_local_ingestion(self):
        with patch("socket.socket", side_effect=AssertionError("no network")):
            item = self.store.create(self.body)
        path = self.store.path(item["id"])
        self.assertEqual("prepared", item["status"])
        self.assertEqual("DELIVERY", item["documents"][0]["id"])
        self.assertEqual(path, self.calls[0][0])
        self.assertTrue((path / "workspace/inbox/DELIVERY.txt").is_file())
        saved = json.loads((path / "matter.json").read_text())
        self.assertNotIn("Fictional delivery record.", json.dumps(saved))
        reopened = matters.MatterStore(self.root, self.ingest).get(item["id"])
        self.assertEqual(item, reopened)
        self.assertEqual(item["id"], self.store.list()[0]["id"])
        objective = (path / "case/objective.md").read_text()
        self.assertIn(self.body["question"], objective)
        self.assertIn("User-reported", objective)
        self.assertIn("counterevidence", objective)
        self.assertEqual("intake_checklist", item["messages"][-1]["kind"])

    def test_followup_queue_receipt_is_explicit_and_persisted(self):
        item = self.store.create(self.body)
        delivery = {"status": "queued", "run_id": "run-synthetic"}
        updated = self.store.message(item["id"], "Inspect the acceptance email.", delivery=delivery)
        self.assertEqual(delivery, updated["messages"][-2]["delivery"])
        self.assertEqual(delivery, updated["messages"][-1]["delivery"])
        self.assertEqual("Queued for next resume of run run-synthetic; not yet applied.",
                         updated["messages"][-1]["body"])
        self.assertEqual(updated, self.store.get(item["id"]))

    def test_followup_preserves_prior_messages_and_run_snapshots(self):
        item = self.store.create(self.body)
        path = self.store.path(item["id"])
        snapshot = path / "workspace/runs/run-kept/instructions.md"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text("original run instruction snapshot")
        first = self.store.message(item["id"], "Focus on the acceptance email first.")
        second = self.store.message(item["id"], "Also preserve contradictory accounts.")
        self.assertGreater(len(second["messages"]), len(first["messages"]))
        self.assertEqual("original run instruction snapshot", snapshot.read_text())
        objective = (path / "case/objective.md").read_text()
        self.assertIn("Focus on the acceptance email first.", objective)
        self.assertIn("Also preserve contradictory accounts.", objective)
        self.assertEqual("instruction_saved", second["messages"][-1]["kind"])
        self.assertIn("not run", second["messages"][-1]["body"])

    def test_concurrent_reopened_stores_do_not_lose_followups(self):
        item = self.store.create(self.body)
        def followup(index):
            store = matters.MatterStore(self.root, self.ingest)
            store.message(item["id"], f"Saved instruction number {index}.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(followup, range(12)))
        saved = self.store.get(item["id"])
        notes = [m["body"] for m in saved["messages"] if m["kind"] == "followup"]
        self.assertEqual(12, len(notes))
        self.assertEqual(12, len(set(notes)))

    def test_validation_happens_before_creating_a_matter_directory(self):
        invalid = [dict(self.body, question="short"),
                   dict(self.body, documents=[{"filename": "../../private.txt", "text": "x"}]),
                   dict(self.body, documents=[{"filename": "x.pdf", "text": "x"}]),
                   dict(self.body, documents=[{"filename": "x.pdf", "content_b64": "bogus"}]),
                   dict(self.body, documents=[{"filename": "x.txt", "text": "x"}] * 11),
                   dict(self.body, jurisdiction="x" * 501)]
        for body in invalid:
            with self.subTest(body=str(body)[:100]):
                with self.assertRaises(ValueError):
                    self.store.create(body)
                self.assertFalse(self.root.exists())
        self.assertEqual([], self.calls)

    def test_duplicate_document_ids_cannot_replace_an_upload(self):
        body = dict(self.body, documents=[{"filename": "same.txt", "text": "one"},
                                          {"filename": "same.md", "text": "two"}])
        with self.assertRaises(ValueError):
            self.store.create(body)
        self.assertEqual([], self.calls)

    def test_empty_intake_declares_missing_information_without_inventing_advice(self):
        item = self.store.create({"question": "Help me understand which facts I must establish."})
        self.assertTrue(any("document" in s.lower() for s in item["missing"]))
        self.assertTrue(any("jurisdiction" in s.lower() for s in item["missing"]))
        self.assertTrue(any("deadline" in s.lower() for s in item["missing"]))
        self.assertTrue(item["plan"])
        self.assertIn("not a model", item["messages"][-1]["body"])

    def test_ingestion_failure_keeps_explicit_incomplete_matter_and_prior_upload(self):
        def fails(root, doc):
            if doc["filename"] == "FAIL.txt":
                raise RuntimeError("Synthetic upload failure")
            return self.ingest(root, doc)
        store = matters.MatterStore(self.root, fails)
        body = dict(self.body, documents=self.body["documents"] + [{"filename": "FAIL.txt", "text": "x"}])
        item = store.create(body)
        self.assertEqual("incomplete", item["status"])
        self.assertEqual(1, len(item["documents"]))
        self.assertEqual(item, store.get(item["id"]))
        self.assertTrue((store.path(item["id"]) / "workspace/inbox/DELIVERY.txt").exists())
        self.assertIn("failed", item["messages"][-1]["body"].lower())

    def test_path_validation_and_unowned_directories(self):
        self.root.mkdir()
        unowned = self.root / ("matter-" + "a" * 32)
        unowned.mkdir()
        (unowned / "keep.txt").write_text("unique work")
        for value in ["../outside", "/tmp/other", unowned.name, "matter-nope"]:
            with self.subTest(value=value):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.store.get(value)
        self.assertEqual([], self.store.list())
        self.assertEqual("unique work", (unowned / "keep.txt").read_text())

    def test_later_upload_preserves_original_records_and_other_matter(self):
        item = self.store.create(self.body)
        other = self.store.create({"question": "A different fictional matter needs independent records."})
        snapshot = self.store.get(other["id"])
        with patch("socket.socket", side_effect=AssertionError("no network")):
            updated = self.store.add_documents(item["id"], {"documents": [{"filename": "LATER.md", "text": "Later fictional record"}]})
        self.assertEqual(["DELIVERY", "LATER"], [d["id"] for d in updated["documents"]])
        self.assertEqual(updated, self.store.get(item["id"]))
        self.assertEqual(snapshot, self.store.get(other["id"]))
        self.assertFalse((self.store.path(other["id"]) / "workspace/inbox/LATER.md").exists())
        self.assertIn("have not been updated", updated["messages"][-1]["body"])

    def test_later_upload_validation_is_atomic_and_duplicates_do_not_overwrite(self):
        item = self.store.create(self.body)
        for docs in ([{"filename": "NEW.txt", "text": "new"}, {"filename": "delivery.md", "text": "replace"}],
                     [{"filename": "../escape.txt", "text": "no"}], [],
                     [{"filename": f"record{i}.txt", "text": "x"} for i in range(10)]):
            with self.assertRaises(ValueError):
                self.store.add_documents(item["id"], {"documents": docs})
            self.assertEqual(item, self.store.get(item["id"]))
        self.assertEqual(1, len(self.calls))
        self.assertEqual("Fictional delivery record.", (self.store.path(item["id"]) / "workspace/inbox/DELIVERY.txt").read_text())

    def test_later_partial_upload_retry_clears_only_recovered_error(self):
        item = self.store.create(self.body)
        def fails(root, doc):
            if doc["filename"] == "FAIL.txt":
                raise RuntimeError("Synthetic upload failure")
            return self.ingest(root, doc)
        self.store.ingest = fails
        result = self.store.add_documents(item["id"], {"documents": [{"filename": "OK.txt", "text": "ok"}, {"filename": "FAIL.txt", "text": "retry"}]})
        self.assertEqual("incomplete", result["status"])
        self.assertEqual(2, len(result["documents"]))
        self.assertEqual("FAIL.txt", result["upload_errors"][0]["file"])
        self.store.ingest = self.ingest
        recovered = self.store.add_documents(item["id"], {"documents": [{"filename": "FAIL.txt", "text": "retry"}]})
        self.assertEqual("prepared", recovered["status"])
        self.assertEqual([], recovered["upload_errors"])
        self.assertEqual(3, len(recovered["documents"]))

    def test_concurrent_later_uploads_keep_all_documents(self):
        item = self.store.create(self.body)
        def upload(index):
            store = matters.MatterStore(self.root, self.ingest)
            store.add_documents(item["id"], {"documents": [{"filename": f"NEW{index}.txt", "text": "fictional"}]})
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(upload, range(4)))
        self.assertEqual(5, len(self.store.get(item["id"])["documents"]))

    def test_symlink_matter_and_objective_are_rejected(self):
        item = self.store.create(self.body)
        path = self.store.path(item["id"])
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.root / ("matter-" + "b" * 32)
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.path(link.name)
        objective = path / "case/objective.md"
        objective.unlink()
        protected = outside / "protected.txt"
        protected.write_text("preserve")
        objective.symlink_to(protected)
        with self.assertRaises(ValueError):
            self.store.message(item["id"], "New followup should never follow this symlink.")
        self.assertEqual("preserve", protected.read_text())


if __name__ == "__main__":
    unittest.main()
