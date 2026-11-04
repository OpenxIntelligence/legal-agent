"""Bounded parallel workers: leases, reservations, one commit authority.

One coordinator thread owns every case mutation; workers run read-only
jobs from pinned input versions and return proposed results. The
coordinator validates each result (lease fencing, TTL, watched
versions, quote fidelity), merges deterministically, commits worker
attempts, and settles usage — including late, cancelled, and failed
work. Worker threads are daemonic: they can never block process exit.

The loop stays single-threaded; only this module (behind one tool
call) fans out, and only the coordinator thread touches the case
record, so no case lock is needed. The usage ledger and document
cache are already safe for concurrent writers.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import document_text, scheduler, usage
from .case_record import CaseError, CaseRecord, LeaseError, StaleLease
from .tools.analyze_document import MAX_DOCS_PER_CALL, analyze, classify_read_error

#: How long a worker lease lasts without renewal. Jobs are synchronous
#: within one tool call; the TTL bounds crash recovery, not live work.
LEASE_TTL = 300.0

#: Token reservation per document a sweep worker reads. Calibrated
#: against scripted usage (~8k/doc uncached); real calls vary, and an
#: overrun keeps its work while being flagged, never silently dropped.
SWEEP_TOKENS_PER_DOC = 10000

#: Input versions a sweep result must still match at merge time.
SWEEP_WATCHES = ("evidence", "checklist")


class Cancelled(Exception):
    """A worker stopped at a cancellation checkpoint."""


class WorkerContext:
    """What one sweep worker gets. Read-only: workers never hold the case record."""

    def __init__(
        self,
        *,
        worker: str,
        task_id: str,
        issue: str,
        question: str,
        document_ids: list,
        client: Any,
        root: Path,
        run_id: str,
        cancel: threading.Event,
        reservation: int,
    ) -> None:
        self.worker = worker
        self.task_id = task_id
        self.issue = issue
        self.question = question
        self.document_ids = list(document_ids)
        self.client = client
        self.root = root
        self.run_id = run_id
        self.purpose = f"sweep:{worker}"
        self.cancel = cancel
        self.reservation = reservation

    def checkpoint(self) -> None:
        """Raise Cancelled if the coordinator stopped this job."""
        if self.cancel.is_set():
            raise Cancelled(f"{self.worker} cancelled")


def sweep_job(wctx: WorkerContext) -> dict:
    """Read one issue's documents; propose verified-quote evidence.

    Returns {"evidence": [...], "read": {"succeeded": [...], "failed": {...}}}.
    Evidence quotes were found on their pages; the coordinator
    re-verifies before merging.
    """
    wctx.checkpoint()
    result = analyze(
        wctx.client,
        wctx.root,
        wctx.document_ids,
        wctx.question,
        run_id=wctx.run_id,
        purpose_prefix=f"{wctx.purpose}:",
    )
    evidence, succeeded, failed = [], [], {}
    for entry in result.get("documents", []):
        doc_id = entry.get("document_id")
        if not doc_id:
            continue
        note = str(entry.get("note", ""))
        if note.startswith("error"):
            failed[doc_id] = {
                "note": note,
                "class": classify_read_error(entry.get("error_kind", "")),
            }
            continue
        succeeded.append(doc_id)
        for item in entry.get("evidence", []):
            if not isinstance(item, dict):
                continue
            evidence.append(
                {
                    "document_id": doc_id,
                    "page": item.get("page"),
                    "quote": str(item.get("quote", "")),
                    "claim": str(item.get("claim", "")),
                    "interpretation": str(item.get("interpretation", "")),
                }
            )
    return {"evidence": evidence, "read": {"succeeded": succeeded, "failed": failed}}


class SweepPool:
    """Run one bounded batch of sweep workers; merge their results."""

    def __init__(
        self,
        case: CaseRecord,
        client: Any,
        *,
        clock: Callable[[], float] = time.time,
        max_workers: int = 3,
    ) -> None:
        self.case = case
        self.client = client
        self.root = Path(case.root)
        self.run_id = case.data["run_id"]
        self.clock = clock
        self.max_workers = max_workers
        self._late: list[dict] = []

    def sweep(
        self,
        assignments: list,
        *,
        timeout: float | None = None,
        job_fn: Callable[[WorkerContext], dict] = sweep_job,
    ) -> dict:
        """Lease, run, and merge one worker per assignment.

        assignments: [{issue, document_ids, reservation?}]. Raises
        nothing for worker failures: every worker lands in the result
        as merged, failed, discarded, or late, with its usage settled.
        Misuse (unknown issue, over-cap batches, unaffordable budget,
        busy tasks) raises before anything is leased or spent.
        """
        self.case = CaseRecord(self.root, self.run_id)
        self._late = []
        jobs = self._admit(assignments)
        self._acquire_all(jobs)
        before = {job["worker"]: self._usage(job["worker"]) for job in jobs}
        cancel = threading.Event()
        slots: list = [None] * len(jobs)
        threads = []
        for index, job in enumerate(jobs):
            thread = threading.Thread(
                target=self._run_job,
                args=(job, cancel, slots, index, job_fn),
                name=f"sweep-{job['worker']}",
                daemon=True,
            )
            threads.append(thread)
            thread.start()
        self._join(threads, timeout)
        if any(thread.is_alive() for thread in threads):
            cancel.set()
        self.case = CaseRecord(self.root, self.run_id)
        return self._merge(jobs, slots, threads, before)

    def collect_late(self, timeout: float | None = None) -> list:
        """Join workers still flying past the sweep timeout; settle their usage.

        Late results stay discarded (their leases are gone); their
        ledgered usage is attributed here so none is lost.
        """
        settled = []
        for record in self._late:
            record["thread"].join(timeout)
            if record["thread"].is_alive():
                continue
            settled.append(
                {
                    "worker": record["worker"],
                    "task": record["task_id"],
                    "finished": True,
                    "usage": self._usage(record["worker"]),
                }
            )
        self._late = [r for r in self._late if r["thread"].is_alive()]
        return settled

    # ---- admission (all-or-nothing, before anything is spent) ----
    def _admit(self, assignments: list) -> list:
        if not isinstance(assignments, list) or not assignments:
            raise LeaseError("sweep needs a non-empty list of assignments")
        if len(assignments) > self.max_workers:
            raise LeaseError(
                f"at most {self.max_workers} sweep workers per call; "
                f"got {len(assignments)}"
            )
        seen = set()
        normalized = []
        for index, raw in enumerate(assignments):
            if not isinstance(raw, dict):
                raise LeaseError("each sweep assignment must be an object")
            issue = str(raw.get("issue", ""))
            docs = raw.get("document_ids") or []
            docs = [docs] if isinstance(docs, str) else [str(d) for d in docs]
            if not issue or issue in seen:
                raise LeaseError(f"assignment {index}: issue must be named once")
            seen.add(issue)
            if not docs:
                raise LeaseError(f"assignment {index}: document_ids is empty")
            if len(docs) > MAX_DOCS_PER_CALL:
                raise LeaseError(
                    f"assignment {index}: at most {MAX_DOCS_PER_CALL} "
                    "documents per worker"
                )
            for doc_id in docs:
                document_text.get_document(self.root, doc_id)
            flagged = [d for d in docs if self.case.flagged(d)]
            if flagged:
                raise LeaseError(
                    f"assignment {index}: confidential documents cannot be read: "
                    + ", ".join(flagged)
                )
            tasks = [
                task
                for task in self.case.data.get("tasks", {}).values()
                if isinstance(task, dict)
                and task.get("kind") == "issue"
                and issue in (task.get("issues") or [])
                and task.get("status") != "withdrawn"
            ]
            if not tasks:
                raise LeaseError(f"assignment {index}: no task for issue {issue!r}")
            task = tasks[0]
            if task.get("status") not in ("open", "stale"):
                raise LeaseError(
                    f"assignment {index}: task {task['id']} is "
                    f"{task.get('status')}: cannot sweep it"
                )
            try:
                reservation = int(
                    raw.get("reservation", SWEEP_TOKENS_PER_DOC * len(docs))
                )
            except (TypeError, ValueError):
                raise LeaseError(
                    f"assignment {index}: reservation must be an integer"
                ) from None
            entry = self.case.data.get("checklist", {}).get(issue, {})
            normalized.append(
                {
                    "worker": f"W{index + 1}",
                    "issue": issue,
                    "task_id": task["id"],
                    "question": (
                        f"Issue: {entry.get('question', issue)} "
                        f"Evidence needed: {entry.get('evidence_needed', '')}"
                    ),
                    "document_ids": docs,
                    "reservation": reservation,
                }
            )
        state = scheduler.budget_state(self.root, self.run_id)
        if state["enforced"]:
            total = sum(job["reservation"] for job in normalized)
            if total > (state["remaining_for_work"] or 0):
                raise LeaseError(
                    f"sweep needs {total} reserved tokens but only "
                    f"{state['remaining_for_work']} remain for work"
                )
        return normalized

    def _acquire_all(self, jobs: list) -> None:
        acquired: list = []
        try:
            for job in jobs:
                lease = self.case.acquire_lease(
                    job["task_id"],
                    job["worker"],
                    LEASE_TTL,
                    job["reservation"],
                    self.clock(),
                    self.case.input_versions(),
                )
                job["token"] = lease["token"]
                job["versions"] = lease["versions"]
                acquired.append(job)
        except LeaseError:
            for job in acquired:
                try:
                    self.case.release_lease(job["task_id"], job["token"])
                except LeaseError:
                    pass
            raise

    # ---- execution (workers never touch the case record) ----
    def _run_job(self, job, cancel, slots, index, job_fn) -> None:
        wctx = WorkerContext(
            worker=job["worker"],
            task_id=job["task_id"],
            issue=job["issue"],
            question=job["question"],
            document_ids=job["document_ids"],
            client=self.client,
            root=self.root,
            run_id=self.run_id,
            cancel=cancel,
            reservation=job["reservation"],
        )
        try:
            slots[index] = ("ok", job_fn(wctx))
        except Cancelled as error:
            slots[index] = ("cancelled", str(error))
        except Exception as error:  # noqa: BLE001 - worker faults are data
            slots[index] = ("error", f"{type(error).__name__}: {error}")

    def _join(self, threads: list, timeout: float | None) -> None:
        if timeout is None:
            for thread in threads:
                thread.join()
            return
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    # ---- merge (deterministic order; the one commit authority) ----
    def _merge(self, jobs, slots, threads, before) -> dict:
        sweeps, dropped, read_ok, read_failed = [], [], [], {}
        for job, slot, thread in zip(jobs, slots, threads):
            merged = self._merge_one(job, slot, thread, before[job["worker"]])
            read_ok.extend(merged.pop("_read_ok"))
            read_failed.update(merged.pop("_read_failed"))
            dropped.extend(merged.pop("_dropped"))
            sweeps.append(merged)
            if thread.is_alive():
                self._late.append(
                    {
                        "worker": job["worker"],
                        "task_id": job["task_id"],
                        "thread": thread,
                    }
                )
        if read_ok:
            self.case.mark_read(sorted(set(read_ok)))
        if read_failed:
            self.case.mark_read_failed(read_failed)
        total_in = sum(s["usage"]["input_tokens"] for s in sweeps)
        total_out = sum(s["usage"]["output_tokens"] for s in sweeps)
        return {
            "sweeps": sweeps,
            "dropped": dropped,
            "late": [
                job["worker"]
                for job, thread in zip(jobs, threads)
                if thread.is_alive()
            ],
            "usage_total": {"input_tokens": total_in, "output_tokens": total_out},
        }

    def _merge_one(self, job, slot, thread, used_before) -> dict:
        worker, task_id = job["worker"], job["task_id"]
        used = self._usage(worker)
        delta = {
            "input_tokens": used["input_tokens"] - used_before["input_tokens"],
            "output_tokens": used["output_tokens"] - used_before["output_tokens"],
        }
        total = delta["input_tokens"] + delta["output_tokens"]
        overrun = total > job["reservation"]
        outcome: dict = {
            "issue": job["issue"],
            "task": task_id,
            "worker": worker,
            "status": "failed",
            "reason": "",
            "evidence": [],
            "usage": delta,
            "overrun": overrun,
            "attempt": None,
            "_read_ok": [],
            "_read_failed": {},
            "_dropped": [],
        }
        if thread.is_alive():
            self._release_best_effort(job)
            text = (
                f"{worker} timed out; result discarded; usage stays ledgered "
                f"under {self._purpose(worker)}"
            )
            self._finish(outcome, job, "failed", text, total)
            return outcome
        kind, payload = slot if slot else ("error", "worker returned nothing")
        if kind == "cancelled":
            self._release_best_effort(job)
            self._finish(outcome, job, "failed", str(payload), total)
            return outcome
        if kind == "error":
            self._release_best_effort(job)
            self._finish(
                outcome, job, "failed", f"{worker} raised: {payload}", total
            )
            return outcome
        task = self.case.task(task_id)
        row = task.get("lease")
        if not isinstance(row, dict) or row.get("token") != job["token"]:
            self._finish(
                outcome, job, "failed",
                f"{worker}'s lease was superseded mid-flight; result discarded",
                total, status="discarded",
            )
            return outcome
        if not CaseRecord.lease_live(row, self.clock()):
            self._release_best_effort(job)
            self._finish(
                outcome, job, "failed",
                f"{worker}'s lease expired mid-flight; result discarded",
                total, status="discarded",
            )
            return outcome
        self._release_best_effort(job)
        if not self._versions_current(job):
            self._finish(
                outcome, job, "failed",
                f"{worker}'s inputs changed mid-flight; result discarded",
                total, status="discarded",
            )
            return outcome
        evidence, dropped = self._verify(job, payload)
        outcome["_dropped"] = dropped
        read = payload.get("read", {}) if isinstance(payload, dict) else {}
        if isinstance(read, dict):
            succeeded = read.get("succeeded", [])
            outcome["_read_ok"] = [d for d in succeeded if isinstance(d, str)]
            failed = read.get("failed", {})
            outcome["_read_failed"] = failed if isinstance(failed, dict) else {}
        text = (
            f"{worker} read {len(job['document_ids'])} documents, "
            f"{len(evidence)} verified quotes"
        )
        if overrun:
            text += f"; overran its {job['reservation']}-token reservation"
        self._finish(outcome, job, "completed", text, total, status="merged")
        outcome["evidence"] = evidence
        return outcome

    def _release_best_effort(self, job) -> None:
        try:
            self.case.release_lease(job["task_id"], job["token"])
        except LeaseError:
            pass

    def _finish(self, outcome, job, attempt_status, text, total, status=None) -> None:
        try:
            self.case.begin_attempt(job["task_id"], job["worker"], allocated_turns=0)
            attempt = self.case.commit_attempt(
                job["task_id"],
                status=attempt_status,
                summary=text,
                turns_used=0,
                tokens_used=total,
            )
        except CaseError as error:
            outcome["reason"] = f"{text}; attempt unrecorded: {error}"
            outcome["status"] = status or "failed"
            return
        outcome["attempt"] = attempt["n"]
        outcome["reason"] = text
        outcome["status"] = status or "failed"

    def _versions_current(self, job) -> bool:
        current = self.case.input_versions()
        pinned = job["versions"]
        return all(current[key] == pinned.get(key) for key in SWEEP_WATCHES)

    def _verify(self, job, payload) -> tuple:
        evidence, dropped = [], []
        items = payload.get("evidence", []) if isinstance(payload, dict) else []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            doc_id, page, quote = (
                item.get("document_id"),
                item.get("page"),
                str(item.get("quote", "")),
            )
            if not isinstance(doc_id, str) or not isinstance(page, int):
                dropped.append(
                    {
                        "worker": job["worker"],
                        "document_id": doc_id,
                        "page": page,
                        "reason": "malformed evidence entry",
                    }
                )
                continue
            text = document_text.page_text(self.root, doc_id, page) or ""
            if not document_text.quote_on_page(quote, text):
                dropped.append(
                    {
                        "worker": job["worker"],
                        "document_id": doc_id,
                        "page": page,
                        "reason": "quote not on that page",
                    }
                )
                continue
            evidence.append(
                {
                    "document_id": doc_id,
                    "page": page,
                    "quote": quote,
                    "claims": [
                        {
                            "worker": job["worker"],
                            "claim": str(item.get("claim", "")),
                            "interpretation": str(item.get("interpretation", "")),
                        }
                    ],
                }
            )
        return self._dedupe(evidence), dropped

    def _dedupe(self, evidence: list) -> list:
        merged: dict = {}
        for item in evidence:
            key = (
                item["document_id"],
                item["page"],
                document_text.normalize(item["quote"]),
            )
            if key not in merged:
                merged[key] = dict(
                    item, workers=[item["claims"][0]["worker"]], contested_reading=False
                )
            else:
                slot = merged[key]
                worker = item["claims"][0]["worker"]
                if worker not in slot["workers"]:
                    slot["workers"].append(worker)
                slot["claims"].extend(item["claims"])
                claims = {
                    document_text.normalize(c["claim"]) for c in slot["claims"]
                }
                slot["contested_reading"] = len(claims) > 1
        return list(merged.values())

    # ---- usage (ledgered at call time under worker purposes) ----
    def _purpose(self, worker: str) -> str:
        return f"sweep:{worker}:"

    def _usage(self, worker: str) -> dict:
        by_purpose = usage.totals(self.root, self.run_id).get("by_purpose", {})
        gathered = {"input_tokens": 0, "output_tokens": 0}
        for purpose, slot in by_purpose.items():
            if not str(purpose).startswith(self._purpose(worker)):
                continue
            if not isinstance(slot, dict):
                continue
            gathered["input_tokens"] += slot.get("input_tokens", 0) or 0
            gathered["output_tokens"] += slot.get("output_tokens", 0) or 0
        return gathered
