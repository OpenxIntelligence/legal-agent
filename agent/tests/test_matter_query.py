"""In-memory query isolation and denial tests; no filesystem datasets or models."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("matter_query_test", REPO / "ui/matter_query.py")
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)

MATTER = "matter-" + "a" * 32
OTHER = "matter-" + "b" * 32
RUN = "run-query-test"


def fixture():
    matter = {"id": MATTER, "runs": [{"id": RUN}], "documents": [
        {"id": "email", "file": "email.txt", "state": "ready"},
    ]}
    citation = {"document_id": "email", "page": 2, "quote": "The deadline is Friday."}
    run = {"id": RUN, "case": {"run_id": RUN,
        "facts": [{"id": "F1", "item": "notice", "claim": "Notice arrived Friday.", "stance": "supports", "kind": "fact", **citation}],
        "memo": {"findings": [{"item": "notice", "summary": "Review the delivery date.", "status": "uncertain", "citations": [citation]}]},
        "requests": [{"id": "R1", "item": "notice", "need": "Original delivery receipt", "rule": "Need exact date", "purpose": "evidence", "status": "open", "response": None, "at": "2026-11-03"}, {"id": "R2", "status": "answered", "response": "A receipt exists"}],
        "artifacts": {"A1": {"id": "A1", "template": "citation-claim-audit", "matter": RUN,
            "rows": [{"row_id": "R1", "kind": "finding", "item": "notice", "assertion": "Edited review assertion.", "assertion_version": 2, "finding_status": "uncertain", "citations": [citation], "checks": {"structural": {"outcome": "passed"}, "semantic": {"outcome": "unavailable"}}}],
            "review": {"decision": "pending"},
            "defects": [{"id": "D1", "category": "citation_incomplete", "severity": "warning", "target": "R1", "kind": "row", "rationale": "Date needs review.", "status": "open", "action": "Inspect the source", "sources": [citation]}]},
            "A2": {"id": "A2", "template": "deadline-tracker", "rows": [{"secret": "not an audit row"}]}}}}
    documents = [{"id": "email", "file": "email.txt", "state": "ready", "sha12": "0" * 12}]
    return matter, run, documents


class MatterQueryTests(unittest.TestCase):
    def setUp(self):
        self.matter, self.run, self.documents = fixture()
        self.snapshot = query.build_snapshot(self.matter, self.run, self.documents)

    def execute(self, sql):
        return query.query_snapshot(self.snapshot, sql)

    def test_projects_actual_record_shapes_without_mutating_inputs(self):
        before = copy.deepcopy((self.matter, self.run, self.documents))
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        self.assertEqual(before, (self.matter, self.run, self.documents))
        self.assertEqual(snapshot["counts"], {"documents": 1, "claims": 2, "citations": 3, "requests": 2, "review_rows": 1, "review_defects": 1})
        result = query.query_snapshot(snapshot, "SELECT assertion, assertion_version, structural_outcome, semantic_outcome, review_decision FROM review_rows")
        self.assertEqual(result["rows"], [["Edited review assertion.", 2, "passed", "unavailable", "pending"]])
        self.assertNotIn("not an audit row", json.dumps(snapshot))

    def test_all_presets_execute_and_only_return_real_selected_handles(self):
        for preset in query.PRESETS.values():
            with self.subTest(preset=preset["label"]):
                result = self.execute(preset["sql"])
                returned = {cell for row in result["rows"] for cell in row if isinstance(cell, str)}
                for handle in result["source_handles"]:
                    self.assertIn(handle["handle"], returned)
                    self.assertEqual((handle["matter_id"], handle["run_id"], handle["document_id"]), (MATTER, RUN, "email"))
        result = self.execute(query.PRESETS["open-requests"]["sql"])
        self.assertEqual(result["rows"][0][0], "R1")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(self.execute("SELECT count(*) FROM claims")["source_handles"], [])

    def test_useful_select_join_aggregate_and_cte(self):
        result = self.execute("SELECT c.claim_id, d.file, count(x.citation_id) FROM claims c JOIN documents d ON c.matter_id=d.matter_id AND c.run_id=d.run_id JOIN citations x ON x.document_id=d.document_id GROUP BY c.claim_id,d.file ORDER BY c.claim_id")
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(self.execute("WITH chosen AS (SELECT text FROM claims WHERE origin='recorded_fact') SELECT upper(text) FROM chosen")["rows"], [["NOTICE ARRIVED FRIDAY."]])

    def test_sql_injection_in_record_is_inert(self):
        self.run["case"]["facts"][0]["claim"] = "'); ATTACH DATABASE '/etc/passwd' AS evil; --"
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        result = query.query_snapshot(snapshot, "SELECT text FROM claims WHERE origin='recorded_fact'")
        self.assertEqual(result["rows"][0][0], self.run["case"]["facts"][0]["claim"])

    def test_rejects_external_system_mutation_and_multistatement_sql(self):
        statements = ["ATTACH DATABASE '/tmp/matter-query-should-never-exist' AS other", "DETACH DATABASE main", "PRAGMA table_info(documents)",
            "CREATE TABLE stolen (x)", "DELETE FROM claims", "UPDATE claims SET text='forged'", "INSERT INTO claims DEFAULT VALUES", "DROP TABLE claims", "VACUUM INTO '/tmp/matter-query-should-never-exist'", "BEGIN", "EXPLAIN SELECT * FROM claims",
            "SELECT * FROM sqlite_master", "SELECT * FROM main.sqlite_schema", "SELECT * FROM pragma_table_info('documents')", "SELECT * FROM dbstat", "SELECT load_extension('/tmp/none')", "SELECT readfile('/etc/passwd')", "SELECT writefile('/tmp/none','x')", "SELECT sqlite_version()", "SELECT randomblob(99999999)", "SELECT zeroblob(99999999)", "SELECT printf('%99999999s','x')", "SELECT 1; SELECT 2", "SELECT 1; DELETE FROM claims", "WITH x AS (SELECT 1) DELETE FROM claims", "SELECT * FROM other.claims"]
        for sql in statements:
            with self.subTest(sql=sql):
                with self.assertRaises(query.QueryError) as error:
                    self.execute(sql)
                self.assertNotIn(sql, str(error.exception))
        self.assertEqual(self.execute("SELECT count(*) FROM claims")["rows"], [[2]])

    def test_recursive_and_cartesian_computation_are_bounded(self):
        with self.assertRaises(query.QueryError):
            self.execute("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x")
        self.run["case"]["facts"] = [{**self.run["case"]["facts"][0], "id": f"F{i}"} for i in range(100)]
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        with self.assertRaises(query.QueryError):
            query.query_snapshot(snapshot, "SELECT count(*) FROM claims a CROSS JOIN claims b CROSS JOIN claims c CROSS JOIN claims d")

    def test_row_cell_and_total_output_limits_report_truncation(self):
        self.run["case"]["facts"] = [{**self.run["case"]["facts"][0], "id": f"F{i}", "claim": "A" * 6000} for i in range(220)]
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        rows = query.query_snapshot(snapshot, "SELECT claim_id FROM claims")
        self.assertEqual(rows["row_count"], query.MAX_ROWS)
        self.assertTrue(rows["truncated"])
        result = query.query_snapshot(snapshot, "SELECT text FROM claims")
        self.assertTrue(result["truncated"])
        self.assertGreater(result["cells_truncated"], 0)
        self.assertLessEqual(len(json.dumps(result).encode()), query.MAX_OUTPUT_BYTES)
        self.assertTrue(all(len(row[0].encode()) <= query.MAX_CELL_BYTES for row in result["rows"]))

    def test_scope_checks_prevent_cross_matter_and_run_reuse(self):
        for change in (lambda: self.run.update(id="run-other"), lambda: self.run.update(matter_id=OTHER),
                       lambda: self.run["case"].update(run_id="run-other")):
            self.matter, self.run, self.documents = fixture()
            change()
            with self.assertRaises(query.QueryError):
                query.build_snapshot(self.matter, self.run, self.documents)
        self.snapshot["tables"]["claims"][0]["matter_id"] = OTHER
        with self.assertRaises(query.QueryError):
            self.execute("SELECT text FROM claims")

    def test_foreign_artifact_is_omitted_without_blocking_scoped_documents(self):
        self.run["case"]["artifacts"]["A1"]["matter"] = "run-other"
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        self.assertEqual(snapshot["counts"]["documents"], 1)
        self.assertEqual(snapshot["counts"]["review_rows"], 0)
        self.assertEqual(snapshot["counts"]["review_defects"], 0)
        self.assertNotIn("Edited review assertion.", json.dumps(snapshot))

    def test_receipts_access_revocation_and_quarantine_filter_records(self):
        self.matter["documents"].extend([{"id": "private", "file": "private.txt"}, {"id": "sealed", "file": "sealed.txt", "state": "quarantined"}])
        self.documents.extend([{"id": "private", "accessible": False}, {"id": "sealed", "quarantined": True}])
        restricted = {"document_id": "private", "page": 1, "quote": "secret quote"}
        self.run["case"]["facts"].append({"id": "F2", "claim": "SECRET CLAIM", **restricted})
        self.run["case"]["memo"]["findings"].append({"summary": "SECRET SUMMARY", "citations": [restricted]})
        self.run["case"]["artifacts"]["A1"]["rows"].append({"row_id": "R2", "assertion": "SECRET REVIEW", "citations": [restricted]})
        self.run["case"]["artifacts"]["A1"]["defects"].append({"id": "D2", "rationale": "SECRET DEFECT", "sources": [restricted]})
        self.documents.append({"id": "alien", "file": "alien.txt"})
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        serialized = json.dumps(snapshot)
        for value in ("SECRET", "private.txt", "sealed.txt", "alien.txt"):
            self.assertNotIn(value, serialized)
        self.run["case"]["flags"] = [{"kind": "privileged", "document_id": "email"}]
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        self.assertEqual(snapshot["counts"]["documents"], 0)
        self.assertEqual(snapshot["counts"]["claims"], 0)
        self.assertEqual(snapshot["source_handles"], {})

    def test_excluded_source_omits_untraceable_free_text_and_requests(self):
        self.run["case"]["memo"]["findings"].append({"summary": "Untraceable memo text"})
        instance = self.run["case"]["artifacts"]["A1"]
        instance["rows"].append({"row_id": "RC", "assertion": "Untraceable review text", "citations": []})
        instance["defects"].append({"id": "D2", "kind": "memo", "rationale": "Untraceable defect text", "sources": []})
        self.assertEqual(query.build_snapshot(self.matter, self.run, self.documents)["counts"]["requests"], 2)
        self.matter["documents"].append({"id": "private", "file": "private.txt"})
        self.documents.append({"id": "private", "accessible": False})
        snapshot = query.build_snapshot(self.matter, self.run, self.documents)
        self.assertEqual(snapshot["counts"]["requests"], 0)
        text = json.dumps(snapshot)
        self.assertNotIn("Untraceable", text)
        self.assertNotIn("Original delivery receipt", text)
        self.assertEqual(snapshot["counts"]["claims"], 2)

    def test_missing_run_never_projects_unsupplied_records(self):
        snapshot = query.build_snapshot(self.matter, None, self.documents)
        self.assertEqual(snapshot["counts"]["documents"], 1)
        self.assertTrue(all(count == 0 for table, count in snapshot["counts"].items() if table != "documents"))

    def test_digest_is_deterministic_changes_with_content_and_empty_scope(self):
        self.assertEqual(self.snapshot["id"], query.build_snapshot(*fixture())["id"])
        self.run["case"]["facts"][0]["claim"] = "A changed assertion"
        self.assertNotEqual(self.snapshot["id"], query.build_snapshot(self.matter, self.run, self.documents)["id"])
        first = query.build_snapshot({"id": MATTER}, None, [])
        second = query.build_snapshot({"id": OTHER}, None, [])
        self.assertNotEqual(first["id"], second["id"])

    def test_input_limits_and_malformed_snapshot_fail_closed(self):
        with self.assertRaises(query.QueryError):
            self.execute("SELECT '" + "x" * query.MAX_SQL_BYTES + "'")
        self.run["case"]["facts"][0]["claim"] = "x" * (query.MAX_INPUT_CELL_BYTES + 1)
        with self.assertRaises(query.QueryError):
            query.build_snapshot(self.matter, self.run, self.documents)
        self.snapshot["tables"]["documents"] = [{}] * (query.MAX_TABLE_RECORDS + 1)
        with self.assertRaises(query.QueryError):
            self.execute("SELECT 1")

    def test_progress_clock_budget_is_enforced(self):
        with patch.object(query.time, "monotonic", side_effect=[1, 100]):
            with self.assertRaises(query.QueryError):
                self.execute("SELECT * FROM documents")


if __name__ == "__main__":
    unittest.main()
