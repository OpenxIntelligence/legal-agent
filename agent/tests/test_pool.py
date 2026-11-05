"""Increment 3: bounded parallel workers with leases and one commit authority.

Fault-injection and concurrency tests: no stale overwrite, no
duplicate accepted effect, no silent conflict, no lost usage. Worker
mechanics use deterministic stub jobs (events, never sleeps); live
sweep behavior uses the scripted model client. All fixtures synthetic.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from types import SimpleNamespace

from agent_kit import tools, usage
from agent_kit.case_record import (
    CaseError,
    CaseRecord,
    LeaseBusy,
    LeaseError,
    StaleLease,
)
from agent_kit.document_text import quote_on_page
from agent_kit.pool import SweepPool
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from tests.test_kit import call

ITEMS = [
    {"id": "alpha", "question": "Was alpha true?", "evidence_needed": "a record of alpha"},
    {"id": "beta", "question": "Was beta true?", "evidence_needed": "a record of beta"},
]


def ledgered(root, run_id, purpose, in_tokens, out_tokens=0):
    usage.record(
        root,
        run_id,
        purpose,
        SimpleNamespace(
            model="scripted",
            usage=SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens),
        ),
    )


class PoolCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("RUN_TOKEN_BUDGET", None)
        self.set_env("SYNTHESIS_RESERVE_TOKENS", None)
        self.set_env("MAX_WORKERS", None)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.now = [1000.0]
        self.clock = lambda: self.now[0]

    def set_env(self, key, value):
        sentinel = object()
        old = os.environ.get(key, sentinel)

        def restore():
            if old is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def read(self, *ids: str) -> None:
        analyze(self.fake.client, self.root, list(ids), "x")

    def fresh(self) -> CaseRecord:
        return CaseRecord(self.root, self.run_id)

    def pool(self, **kwargs) -> SweepPool:
        kwargs.setdefault("clock", self.clock)
        return SweepPool(self.fresh(), self.fake.client, **kwargs)

    def quote(self, doc: str, start: int = 0, end: int = 40) -> str:
        return support.INITIAL[doc][0][start:end]


class LeaseTest(PoolCase):
    def test_lease_lifecycle_and_fencing(self) -> None:
        self.case.set_checklist(ITEMS)
        first = self.case.acquire_lease("T1", "W1", 300, 100, self.now[0], {})
        self.assertEqual(1, first["token"])
        with self.assertRaises(LeaseBusy):
            self.case.acquire_lease("T1", "W2", 300, 100, self.now[0], {})
        with self.assertRaises(StaleLease):
            self.case.release_lease("T1", 999)
        released = self.case.release_lease("T1", 1)
        self.assertEqual("W1", released["worker"])
        self.assertIsNone(self.fresh().task("T1")["lease"])
        second = self.case.acquire_lease("T1", "W2", 300, 100, self.now[0], {})
        self.assertEqual(2, second["token"])

    def test_expired_lease_is_superseded(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.acquire_lease("T1", "W1", 10, 100, self.now[0], {})
        self.now[0] += 20.0
        second = self.case.acquire_lease("T1", "W2", 10, 100, self.now[0], {})
        self.assertEqual(2, second["token"])
        with self.assertRaises(StaleLease):
            self.case.release_lease("T1", 1)

    def test_lease_refuses_bad_tasks(self) -> None:
        self.case.set_checklist(ITEMS)
        with self.assertRaises(CaseError):
            self.case.acquire_lease("T99", "W1", 300, 100, self.now[0], {})
        self.case.open_request("alpha", "more", "need", ["d1"])
        with self.assertRaises(LeaseError):
            self.case.acquire_lease("T1", "W1", 300, 100, self.now[0], {})
        self.read("DOC-REPORT")
        self.case.record_fact("beta", "DOC-REPORT", 1, self.quote("DOC-REPORT"), "c")
        self.case.mark_answered(["beta"])
        with self.assertRaises(LeaseError):
            self.case.acquire_lease("T2", "W1", 300, 100, self.now[0], {})
        self.case.begin_attempt("T0", "loop")
        with self.assertRaises(LeaseError):
            self.case.acquire_lease("T0", "W1", 300, 100, self.now[0], {})

    def test_break_lease_override(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.acquire_lease("T1", "W1", 300, 100, self.now[0], {})
        with self.assertRaises(CaseError):
            self.case.break_lease("T1", "")
        self.case.break_lease("T1", "worker process died")
        self.assertIsNone(self.fresh().task("T1")["lease"])
        actions = [a["action"] for a in self.fresh().data["audit"]]
        self.assertIn("lease_broken", actions)

    def test_delegated_begin_under_running_ancestor(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.begin_attempt("T0", "loop")
        self.case.begin_attempt("T1", "W1")
        self.assertEqual("running", self.fresh().task("T1")["status"])
        with self.assertRaises(CaseError):
            self.case.begin_attempt("T2", "W2")


class MergeTest(PoolCase):
    def stub(self, evidence_by_worker):
        def job(wctx):
            ledgered(
                self.root, self.run_id, f"{wctx.purpose}:analyze_document", 100, 10
            )
            return {
                "evidence": evidence_by_worker.get(wctx.worker, []),
                "read": {"succeeded": list(wctx.document_ids), "failed": {}},
            }

        return job

    def test_merge_follows_assignment_order_not_completion_order(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MINUTES")
        second_done = threading.Event()

        def slow_first(wctx):
            self.assertTrue(second_done.wait(10))
            return self.stub(
                {"W1": [{"document_id": "DOC-REPORT", "page": 1,
                          "quote": self.quote("DOC-REPORT"), "claim": "a",
                          "interpretation": "i"}]}
            )(wctx)

        def fast_second(wctx):
            try:
                return self.stub(
                    {"W2": [{"document_id": "DOC-MINUTES", "page": 1,
                              "quote": self.quote("DOC-MINUTES"), "claim": "b",
                              "interpretation": "i"}]}
                )(wctx)
            finally:
                second_done.set()

        dispatched = {"n": 0}

        def dispatch(wctx):
            dispatched["n"] += 1
            if wctx.worker == "W1":
                return slow_first(wctx)
            return fast_second(wctx)

        result = self.pool().sweep(
            [
                {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
                {"issue": "beta", "document_ids": ["DOC-MINUTES"]},
            ],
            job_fn=dispatch,
        )
        self.assertEqual(2, dispatched["n"])
        self.assertEqual(["alpha", "beta"], [s["issue"] for s in result["sweeps"]])
        self.assertEqual(["merged", "merged"], [s["status"] for s in result["sweeps"]])
        self.assertEqual(
            "DOC-REPORT", result["sweeps"][0]["evidence"][0]["document_id"]
        )

    def test_duplicate_quote_merges_once_with_conflict_flagged(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        quote = self.quote("DOC-REPORT")
        job = self.stub(
            {
                "W1": [
                    {"document_id": "DOC-REPORT", "page": 1, "quote": quote,
                     "claim": "reading one", "interpretation": "i1"},
                    {"document_id": "DOC-REPORT", "page": 1, "quote": quote,
                     "claim": "reading two", "interpretation": "i2"},
                ]
            }
        )
        result = self.pool().sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}], job_fn=job
        )
        (entry,) = result["sweeps"][0]["evidence"]
        self.assertEqual(["W1"], entry["workers"])
        self.assertTrue(entry["contested_reading"])
        self.assertEqual(2, len(entry["claims"]))

    def test_invented_quote_dropped_loudly(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        job = self.stub(
            {
                "W1": [
                    {"document_id": "DOC-REPORT", "page": 1,
                     "quote": "words that appear nowhere", "claim": "x",
                     "interpretation": "y"},
                    {"document_id": "DOC-REPORT", "page": "nope",
                     "quote": "x", "claim": "x", "interpretation": "y"},
                ]
            }
        )
        result = self.pool().sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}], job_fn=job
        )
        self.assertEqual([], result["sweeps"][0]["evidence"])
        self.assertEqual("merged", result["sweeps"][0]["status"])
        reasons = sorted(d["reason"] for d in result["dropped"])
        self.assertEqual(
            ["malformed evidence entry", "quote not on that page"], reasons
        )

    def test_stale_inputs_discarded_without_overwrite(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MINUTES")
        quote = self.quote("DOC-MINUTES")

        def concurrent_writer(wctx):
            other = CaseRecord(self.root, self.run_id)
            other.record_fact("beta", "DOC-MINUTES", 1, quote, "c")
            return self.stub(
                {"W1": [{"document_id": "DOC-MINUTES", "page": 1,
                          "quote": quote, "claim": "c", "interpretation": "i"}]}
            )(wctx)

        result = self.pool().sweep(
            [{"issue": "beta", "document_ids": ["DOC-MINUTES"]}],
            job_fn=concurrent_writer,
        )
        sweep = result["sweeps"][0]
        self.assertEqual("discarded", sweep["status"])
        self.assertIn("mid-flight", sweep["reason"])
        self.assertEqual([], sweep["evidence"])
        # The concurrent write stands; only the stale read was discarded.
        self.assertEqual(1, len(self.fresh().data["facts"]))
        self.assertEqual("failed", self.fresh().task("T2")["attempts"][0]["status"])

    def test_superseded_lease_rejects_the_result(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        quote = self.quote("DOC-REPORT")

        def intruder(wctx):
            other = CaseRecord(self.root, self.run_id)
            other.break_lease(wctx.task_id, "fault injection")
            other.acquire_lease(
                wctx.task_id, "intruder", 300, 0, self.now[0], {}
            )
            return self.stub(
                {"W1": [{"document_id": "DOC-REPORT", "page": 1,
                          "quote": quote, "claim": "c", "interpretation": "i"}]}
            )(wctx)

        result = self.pool().sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}], job_fn=intruder
        )
        sweep = result["sweeps"][0]
        self.assertEqual("discarded", sweep["status"])
        self.assertIn("superseded", sweep["reason"])
        self.assertEqual([], sweep["evidence"])
        self.fresh().break_lease("T1", "test cleanup")

    def test_overrun_keeps_work_and_flags_it(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")

        def expensive(wctx):
            ledgered(
                self.root, self.run_id, f"{wctx.purpose}:analyze_document", 9000, 2000
            )
            return {"evidence": [], "read": {"succeeded": [], "failed": {}}}

        result = self.pool().sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"], "reservation": 100}],
            job_fn=expensive,
        )
        sweep = result["sweeps"][0]
        self.assertEqual("merged", sweep["status"])
        self.assertTrue(sweep["overrun"])
        self.assertIn("overran", sweep["reason"])
        self.assertEqual(11000, sweep["usage"]["input_tokens"] + sweep["usage"]["output_tokens"])

    def test_worker_error_does_not_sink_siblings(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MINUTES")

        def flaky(wctx):
            if wctx.worker == "W1":
                raise ValueError("boom")
            return self.stub({})(wctx)

        result = self.pool().sweep(
            [
                {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
                {"issue": "beta", "document_ids": ["DOC-MINUTES"]},
            ],
            job_fn=flaky,
        )
        self.assertEqual("failed", result["sweeps"][0]["status"])
        self.assertIn("boom", result["sweeps"][0]["reason"])
        self.assertEqual("merged", result["sweeps"][1]["status"])
        self.assertEqual(
            "failed", self.fresh().task("T1")["attempts"][0]["status"]
        )

    def test_timeout_discards_once_and_settles_late_usage(self) -> None:
        self.case.set_checklist(ITEMS)
        gate = threading.Event()

        def blocked(wctx):
            self.assertTrue(gate.wait(10))
            ledgered(
                self.root, self.run_id, f"{wctx.purpose}:analyze_document", 500, 50
            )
            return {"evidence": [], "read": {"succeeded": [], "failed": {}}}

        pool = self.pool()
        result = pool.sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}],
            timeout=0,
            job_fn=blocked,
        )
        self.assertEqual(["W1"], result["late"])
        self.assertEqual("failed", result["sweeps"][0]["status"])
        self.assertIn("timed out", result["sweeps"][0]["reason"])
        gate.set()
        late = pool.collect_late(timeout=10)
        self.assertEqual(1, len(late))
        self.assertTrue(late[0]["finished"])
        self.assertEqual(550, late[0]["usage"]["input_tokens"] + late[0]["usage"]["output_tokens"])
        # Exactly one attempt: the late finish never recommits.
        self.assertEqual(1, len(self.fresh().task("T1")["attempts"]))

    def test_cooperative_cancel(self) -> None:
        self.case.set_checklist(ITEMS)

        def quitter(wctx):
            wctx.cancel.set()
            wctx.checkpoint()
            raise AssertionError("unreachable")

        result = self.pool().sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}], job_fn=quitter
        )
        self.assertEqual("failed", result["sweeps"][0]["status"])
        self.assertIn("cancelled", result["sweeps"][0]["reason"])

    def test_budget_admission_refuses_without_spending(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "1000")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "100")
        self.case.set_checklist(ITEMS)
        with self.assertRaises(LeaseError):
            self.pool().sweep([{"issue": "alpha", "document_ids": ["DOC-REPORT"]}])
        tasks = self.fresh().data["tasks"]
        self.assertIsNone(tasks["T1"].get("lease"))
        self.assertEqual([], tasks["T1"]["attempts"])
        purposes = usage.totals(self.root, self.run_id).get("by_purpose", {})
        self.assertFalse([p for p in purposes if str(p).startswith("sweep:")])

    def test_worker_cap_refuses_the_whole_call(self) -> None:
        self.case.set_checklist(ITEMS)
        with self.assertRaises(LeaseError):
            self.pool(max_workers=1).sweep(
                [
                    {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
                    {"issue": "beta", "document_ids": ["DOC-MINUTES"]},
                ]
            )
        self.assertIsNone(self.fresh().task("T1").get("lease"))

    def test_attempts_carry_explicit_worker_usage(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT", "DOC-MINUTES")
        result = self.pool().sweep(
            [
                {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
                {"issue": "beta", "document_ids": ["DOC-MINUTES"]},
            ],
            job_fn=self.stub({}),
        )
        for task_id, sweep in (("T1", result["sweeps"][0]), ("T2", result["sweeps"][1])):
            (attempt,) = self.fresh().task(task_id)["attempts"]
            self.assertEqual("completed", attempt["status"])
            self.assertEqual(0, attempt["turns_used"])
            self.assertEqual(
                sweep["usage"]["input_tokens"] + sweep["usage"]["output_tokens"],
                attempt["tokens_used"],
            )
            self.assertEqual(110, attempt["tokens_used"])


class DelegatedScopeTest(PoolCase):
    def test_out_of_assignment_quotes_and_receipts_cannot_be_accepted(self):
        self.case.set_checklist(ITEMS)
        self.read('DOC-REPORT', 'DOC-MINUTES')
        # Reading in a different operation does not authorize this worker's scope.
        def foreign_result(wctx):
            return {'evidence': [{'document_id': 'DOC-MINUTES', 'page': 1,
                                  'quote': self.quote('DOC-MINUTES'), 'claim': 'Foreign'}],
                    'read': {'succeeded': ['DOC-MINUTES'], 'failed': {'DOC-REF': 'fabricated failure'}}}
        before = self.fresh().data['failed_reads'].copy()
        result = self.pool().sweep([{'issue': 'alpha', 'document_ids': ['DOC-REPORT']}], job_fn=foreign_result)
        self.assertEqual([], result['sweeps'][0]['evidence'])
        self.assertEqual(3, len(result['dropped']))
        self.assertEqual(before, self.fresh().data['failed_reads'])
        self.assertTrue(all('permitted assignment' in row['reason'] for row in result['dropped']))

    def test_invalid_reservation_cannot_bypass_admission_or_start_workers(self):
        self.case.set_checklist(ITEMS)
        called = []
        for reservation in (-100, 0, True, 1.5, "100", None):
            with self.subTest(reservation=reservation):
                with self.assertRaisesRegex(LeaseError, "positive integer"):
                    self.pool().sweep(
                        [{"issue": "alpha", "document_ids": ["DOC-REPORT"],
                          "reservation": reservation}],
                        job_fn=lambda context: called.append(context),
                    )
        self.assertEqual([], called)
        self.assertEqual([], self.fresh().task("T1")["attempts"])

    def test_contradictory_read_receipts_fail(self):
        self.case.set_checklist(ITEMS)
        result = self.pool().sweep(
            [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}],
            job_fn=lambda context: {"evidence": [], "read": {
                "succeeded": ["DOC-REPORT"], "failed": {"DOC-REPORT": "failure"}}},
        )
        self.assertEqual("failed", result["sweeps"][0]["status"])
        self.assertIn("contradictory", result["sweeps"][0]["reason"])
        self.assertNotIn("DOC-REPORT", self.fresh().data["read_ids"])

    def test_malformed_worker_results_fail_without_clean_completion(self):
        self.case.set_checklist(ITEMS)
        for payload in (None, [], {'evidence': [], 'read': {'succeeded': 'DOC-REPORT'}},
                        {'evidence': 'quotes', 'read': {}}):
            with self.subTest(payload=payload):
                result = self.pool().sweep([{'issue': 'alpha', 'document_ids': ['DOC-REPORT']}],
                                           job_fn=lambda context: payload)
                self.assertEqual('failed', result['sweeps'][0]['status'])
                self.assertIn('malformed', result['sweeps'][0]['reason'])
                self.assertNotIn('DOC-REPORT', self.fresh().data['read_ids'])


class SweepToolTest(PoolCase):
    def sweep(self, assignments):
        return tools.run(
            self.fake.client, self.root, self.run_id,
            call("sweep_issues", {"sweeps": assignments}),
        )

    def test_sweep_tool_end_to_end(self) -> None:
        self.case.set_checklist(ITEMS)
        result = self.sweep(
            [
                {"issue": "alpha", "document_ids": ["DOC-REPORT", "DOC-MINUTES"]},
                {"issue": "beta", "document_ids": ["DOC-REF"]},
            ]
        )
        self.assertEqual(["merged", "merged"], [s["status"] for s in result["sweeps"]])
        first = result["sweeps"][0]["evidence"][0]
        self.assertTrue(
            quote_on_page(first["quote"], support.INITIAL[first["document_id"]][0])
        )
        self.assertEqual(1, len(self.fresh().task("T1")["attempts"]))
        self.assertEqual(
            {"DOC-REF", "DOC-REPORT", "DOC-MINUTES"},
            set(self.fresh().data["read_ids"]),
        )
        purposes = usage.totals(self.root, self.run_id).get("by_purpose", {})
        self.assertTrue(any(str(p).startswith("sweep:W1:") for p in purposes))
        total = result["usage_total"]
        self.assertGreater(total["input_tokens"] + total["output_tokens"], 0)
        # Proposals only: nothing recorded as fact.
        self.assertEqual([], self.fresh().data["facts"])

    def test_overlapping_docs_merge_per_issue(self) -> None:
        self.case.set_checklist(ITEMS)
        result = self.sweep(
            [
                {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
                {"issue": "beta", "document_ids": ["DOC-REPORT"]},
            ]
        )
        self.assertEqual(["merged", "merged"], [s["status"] for s in result["sweeps"]])
        self.assertTrue(result["sweeps"][0]["evidence"])
        self.assertTrue(result["sweeps"][1]["evidence"])
        cached = list((self.root / "workspace/cache/text").glob("*.json"))
        for path in cached:
            json.loads(path.read_text())

    def test_sweep_validation(self) -> None:
        self.case.set_checklist(ITEMS)
        self.assertIn(
            "no task",
            self.sweep([{"issue": "nope", "document_ids": ["DOC-REPORT"]}])["error"],
        )
        self.assertIn(
            "not in the inbox",
            self.sweep([{"issue": "alpha", "document_ids": ["DOC-NOPE"]}])["error"],
        )
        self.assertIn(
            "document_ids is empty", self.sweep([{"issue": "alpha", "document_ids": []}])["error"]
        )
        five = ["DOC-REPORT"] * 5
        self.assertIn(
            "at most 4", self.sweep([{"issue": "alpha", "document_ids": five}])["error"]
        )
        dup = [
            {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
            {"issue": "alpha", "document_ids": ["DOC-MINUTES"]},
        ]
        self.assertIn("named once", self.sweep(dup)["error"])
        self.case.open_request("alpha", "more", "need", ["d1"])
        self.assertIn(
            "blocked", self.sweep([{"issue": "alpha", "document_ids": ["DOC-REPORT"]}])["error"]
        )
        self.set_env("MAX_WORKERS", "1")
        two = [
            {"issue": "alpha", "document_ids": ["DOC-REPORT"]},
            {"issue": "beta", "document_ids": ["DOC-MINUTES"]},
        ]
        self.assertIn("at most 1", self.sweep(two)["error"])
        self.set_env("MAX_WORKERS", "bogus")
        self.assertIn("MAX_WORKERS", self.sweep(two)["error"])

    def test_prompt_mentions_sweep(self) -> None:
        self.assertIn("sweep_issues", support.instructions_file())


if __name__ == "__main__":
    unittest.main()
