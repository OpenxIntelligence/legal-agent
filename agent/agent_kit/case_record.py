"""The case record: the simulated case-management system the agent acts on.

One JSON record per run at workspace/case_records/<run-id>.json, written only
through this module and append-audited. The agent changes it through tools
(set_checklist, record_fact, flag_privileged, request_evidence, submit_memo); people change
it through ./helper review (answer a request, approve or reject the memo).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import document_text
from . import redactions
from .progress import load_progress, now, tokens_spent
from .textnorm import normalize as _normalize_text
from .reasoning import (
    GATE_VERSION,
    LAW_BASES,
    QUESTION_PROMPTS,
    REQUIRED_CLASSES,
    TASK_TYPES,
    canonical_category,
    trace_fact_impact,
    validate_adversarial,
    validate_answer,
    validate_approval_decision,
    validate_approval_request,
    validate_authority,
    validate_classification,
    validate_decomposition,
    validate_element,
    validate_identification,
    validate_triage,
    validate_work_product,
    validate_workflow,
)
from .sources import QUERY_CLASSES, REGIONS, validate_provenance

ITEM_STATUSES = ("open", "answered", "escalated")
LEGACY_PROVEN = "proven"

MAX_FACTS_PER_CASE = 500
MAX_FACTS_PER_BATCH = 4
MAX_DRAFT_CHECKS = 5
MAX_CITATIONS_PER_FINDING = 3
MAX_READ_ATTEMPTS_PER_DOC = 3

FACT_STANCES = ("supports", "opposes", "context")

CLAIM_KINDS = ("fact", "legal_rule", "applicability", "inference")

# Panel lenses for adversarial review (§7): the role one challenge speaks
# from. Optional per review; coverage is advisory, never gating, so legacy
# reviews without a lens keep working unchanged.
ADVERSARIAL_LENSES = ("evidence", "law", "procedure", "strategy")

BRANCH_BASIS_KINDS = ("connecting_facts", "clause", "authority", "operator")

QUERY_OUTCOMES = ("answered", "unanswered", "blocked")

MATTER_STATUSES = ("known", "inferred", "disputed", "unknown")

TASK_STATUSES = (
    "open",
    "running",
    "blocked",
    "completed",
    "failed",
    "stale",
    "withdrawn",
)
ATTEMPT_OUTCOMES = ("completed", "partial", "blocked", "failed", "interrupted")

ROOT_TASK = "T0"

REQUEST_PURPOSES = (
    "initial",
    "follow_up",
    "corroboration",
    "contradiction",
    "suspected_discrepancy",
    "missing_primary",
    "replacement",
)

READ_FAILURE_CLASSES = (
    "transient",
    "corrupt",
    "partial",
    "restricted",
    "internal",
    "unknown",
)
# Read attempts before a replacement path opens, by failure class.
# Restricted and internal failures never resolve to re-reading.
REPLACEMENT_AFTER = {"transient": 3, "unknown": 3, "partial": 1, "corrupt": 0}

# C-0 collaboration backbone (docs/collaboration/05-data-model.md):
# composer drafts, threads, directives, context items, activity
# edges. Run-scoped keys on the case record; immutable history
# plus optimistic concurrency, no new infrastructure.
DRAFT_STATES = ("draft", "sent")
VERIFICATION_STATUSES = ("pending", "passed", "challenged", "stale")
THREAD_STATES = ("open", "resolved")
THREAD_OBJECT_TYPES = ("draft", "artifact")
DIRECTIVE_KINDS = ("redirect", "add-context", "constrain", "question")
DIRECTIVE_STATES = (
    "proposed",
    "acknowledged",
    "applied",
    "refused",
    "superseded",
)
CONTEXT_SOURCES = ("composer", "upload", "thread")
CONTEXT_STATES = ("quarantined", "admitted", "rejected")
EDGE_RELATIONS = (
    "challenged",
    "from",
    "resulted-in",
    "supports",
    "contradicts",
    "invalidated",
    "approved",
    "superseded",
)
EDGE_ENDPOINT_TYPES = (
    "draft",
    "artifact",
    "thread",
    "directive",
    "context",
    "objection",
)
PARTICIPANT_TYPES = ("human", "agent")
AGENT_ROLES = (
    "research",
    "evidence",
    "contract",
    "drafting",
    "critic",
    "specialist",
)
# Governance (09-security): least privilege by default. Humans act
# as associates unless named otherwise; admitting quarantined
# context needs attorney or admin. Agents take up and apply
# directives; they never admit context and never approve.
HUMAN_ROLES = ("client", "associate", "attorney", "admin")
ADMIT_ROLES = ("attorney", "admin")
DEFAULT_HUMAN_ROLE = "associate"
# Hardening: collaboration text is review prose, not bulk import.
MAX_COLLAB_BODY = 200_000
MAX_COLLAB_TITLE = 500


def _parse_v(version_id: str) -> int | None:
    match = re.fullmatch(r"v(\d+)", str(version_id or "").strip())
    return int(match.group(1)) if match else None


def _safe_int(value, what: str) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise CaseError(f"the stored {what} is corrupt: {value!r}")


def thread_freshness_for(data: dict, thread: dict) -> dict:
    """version_current / anchor_match for a thread (null = unknown).

    Module-level so artifact exports reuse the exact same
    computation as the interactive summary.
    """
    otype, oid = thread.get("object_type"), thread.get("object_id")
    version_id = thread.get("version_id") or ""
    anchor = thread.get("anchor") or {}
    if otype == "artifact":
        versions = (data.get("artifacts") or {}).get(oid, {}).get(
            "versions"
        ) or []
        if not version_id or not isinstance(versions, list):
            return {"version_current": None, "anchor_match": None}
        number = _parse_v(version_id)
        return {
            "version_current": number == len(versions)
            if number is not None
            else None,
            "anchor_match": None,
        }
    draft = next(
        (
            d
            for d in data.get("composer_drafts") or []
            if isinstance(d, dict) and d.get("id") == oid
        ),
        None,
    )
    if draft is None:
        return {"version_current": None, "anchor_match": None}
    current = _safe_int(draft.get("version"), "draft version")
    body = draft.get("body_md") or ""
    version_current = None
    if version_id:
        number = _parse_v(version_id)
        version_current = number == current if number is not None else None
    anchor_match = None
    start, end, quoted = (
        anchor.get("start", 0),
        anchor.get("end", 0),
        anchor.get("quoted") or "",
    )
    if quoted:
        anchor_match = (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(body)
            and body[start:end] == quoted
        )
    return {
        "version_current": version_current,
        "anchor_match": anchor_match,
    }


def collaboration_export(data: dict) -> dict | None:
    """The deliberation record for artifact exports (None when empty).

    Drafts (current bodies + history metadata), threads (full
    messages, anchors, freshness), directives, context items,
    and activity edges. Lawyer-authored like reviewer
    feedback, so it exports as-is under the same precedent.
    """
    drafts = [d for d in data.get("composer_drafts") or [] if isinstance(d, dict)]
    threads = [t for t in data.get("threads") or [] if isinstance(t, dict)]
    directives = [
        d for d in data.get("directives") or [] if isinstance(d, dict)
    ]
    context = [c for c in data.get("context_items") or [] if isinstance(c, dict)]
    edges = [e for e in data.get("activity_edges") or [] if isinstance(e, dict)]
    if not (drafts or threads or directives or context or edges):
        return None
    return {
        "drafts": [
            {
                "id": d.get("id"),
                "title": d.get("title", ""),
                "body_md": d.get("body_md", ""),
                "state": d.get("state"),
                "version": d.get("version"),
                "verification_status": d.get(
                    "verification_status", "pending"
                ),
                "author": d.get("author", {}),
                "updated_at": d.get("updated_at", ""),
                "history": [
                    {
                        "version": h.get("version"),
                        "author": h.get("author", {}),
                        "at": h.get("at", ""),
                    }
                    for h in d.get("history") or []
                    if isinstance(h, dict)
                ],
            }
            for d in drafts
        ],
        "threads": [
            {
                "id": t.get("id"),
                "object_type": t.get("object_type"),
                "object_id": t.get("object_id"),
                "version_id": t.get("version_id", ""),
                "anchor": t.get("anchor", {}),
                "state": t.get("state"),
                "messages": copy.deepcopy(t.get("messages") or []),
                "resolution": t.get("resolution", ""),
                **thread_freshness_for(data, t),
            }
            for t in threads
        ],
        "directives": [copy.deepcopy(d) for d in directives],
        "context": [copy.deepcopy(c) for c in context],
        "edges": [copy.deepcopy(e) for e in edges],
    }


def _need_pair(pair) -> list:
    """One [document_id, page] need, validated. Refuses junk loudly."""
    doc, page = None, None
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        doc, page = pair
    if not str(doc or "").strip():
        raise CaseError(
            f"need passage {pair!r} must name its document"
        )
    try:
        number = int(page)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise CaseError(
            f"need passage {pair!r} must carry an integer page"
        ) from None
    if number < 1:
        raise CaseError(
            f"need passage {pair!r} must carry a page of 1 or more"
        )
    return [str(doc), number]


def stance_of(fact: dict) -> str:
    """A fact's stance toward its item. Facts recorded before stances read as supports.

    Stances are the investigator's characterizations of collected evidence,
    not independent claim-support assessments: a supports label alone never
    says the claims check judged the claim supported. An item reads as
    answered only after a passed assessment (see effective_status).
    """
    stance = fact.get("stance", "supports") if isinstance(fact, dict) else "supports"
    return stance if stance in FACT_STANCES else "supports"


def _evidence_fingerprint(facts: list) -> str:
    """The live-evidence version a memo filing pins.

    Same shape as quality.records.evidence_fingerprint (kept local so
    the case record does not depend on the quality layer).
    """
    core = sorted(
        (
            f.get("id"),
            f.get("document_id"),
            f.get("page"),
            f.get("quote"),
            stance_of(f),
        )
        for f in facts or []
        if isinstance(f, dict)
    )
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()[:24]


def memo_fingerprint(memo: dict) -> str:
    """The draft version a draft check pins: conclusion, findings, gaps."""
    core = {
        "conclusion": (memo.get("conclusion") or ""),
        "findings": memo.get("findings") or [],
        "gaps": (memo.get("gaps") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, default=str).encode()
    ).hexdigest()
    return digest[:24]


def batch_op_id(proposal: dict) -> str:
    """Stable identity for one batch entry, derived from its normalized content.

    The same proposal always yields the same op id, so a retried batch
    can be correlated entry by entry; acceptance itself stays
    idempotent through record_fact's duplicate-evidence check.
    """
    core = {
        "item": str(proposal.get("item", "")),
        "document_id": str(proposal.get("document_id", "")),
        "page": str(proposal.get("page", "")),
        "quote": document_text.normalize(str(proposal.get("quote", ""))),
        "claim": " ".join(str(proposal.get("claim", "")).split()),
        "stance": str(proposal.get("stance", "supports")),
        "kind": str(proposal.get("kind", "fact")),
    }
    digest = hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()
    return "op-" + digest[:12]


def effective_status(entry: dict, memo_checks: list) -> str:
    """An item's case status with legacy records mapped honestly.

    Entries written before the B vocabulary carry "proven", meaning the
    investigator recorded a supports fact — not that support was
    assessed. Such an entry reads as "answered" only when a passed
    claims check exists for the run; otherwise it reads as "open",
    because its historical assessment state is unknown.
    """
    status = entry.get("status") if isinstance(entry, dict) else None
    if status in ITEM_STATUSES:
        return status
    if status == LEGACY_PROVEN and any(
        isinstance(c, dict)
        and c.get("check") == "claims"
        and c.get("outcome") == "passed"
        for c in memo_checks or []
    ):
        return "answered"
    return "open"


class CaseError(ValueError):
    """A write the case record refused. The message is safe to show the model."""


class LeaseError(CaseError):
    """A worker lease was refused, busy, or stale. Safe to show the model."""


class LeaseBusy(LeaseError):
    """The task already has a live lease held by another worker."""


class StaleLease(LeaseError):
    """The lease token is expired or superseded: the holder must stop."""


class FactLimitError(CaseError):
    """The case already has MAX_FACTS_PER_CASE live facts. Not a rejected quote."""


ITEM_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class CaseRecord:
    def __init__(self, root: Path, run_id: str) -> None:
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", run_id):
            raise CaseError("Invalid case run ID.")
        self.root = Path(root)
        self.path = self.root / "workspace/case_records" / f"{run_id}.json"
        if any(path.is_symlink() for path in (self.root / "workspace", self.path.parent, self.path)):
            raise CaseError("Invalid case record path: symbolic links are not allowed.")
        if self.path.is_file():
            self.data: dict[str, Any] = json.loads(self.path.read_text())
            if not isinstance(self.data, dict) or self.data.get("run_id") != run_id:
                raise CaseError("Case record run ID does not match its file.")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data = {
                "run_id": run_id,
                "created": now(),
                "checklist": {},
                "checklist_checks": [],
                "memo_checks": [],
                "read_ids": [],
                "failed_reads": {},
                "facts": [],
                "fact_seq": 0,
                "withdrawn_facts": [],
                "rejected_facts": [],
                "flags": [],
                "signature_checks": [],
                "requests": [],
                "memo": None,
                "memo_history": [],
                "audit": [],
                "artifacts": {},
            }
            self._save("created", "runtime", {})

    # ---- persistence ----
    def _save(self, action: str, actor: str, detail: dict) -> None:
        # Display-ready snapshots, refreshed on every mutation so the
        # console (which never imports this code) renders current
        # staleness and approval state without recomputing it.
        try:
            self.data["claims_staleness"] = self.claims_staleness()
            self.data["approval_status"] = self.approval_status()
            self.data["shared_quotes"] = self.shared_quotes()
        except Exception:
            pass
        self.data["audit"].append(
            {"at": now(), "actor": actor, "action": action, "detail": detail}
        )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)

    def _item(self, item: str) -> dict:
        if not self.data["checklist"]:
            raise CaseError("there is no checklist yet: call set_checklist first")
        if item not in self.data["checklist"]:
            raise CaseError(
                f"unknown checklist item {item!r}; valid items: {', '.join(self.data['checklist'])}"
            )
        return self.data["checklist"][item]

    def flagged(self, document_id: str) -> bool:
        return any(
            flag["document_id"] == document_id and flag["kind"] == "privileged"
            for flag in self.data["flags"]
        )

    # ---- agent writes ----
    def parse_checklist(self, items: list) -> dict:
        """Validate checklist items and return the new checklist, without saving it."""
        if not isinstance(items, list) or not items:
            raise CaseError("the checklist needs at least one item")
        new: dict[str, dict] = {}
        for raw in items:
            if not isinstance(raw, dict):
                raise CaseError("each checklist item must be an object")
            item_id = str(raw.get("id", "")).strip()
            if not ITEM_ID.match(item_id):
                raise CaseError(
                    f"item id {item_id!r} must be snake_case, e.g. ready_statement"
                )
            if item_id in new:
                raise CaseError(f"duplicate checklist item {item_id}")
            question = str(raw.get("question", "")).strip()
            evidence = str(raw.get("evidence_needed", "")).strip()
            if not question:
                raise CaseError(f"{item_id}: question is empty")
            if not evidence:
                raise CaseError(
                    f"{item_id}: say what evidence would settle it (evidence_needed)"
                )
            old = self.data["checklist"].get(item_id, {})
            amends = raw.get("amends", [])
            if not isinstance(amends, list) or any(
                not isinstance(a, str) for a in amends
            ):
                raise CaseError(f"{item_id}: amends must be a list of item ids")
            new[item_id] = {
                "question": question,
                "evidence_needed": evidence,
                "needs_signature_check": bool(raw.get("needs_signature_check")),
                "amendment": str(raw.get("amendment", "") or "").strip(),
                "amends": [a.strip() for a in amends if a.strip()],
                "status": old.get("status", "open"),
                "facts": old.get("facts", []),
                "request": old.get("request"),
            }
        dropped = [
            k
            for k, v in self.data["checklist"].items()
            if k not in new and (v["facts"] or v["request"])
        ]
        if dropped:
            raise CaseError(
                f"keep the items that already have facts or requests: {', '.join(dropped)}"
            )
        return new

    def set_checklist(self, items: list) -> dict:
        """Replace the checklist. Items that already have facts or requests must be kept.

        The first accepted checklist also captures the scope baseline:
        the assignment's obligations, frozen before investigation, that
        the scope guard holds later revisions to.
        """
        new = self.parse_checklist(items)
        if "scope_baseline" not in self.data:
            self.data["scope_baseline"] = {
                item_id: entry["question"] for item_id, entry in new.items()
            }
        checks = self.data.get("checklist_checks", [])
        first_draft = (
            set(checks[0]["draft"]) if checks and not checks[0]["passed"] else None
        )
        for item_id, entry in new.items():
            entry["added_after_check"] = (
                first_draft is not None and item_id not in first_draft
            )
        self.data["checklist"] = new
        try:
            version = int(self.data.get("checklist_version", 0)) + 1
        except (TypeError, ValueError):
            version = 1
        self.data["checklist_version"] = version
        self._save("checklist_set", "agent", {"items": list(new)})
        self.sync_issue_tasks()
        self._stale_completed_tasks(f"checklist revised to v{version}")
        return new

    def record_checklist_check(self, items: list, result: dict) -> dict:
        """Store the outcome of the ground-truth checklist check for one draft."""
        record = {
            "draft": [str(i.get("id")) for i in items if isinstance(i, dict)],
            "passed": bool(result.get("passed")),
            "score": result.get("score"),
            "feedback": result.get("feedback", ""),
            "attempt": result.get("attempt"),
            "accepted": bool(result.get("accepted", result.get("passed"))),
            "at": now(),
        }
        self.data.setdefault("checklist_checks", []).append(record)
        self._save(
            "checklist_checked",
            "verification",
            {"passed": record["passed"], "accepted": record["accepted"]},
        )
        return record

    def record_memo_check(
        self,
        check: str,
        problems: list,
        *,
        outcome: str | None = None,
        scope: dict | None = None,
        verifier: str | None = None,
        support: dict | None = None,
        units: dict | None = None,
        regressions: list | None = None,
        fact_index: dict | None = None,
        advisories: list | None = None,
    ) -> dict:
        """Store the outcome of a memo check (memo rules or claims) for one submitted memo.

        Claims checks also record their outcome (passed/failed/unavailable), the
        scope actually assessed, the verifier version, the judge's support
        refs by unit id, the per-unit verdicts, any verdict regressions
        (units supported before, unsupported now), and the fact index the
        assessment saw (for later relevance diffing). Advisories are
        non-blocking reviewer notes (e.g. contested one-sided
        assertions) carried so the filed record never shows an
        unqualified pass the gate did not mean. Records written before
        those fields existed stay readable and are never rewritten. A
        claims check that assessed (any outcome but unavailable) resets
        the facts-since-assessment nudge counter; an unavailable check
        leaves it, because the evidence is still unassessed.
        """
        record: dict = {
            "check": check,
            "passed": not problems,
            "problems": problems,
            "at": now(),
        }
        if advisories:
            record["advisories"] = [str(a) for a in advisories]
        if outcome is not None:
            record["outcome"] = outcome
        if scope is not None:
            record["scope"] = scope
        if verifier is not None:
            record["verifier"] = verifier
        if support is not None:
            record["support"] = support
        if units is not None:
            record["units"] = units
        if regressions is not None:
            record["regressions"] = regressions
        if fact_index is not None:
            record["fact_index"] = fact_index
        self.data.setdefault("memo_checks", []).append(record)
        if check == "claims" and outcome != "unavailable":
            self.data["facts_since_assessment"] = 0
        self._save(
            "memo_checked",
            "verification",
            {
                "check": check,
                "passed": not problems,
                "outcome": outcome or ("passed" if not problems else "failed"),
            },
        )
        return record

    def record_draft_check(
        self,
        *,
        check: str,
        passed: bool,
        outcome: str,
        problems: list,
        memo: dict,
        scope: dict | None = None,
        verifier: str | None = None,
        advisories: list | None = None,
    ) -> dict:
        """Store one draft (pre-filing) assessment, bound to its versions.

        A draft informs revision only: it never files, never counts as a
        refusal, never settles an item, and never feeds the memo_checks
        the filing gate, staleness, and legacy-status mapping read. Each
        record pins the draft text version and the live-evidence version
        it assessed. Only the most recent MAX_DRAFT_CHECKS records are
        kept; every check stays in the append-only audit log. A draft
        claims assessment resets the facts-since-assessment counter
        unless it was unavailable.
        """
        drafts = self.data.setdefault("draft_checks", [])
        seq = max([d.get("n", 0) for d in drafts if isinstance(d, dict)] + [0]) + 1
        record: dict = {
            "n": seq,
            "at": now(),
            "check": check,
            "passed": passed,
            "outcome": outcome,
            "problems": list(problems),
            "memo_version": memo_fingerprint(memo),
            "evidence_version": _evidence_fingerprint(self.data.get("facts", [])),
        }
        if scope is not None:
            record["scope"] = scope
        if verifier is not None:
            record["verifier"] = verifier
        if advisories:
            record["advisories"] = [str(a) for a in advisories]
        drafts.append(record)
        del drafts[:-MAX_DRAFT_CHECKS]
        if check == "claims" and outcome != "unavailable":
            self.data["facts_since_assessment"] = 0
        self._save(
            "draft_checked",
            "verification",
            {"draft": seq, "check": check, "outcome": outcome, "passed": passed},
        )
        return record

    # ---- matter context ----
    def note_context(
        self, field: str, value: str, status: str = "known", provenance: str = ""
    ) -> dict:
        """Record one matter fact: what the assignment establishes about the case.

        `field` names the fact (purpose, parties, forum, key dates, ...).
        `status` is known, inferred, disputed, or unknown: unknown fields
        carry no value and stay explicitly listed until established. Each
        note bumps the matter version; completed tasks go stale, because
        conclusions may rest on the changed context. Repeating an
        identical note is a no-op returning the existing entry.
        """
        field = str(field or "").strip()
        if not ITEM_ID.match(field):
            raise CaseError(
                f"context field {field!r} must be snake_case, e.g. governing_law"
            )
        if status not in MATTER_STATUSES:
            raise CaseError(
                f"status {status!r} must be one of: {', '.join(MATTER_STATUSES)}"
            )
        value = str(value or "")
        if status == "unknown" and value.strip():
            raise CaseError(
                "an unknown field carries no value; use inferred for a suspicion"
            )
        if status != "unknown" and not value.strip():
            raise CaseError(f"a {status} field needs a value")
        matter = self.data.setdefault("matter", {"version": 0, "fields": {}})
        fields = matter.setdefault("fields", {})
        existing = fields.get(field)
        if (
            isinstance(existing, dict)
            and existing.get("value", "") == value
            and existing.get("status") == status
            and existing.get("provenance", "") == str(provenance or "")
        ):
            return dict(existing, field=field, matter_version=matter.get("version", 0))
        matter["version"] = int(matter.get("version", 0)) + 1
        fields[field] = {
            "value": value,
            "status": status,
            "provenance": str(provenance or ""),
            "at": now(),
        }
        self._save(
            "context_noted",
            "agent",
            {
                "field": field,
                "status": status,
                "matter_version": matter["version"],
            },
        )
        self._stale_completed_tasks(
            f"matter context is now v{matter['version']}"
        )
        return dict(
            fields[field], field=field, matter_version=matter["version"]
        )

    def matter_version(self) -> int:
        """The current matter-context version (0 when nothing noted yet)."""
        try:
            return int(self.data.get("matter", {}).get("version", 0))
        except (TypeError, ValueError):
            return 0

    def matter_unknowns(self) -> list:
        """Matter fields explicitly marked unknown."""
        fields = self.data.get("matter", {}).get("fields", {})
        if not isinstance(fields, dict):
            return []
        return sorted(
            name
            for name, entry in fields.items()
            if isinstance(entry, dict) and entry.get("status") == "unknown"
        )

    # ---- first-principles reasoning ----
    def _pin_assessment_policy(self, task_type: str, basis: str) -> dict:
        """Freeze the assessment policy server-side (first pin wins).

        Records task type + gate version + required classes as one
        immutable policy. The gate enforces the pin — never the
        live label or the live class table — so mid-run edits and
        code upgrades cannot silently switch what filing requires.
        Only reclassify_task_type replaces a pin, with basis and
        dependent invalidation.
        """
        pin = self.data.get("assessment_policy")
        if isinstance(pin, dict) and pin.get("task_type"):
            return pin
        if task_type not in REQUIRED_CLASSES:
            raise CaseError(
                f"task type {task_type!r} has no required-class table"
            )
        pin = {
            "task_type": task_type,
            "gate": GATE_VERSION,
            "required_classes": list(REQUIRED_CLASSES[task_type]),
            "pinned_at": now(),
            "basis": basis,
        }
        self.data["assessment_policy"] = pin
        return pin

    def set_task_type(self, task_type: str) -> str:
        """Set the matter's task type (research, litigation, ...).

        Setting a task type engages the reasoning gate at filing:
        the run must answer the task's foundational questions,
        analyze controlling tests element by element where it relies
        on them, and record an adversarial review. The first set
        pins the assessment policy; changing the type later goes
        through reclassify_task_type with a recorded basis. Runs
        that never set one stay on the memo-rules + claims path.
        """
        task_type = str(task_type or "").strip()
        if task_type not in TASK_TYPES:
            raise CaseError(
                f"task type {task_type!r} must be one of: "
                f"{', '.join(TASK_TYPES)}"
            )
        stored = str(self.data.get("task_type", "") or "")
        if stored and stored != task_type:
            raise CaseError(
                f"task type is already {stored!r}; reclassify to "
                f"{task_type!r} with reclassify_task_type and record why"
            )
        self.data["task_type"] = task_type
        self._pin_assessment_policy(task_type, "task_type_set")
        self._save("task_type_set", "agent", {"task_type": task_type})
        return task_type

    def reclassify_task_type(self, task_type: str, basis: str) -> dict:
        """Change the task type with recorded basis + invalidation.

        Logs the from/to/basis entry, updates the type, re-pins the
        assessment policy under the current gate, and stales the
        validated decomposition (it was built for the old policy):
        the gate fails until the question set is re-validated.
        Prior approval grants are staled at the same time: a grant
        assessed under the old policy must revalidate under the
        new one (closeout §8).
        Reclassifying to the current type returns the pin unchanged
        when it is current; a drifted or malformed pin is re-pinned
        under the live gate instead, so drift stays resolvable
        without inventing a different type (closeout §7).
        """
        task_type = str(task_type or "").strip()
        if task_type not in TASK_TYPES:
            raise CaseError(
                f"task type {task_type!r} must be one of: "
                f"{', '.join(TASK_TYPES)}"
            )
        if task_type not in REQUIRED_CLASSES:
            raise CaseError(
                f"task type {task_type!r} has no required-class table"
            )
        basis = str(basis or "").strip()
        if not basis:
            raise CaseError(
                "reclassification needs a basis: why the type changes"
            )
        stored = str(self.data.get("task_type", "") or "")
        if stored == task_type:
            pin = self.data.get("assessment_policy")
            required = pin.get("required_classes") if isinstance(pin, dict) else None
            classes_valid = (
                isinstance(required, list)
                and bool(required)
                and all(c in QUESTION_PROMPTS for c in required)
            )
            if (
                isinstance(pin, dict)
                and pin.get("task_type") == task_type
                and str(pin.get("gate") or "") == GATE_VERSION
                and classes_valid
            ):
                return self._pin_assessment_policy(task_type, "reclassification")
            # Drifted or malformed: re-pin under the live gate,
            # keeping the frozen class set when it is still valid.
            if not classes_valid:
                required = list(REQUIRED_CLASSES[task_type])
                self._stale_decomposition(
                    f"pin classes unreadable, re-pinned: {basis}"
                )
            self.data["assessment_policy"] = {
                "task_type": task_type,
                "gate": GATE_VERSION,
                "required_classes": list(required),
                "pinned_at": now(),
                "basis": "reclassification",
            }
            self._stale_grants(
                stored, task_type, f"same-type re-pin: {basis}"
            )
            log = self.data.get("reclassifications")
            if not isinstance(log, list):
                log = []
                self.data["reclassifications"] = log
            log.append(
                {"from": stored, "to": task_type, "basis": basis,
                 "repin": True, "at": now()}
            )
            self._save(
                "task_reclassified",
                "agent",
                {"from": stored, "to": task_type, "basis": basis,
                 "repin": True},
            )
            return dict(self.data["assessment_policy"])
        log = self.data.get("reclassifications")
        if not isinstance(log, list):
            log = []
            self.data["reclassifications"] = log
        log.append(
            {"from": stored, "to": task_type, "basis": basis, "at": now()}
        )
        self.data["task_type"] = task_type
        classification = self.data.get("classification")
        if isinstance(classification, dict):
            classification["gate_task_type"] = task_type
        self.data["assessment_policy"] = {
            "task_type": task_type,
            "gate": GATE_VERSION,
            "required_classes": list(REQUIRED_CLASSES[task_type]),
            "pinned_at": now(),
            "basis": "reclassification",
        }
        self._stale_grants(stored, task_type, basis)
        self._stale_decomposition(
            f"task reclassified {stored!r} -> {task_type!r}: {basis}"
        )
        self._save(
            "task_reclassified",
            "agent",
            {"from": stored, "to": task_type, "basis": basis},
        )
        return dict(self.data["assessment_policy"])

    def _stale_decomposition(self, reason: str) -> None:
        """Mark the validated decomposition stale after re-pinning."""
        decompositions = self.data.get("decompositions")
        if isinstance(decompositions, list) and decompositions:
            latest = decompositions[-1]
            previous = (
                latest.get("questions", [])
                if isinstance(latest, dict)
                else []
            )
            decompositions.append(
                {
                    "verdict": "stale",
                    "stale_reason": reason,
                    "questions": previous,
                    "at": now(),
                }
            )

    def _stale_grants(self, previous: str, task_type: str, basis: str) -> None:
        """Stale prior approval grants after the policy is re-pinned.

        A grant assessed under the old task type no longer
        authorizes: approval_status reports it stale until the
        operator revalidates under the new assessment policy.
        Denials and pending requests are untouched — only live
        grants lose authority (closeout §8).
        """
        approvals = self.data.get("approvals")
        if not isinstance(approvals, list):
            return
        for appr in approvals:
            if (
                isinstance(appr, dict)
                and appr.get("decision") == "grant"
                and not appr.get("stale")
            ):
                appr["stale"] = True
                appr["stale_reason"] = (
                    f"reclassified {previous!r} -> {task_type!r} "
                    f"({basis}): revalidate this grant under the new "
                    "assessment policy"
                )

    def answer_foundational(
        self,
        *,
        qclass: str,
        question: str,
        rule_or_source: str,
        evidence: str,
        answer: str,
        basis: str,
        contrary: str,
        effect: str,
        status: str = "answered",
        resolution: str = "",
        issue: str = "",
        task_type: str = "",
        depends_on: list | None = None,
        threshold: bool = False,
    ) -> dict:
        """Answer one foundational question (§4 A–G), stored for the gate.

        `issue` optionally links the answer to the checklist item it
        supports; asserted findings must each trace to a linked
        answer. `task_type` sets the matter's type on first use.
        Unresolved answers must name their resolution path. Recording
        the exact same answer twice is idempotent. `depends_on` names
        foundational questions (FQ ids) this answer rests on, which
        must already be recorded; `threshold` marks a potentially
        dispositive question to resolve first.
        """
        entry = {
            "class": str(qclass or ""),
            "question": str(question or ""),
            "rule_or_source": str(rule_or_source or ""),
            "evidence": str(evidence or ""),
            "answer": str(answer or ""),
            "basis": str(basis or ""),
            "contrary": str(contrary or ""),
            "effect": str(effect or ""),
            "status": str(status or "answered"),
            "resolution": str(resolution or ""),
            "issue": str(issue or "").strip(),
        }
        problems = validate_answer(entry)
        if problems:
            raise CaseError("; ".join(problems))
        if depends_on is not None and (
            not isinstance(depends_on, list)
            or any(not isinstance(d, str) for d in depends_on)
        ):
            raise CaseError("depends_on must be a list of question ids")
        deps = [str(d or "") for d in (depends_on or [])]
        if any(not d for d in deps):
            raise CaseError("dependency ids must be non-empty")
        known = {
            f.get("id")
            for f in self.data.get("foundational", [])
            if isinstance(f, dict)
        }
        for dep in deps:
            if dep not in known:
                raise CaseError(
                    f"unknown foundational question {dep!r}: "
                    "record dependencies before their dependents"
                )
        entry["depends_on"] = deps
        entry["threshold"] = bool(threshold)
        if entry["issue"] and (
            not self.data.get("checklist") or entry["issue"] not in self.data["checklist"]
        ):
            raise CaseError(
                f"unknown checklist item {entry['issue']!r}: set the checklist first"
            )
        task_type = str(task_type or "").strip()
        if task_type:
            if task_type not in TASK_TYPES:
                raise CaseError(
                    f"task type {task_type!r} must be one of: "
                    f"{', '.join(TASK_TYPES)}"
                )
            stored = str(self.data.get("task_type", "") or "")
            if stored and stored != task_type:
                raise CaseError(
                    f"task type is already {stored!r}; not changing it to {task_type!r}"
                )
            self.data["task_type"] = task_type
        for existing in self.data.get("foundational", []):
            if not isinstance(existing, dict):
                continue
            # Pre-dependency records read as no dependencies, not threshold.
            normalized = dict(
                {"depends_on": [], "threshold": False}, **existing
            )
            if all(normalized.get(k) == v for k, v in entry.items()):
                return dict(existing)
        try:
            seq = int(self.data.get("foundational_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["foundational_seq"] = seq + 1
        entry = dict(
            entry, id=f"FQ{self.data['foundational_seq']}", at=now()
        )
        self.data.setdefault("foundational", []).append(entry)
        self._save(
            "question_answered",
            "agent",
            {
                "question": entry["id"],
                "class": entry["class"],
                "status": entry["status"],
            },
        )
        return entry

    def analyze_element(
        self,
        *,
        issue: str,
        test: str,
        element: str,
        rule: str,
        source: str,
        facts_evidence: str,
        application: str,
        supporting: str,
        opposing: str,
        missing: str,
        finding: str,
        effect: str,
    ) -> dict:
        """Analyze one element of a controlling test (§5), stored for the gate.

        `issue` is the checklist item the analysis supports; asserted
        legal-rule and applicability findings must each have one.
        """
        self._item(str(issue or ""))
        entry = {
            "issue": str(issue),
            "test": str(test or ""),
            "element": str(element or ""),
            "rule": str(rule or ""),
            "source": str(source or ""),
            "facts_evidence": str(facts_evidence or ""),
            "application": str(application or ""),
            "supporting": str(supporting or ""),
            "opposing": str(opposing or ""),
            "missing": str(missing or ""),
            "finding": str(finding or ""),
            "effect": str(effect or ""),
        }
        problems = validate_element(entry)
        if problems:
            raise CaseError("; ".join(problems))
        try:
            seq = int(self.data.get("element_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["element_seq"] = seq + 1
        entry = dict(entry, id=f"EL{self.data['element_seq']}", at=now())
        self.data.setdefault("elements", []).append(entry)
        self._save(
            "element_analyzed",
            "agent",
            {"analysis": entry["id"], "issue": entry["issue"]},
        )
        return entry

    def record_adversarial(
        self,
        *,
        strongest_against: str,
        fact_risks: str,
        adverse_authority: str,
        overlooked: str,
        opposing_counsel: str,
        judge_needs: str,
        alternative_theory: str,
        reversing_fact: str,
        revision_needed: bool,
        revision: str,
        dropped: list | None = None,
        lens: str = "",
    ) -> dict:
        """Challenge the preliminary conclusion before filing (§7).

        The latest review is what the gate reads; re-review after
        new evidence arrives. `revision` says what changed, or why
        no change was needed. `dropped` optionally records
        objections that were raised and dismissed, each with its
        counter-evidence, so the record shows the challenge was
        real rather than skipped. A dismissal is agent-proposed,
        never assessed: each entry carries assessment "proposed"
        plus any fact references its counter-evidence names that
        the record cannot resolve (unresolved_refs). A nonempty
        counter-evidence string records an explanation, not proof
        the objection failed. `lens` optionally names the panel
        role this challenge speaks from (see ADVERSARIAL_LENSES).
        """
        known_ids = {
            str(f.get("id") or "")
            for f in list(self.data.get("facts") or [])
            + list(self.data.get("withdrawn_facts") or [])
            if isinstance(f, dict) and str(f.get("id") or "")
        }
        entry = {
            "strongest_against": str(strongest_against or ""),
            "fact_risks": str(fact_risks or ""),
            "adverse_authority": str(adverse_authority or ""),
            "overlooked": str(overlooked or ""),
            "opposing_counsel": str(opposing_counsel or ""),
            "judge_needs": str(judge_needs or ""),
            "alternative_theory": str(alternative_theory or ""),
            "reversing_fact": str(reversing_fact or ""),
            "revision_needed": bool(revision_needed),
            "revision": str(revision or ""),
            "lens": str(lens or ""),
            "dropped": [
                {
                    "objection": str(d.get("objection", "") or ""),
                    "counter_evidence": str(
                        d.get("counter_evidence", "") or ""
                    ),
                    "assessment": "proposed",
                    "unresolved_refs": sorted(
                        {
                            ref
                            for ref in re.findall(
                                r"\bF\d+\b",
                                str(d.get("counter_evidence", "") or ""),
                            )
                            if ref not in known_ids
                        }
                    ),
                }
                for d in (dropped or [])
                if isinstance(d, dict)
            ],
        }
        problems = validate_adversarial(
            dict(
                entry,
                revision_needed=bool(revision_needed),
                dropped=dropped,
            )
        )
        if problems:
            raise CaseError("; ".join(problems))
        if entry["lens"] and entry["lens"] not in ADVERSARIAL_LENSES:
            raise CaseError(
                f"unknown review lens {entry['lens']!r}: "
                f"one of {', '.join(ADVERSARIAL_LENSES)}"
            )
        try:
            seq = int(self.data.get("adversarial_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["adversarial_seq"] = seq + 1
        entry = dict(entry, id=f"AR{self.data['adversarial_seq']}", at=now())
        self.data.setdefault("adversarial", []).append(entry)
        disproven = 0
        for item in entry["dropped"]:
            self._ledger_disprove(
                item["objection"],
                item["counter_evidence"],
                reporter=entry["id"],
            )
            disproven += 1
        self._save(
            "adversarial_reviewed",
            "agent",
            {
                "review": entry["id"],
                "revision_needed": entry["revision_needed"],
                "disproven": disproven,
            },
        )
        return entry

    def adversarial_lenses(self) -> list:
        """Distinct panel lenses that challenged this run, sorted.

        Advisory coverage: which roles spoke. Never gating, so runs
        with lens-less (legacy) reviews are unaffected.
        """
        return sorted(
            {
                review.get("lens", "")
                for review in self.data.get("adversarial", [])
                if isinstance(review, dict) and review.get("lens")
            }
        )

    def panel_status(self, required_lenses: list | None = None) -> dict:
        """Panel coverage: which required lenses spoke and which are missing.

        Advisory only, like adversarial_lenses: the coordinator reads
        this to nudge the next challenge, never to gate.
        """
        if required_lenses is None:
            required = list(ADVERSARIAL_LENSES)
        else:
            required = [str(lens) for lens in required_lenses]
            unknown = [lens for lens in required if lens not in ADVERSARIAL_LENSES]
            if unknown:
                raise CaseError(
                    f"unknown panel lens {unknown[0]!r}: "
                    f"one of {', '.join(ADVERSARIAL_LENSES)}"
                )
        covered = [lens for lens in required if lens in self.adversarial_lenses()]
        missing = [lens for lens in required if lens not in covered]
        return {"covered": covered, "missing": missing, "complete": not missing}

    # ---- objection ledger (raise -> disprove -> restore) ----
    def _find_objection(self, ref: str) -> dict | None:
        """The ledger finding for an id (OBJ1) or objection text, else None."""
        for finding in self.data.get("objections", []):
            if not isinstance(finding, dict):
                continue
            if finding.get("id") == ref:
                return finding
        key = _normalize_text(ref or "")
        if not key:
            return None
        for finding in self.data.get("objections", []):
            if isinstance(finding, dict) and finding.get("key") == key:
                return finding
        return None

    def _ledger_disprove(
        self, objection: str, counter_evidence: str, *, reporter: str = ""
    ) -> dict:
        """Register a disproven objection without saving (one save per call)."""
        key = _normalize_text(objection or "")
        finding = self._find_objection(objection)
        if finding is None:
            try:
                seq = int(self.data.get("objection_seq", 0))
            except (TypeError, ValueError):
                seq = 0
            self.data["objection_seq"] = seq + 1
            finding = {
                "id": f"OBJ{self.data['objection_seq']}",
                "objection": str(objection or ""),
                "key": key,
                "status": "open",
                "counter_evidence": "",
                "reporters": [],
                "history": [],
                "at": now(),
            }
            self.data.setdefault("objections", []).append(finding)
        if reporter and reporter not in finding["reporters"]:
            finding["reporters"].append(reporter)
        finding["status"] = "disproven"
        finding["counter_evidence"] = str(counter_evidence or "")
        finding["history"].append(
            {
                "status": "disproven",
                "by": reporter or "agent",
                "counter_evidence": str(counter_evidence or ""),
                "at": now(),
            }
        )
        return finding

    def raise_objection(self, objection: str, *, reporter: str = "") -> dict:
        """Raise a standing objection (open until disproven).

        Repeat reports of the same objection (normalized) join the
        one finding instead of duplicating it.
        """
        if not _normalize_text(objection or ""):
            raise CaseError("the objection must say what is challenged")
        finding = self._find_objection(objection)
        if finding is None:
            try:
                seq = int(self.data.get("objection_seq", 0))
            except (TypeError, ValueError):
                seq = 0
            self.data["objection_seq"] = seq + 1
            finding = {
                "id": f"OBJ{self.data['objection_seq']}",
                "objection": str(objection or ""),
                "key": _normalize_text(objection or ""),
                "status": "open",
                "counter_evidence": "",
                "reporters": [],
                "history": [],
                "at": now(),
            }
            self.data.setdefault("objections", []).append(finding)
            finding["history"].append(
                {"status": "open", "by": reporter or "agent", "at": now()}
            )
        if reporter and reporter not in finding["reporters"]:
            finding["reporters"].append(reporter)
        self._save(
            "objection_raised", "agent", {"objection": finding["id"]}
        )
        return dict(finding)

    def disprove_objection(
        self, ref: str, counter_evidence: str, *, reporter: str = ""
    ) -> dict:
        """Disprove an objection with counter-evidence (coordinator)."""
        if not str(counter_evidence or "").strip():
            raise CaseError("disproving needs the counter-evidence that did it")
        finding = self._ledger_disprove(
            ref, counter_evidence, reporter=reporter
        )
        self._save(
            "objection_disproven", "agent", {"objection": finding["id"]}
        )
        return dict(finding)

    def restore_objection(
        self, ref: str, reason: str, *, reporter: str = ""
    ) -> dict:
        """Reopen a disproven objection (judge restore)."""
        if not str(reason or "").strip():
            raise CaseError("restoring needs the reason it was wrongly dropped")
        finding = self._find_objection(ref)
        if finding is None:
            raise CaseError(f"unknown objection {ref!r}")
        if finding.get("status") != "disproven":
            raise CaseError(f"{finding['id']} is already open")
        if reporter and reporter not in finding["reporters"]:
            finding["reporters"].append(reporter)
        finding["status"] = "open"
        finding["history"].append(
            {
                "status": "open",
                "by": reporter or "agent",
                "reason": str(reason or ""),
                "at": now(),
            }
        )
        self._save(
            "objection_restored", "agent", {"objection": finding["id"]}
        )
        return dict(finding)

    def open_objections(self) -> list:
        """Standing objections: raised or restored, never disproven."""
        return [
            dict(finding)
            for finding in self.data.get("objections", [])
            if isinstance(finding, dict) and finding.get("status") == "open"
        ]

    # ---- universal task identification ----
    def identify_task(
        self,
        *,
        requested: str,
        objective: str,
        deliverable: str,
        underlying_problem: str,
        context_available: str,
        missing_or_ambiguous: str,
        constraints: str,
        categories: list,
        dependencies: list | None = None,
        primary: str = "",
        gate_task_type: str = "",
        jurisdiction: str = "",
        law_basis: str = "",
    ) -> dict:
        """Identify the user's actual task and classify it (Stages 1-3).

        Distinguishes the requested output from the underlying legal
        problem, records the deliverable, context, gaps, and
        constraints, and classifies the work across one or more
        taxonomy categories with rationales, inter-category
        dependencies, a primary category, and the gate task type the
        filing checks run under. Recording an identification engages
        the identified gate mode. Re-identifying replaces the record
        (audited), for when the request sharpens mid-run.

        `jurisdiction` records the operator- or source-established
        jurisdiction, if any (never inferred). `law_basis` declares
        where the law comes from (one of LAW_BASES): the scope gate
        refuses background-needed/mixed filings unless narrowed or
        human-qualified (see docs/supported-coverage.md).
        """
        law_basis = str(law_basis or "").strip()
        if law_basis and law_basis not in LAW_BASES:
            raise CaseError(
                f"unknown law basis {law_basis!r}: "
                f"must be one of {', '.join(LAW_BASES)}"
            )
        entry = {
            "requested": str(requested or ""),
            "objective": str(objective or ""),
            "deliverable": str(deliverable or ""),
            "underlying_problem": str(underlying_problem or ""),
            "context_available": str(context_available or ""),
            "missing_or_ambiguous": str(missing_or_ambiguous or ""),
            "constraints": str(constraints or ""),
            "jurisdiction": str(jurisdiction or ""),
            "law_basis": law_basis,
        }
        problems = validate_identification(entry)
        if problems:
            raise CaseError("; ".join(problems))
        if not isinstance(categories, list) or any(
            not isinstance(c, dict) for c in categories
        ):
            raise CaseError("categories must be a list of objects")
        if dependencies is not None and (
            not isinstance(dependencies, list)
            or any(not isinstance(e, dict) for e in dependencies)
        ):
            raise CaseError("dependencies must be a list of objects")
        clean_categories = [
            {
                "category": canonical_category(str(c.get("category", ""))),
                "subcategory": str(c.get("subcategory", "") or ""),
                "rationale": str(c.get("rationale", "")),
            }
            for c in (categories or [])
            if isinstance(c, dict)
        ]
        clean_edges = [
            {"from": canonical_category(str(e.get("from", ""))),
             "to": canonical_category(str(e.get("to", "")))}
            for e in (dependencies or [])
            if isinstance(e, dict)
        ]
        primary = canonical_category(str(primary or ""))
        problems = validate_classification(
            clean_categories, clean_edges, primary,
            str(gate_task_type or ""),
        )
        if problems:
            raise CaseError("; ".join(problems))
        gate_task_type = str(gate_task_type)
        stored = str(self.data.get("task_type", "") or "")
        if stored and stored != gate_task_type:
            raise CaseError(
                f"task type is already {stored!r}; not changing it to {gate_task_type!r}"
            )
        self.data["task_type"] = gate_task_type
        self._pin_assessment_policy(gate_task_type, "identification")
        entry = dict(entry, at=now())
        self.data["identification"] = entry
        self.data["classification"] = {
            "categories": clean_categories,
            "dependencies": clean_edges,
            "primary": str(primary),
            "gate_task_type": gate_task_type,
            "at": now(),
        }
        self._save(
            "task_identified",
            "agent",
            {
                "categories": [c["category"] for c in clean_categories],
                "primary": str(primary),
            },
        )
        return dict(entry, classification=dict(self.data["classification"]))

    def plan_workflow(self, *, components: list, rationale: str = "") -> dict:
        """Select the workflow components and their order (Stage 6).

        Each component names a catalog workflow, its purpose in this
        matter, and which earlier components it runs after. Planning
        again replaces the plan (audited).
        """
        if not isinstance(components, list) or any(
            not isinstance(c, dict) for c in components
        ):
            raise CaseError("components must be a list of objects")
        for c in components:
            after = c.get("after") or []
            if not isinstance(after, list) or any(
                not isinstance(a, str) for a in after
            ):
                raise CaseError("each component's after must be a list of ids")
        clean = [
            {
                "workflow": str(c.get("workflow", "")),
                "purpose": str(c.get("purpose", "")),
                "after": [str(a or "") for a in (c.get("after") or [])],
            }
            for c in components
        ]
        problems = validate_workflow(clean)
        if problems:
            raise CaseError("; ".join(problems))
        plan = {
            "components": clean,
            "rationale": str(rationale or ""),
            "at": now(),
        }
        self.data["workflow"] = plan
        self._save(
            "workflow_planned",
            "agent",
            {"components": [c["workflow"] for c in clean]},
        )
        return plan

    def record_plan(
        self, steps: list, *, actor: str = "agent", reason: str = ""
    ) -> dict:
        """Record one investigation-plan revision.

        Every update appends an immutable revision (number, steps,
        actor, reason, timestamp) to `plan_history` and refreshes the
        current plan at `data["plan"]`, so the plan survives resume
        and stays visible to audit. Earlier revisions are never
        rewritten: re-planning appends (audited).
        """
        clean = (
            [str(s or "") for s in steps] if isinstance(steps, list) else []
        )
        history = self.data.setdefault("plan_history", [])
        entry = {
            "revision": len(history) + 1,
            "steps": clean,
            "actor": str(actor or "agent"),
            "reason": str(reason or ""),
            "at": now(),
        }
        history.append(entry)
        self.data["plan"] = dict(entry)
        self._save(
            "plan_updated",
            entry["actor"],
            {"revision": entry["revision"], "steps": len(clean)},
        )
        return dict(entry)

    def triage_gap(
        self,
        *,
        ref: str,
        gap: str,
        needed_for: str,
        sources_checked: str,
        decision: str,
        detail: str,
    ) -> dict:
        """Decide how to close one information gap (Stage 5).

        `ref` is the unresolved foundational question (FQ id) or
        unknown matter field (matter:name) this decides for, which
        must already be recorded. Decisions: investigate a source,
        ask the user, analyze scenarios, or accept and disclose.
        """
        entry = {
            "ref": str(ref or "").strip(),
            "gap": str(gap or ""),
            "needed_for": str(needed_for or ""),
            "sources_checked": str(sources_checked or ""),
            "decision": str(decision or ""),
            "detail": str(detail or ""),
        }
        problems = validate_triage(entry)
        if problems:
            raise CaseError("; ".join(problems))
        if entry["ref"].startswith("matter:"):
            field = entry["ref"][len("matter:"):]
            fields = self.data.get("matter", {}).get("fields", {})
            if not isinstance(fields, dict) or field not in fields:
                raise CaseError(
                    f"unknown matter field {field!r}: note it first"
                )
        else:
            known = {
                f.get("id")
                for f in self.data.get("foundational", [])
                if isinstance(f, dict)
            }
            if entry["ref"] not in known:
                raise CaseError(
                    f"unknown foundational question {entry['ref']!r}: "
                    "record the question first"
                )
        try:
            seq = int(self.data.get("triage_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["triage_seq"] = seq + 1
        entry = dict(entry, id=f"TG{self.data['triage_seq']}", at=now())
        self.data.setdefault("triages", []).append(entry)
        self._save(
            "gap_triaged",
            "agent",
            {"triage": entry["id"], "ref": entry["ref"],
             "decision": entry["decision"]},
        )
        return entry

    def validate_decomposition(
        self, *, questions: list, waived: list | None = None
    ) -> dict:
        """Validate the planned question set before investigating.

        Checks class coverage, threshold coverage (or waivers),
        dependency validity, redundancy, and resolution criteria
        against the run's classification. Every attempt is recorded
        with its verdict (pass or fail); the filing gate reads the
        latest. Requires identification first.
        """
        classification = self.data.get("classification") or {}
        categories = [
            c.get("category")
            for c in (classification.get("categories") or [])
            if isinstance(c, dict)
        ]
        if not categories:
            raise CaseError("identify the task first: no classification recorded")
        if not isinstance(questions, list) or any(
            not isinstance(q, dict) for q in questions
        ):
            raise CaseError("questions must be a list of objects")
        if waived is not None and (
            not isinstance(waived, list)
            or any(not isinstance(w, dict) for w in waived)
        ):
            raise CaseError("waived thresholds must be a list of objects")
        for entry in questions:
            deps = entry.get("depends_on") or []
            if not isinstance(deps, list) or any(
                not isinstance(d, str) for d in deps
            ):
                raise CaseError("each question's depends_on must be a list of ids")
        clean = [
            {
                "id": str(q.get("id", "")),
                "class": str(q.get("class", "")),
                "question": str(q.get("question", "")),
                "criterion": str(q.get("criterion", "")),
                "issue": str(q.get("issue", "") or ""),
                "depends_on": [str(d) for d in (q.get("depends_on") or [])],
                "threshold": bool(q.get("threshold", False)),
            }
            for q in questions
        ]
        clean_waived = [
            {"area": str(w.get("area", "")), "reason": str(w.get("reason", ""))}
            for w in (waived or [])
        ]
        gate_task_type = str(classification.get("gate_task_type", ""))
        problems, advisory = validate_decomposition(
            clean, clean_waived, categories, gate_task_type
        )
        try:
            seq = int(self.data.get("decomposition_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["decomposition_seq"] = seq + 1
        record = {
            "version": self.data["decomposition_seq"],
            "questions": clean,
            "waived": clean_waived,
            "verdict": "pass" if not problems else "fail",
            "problems": problems,
            "advisory": advisory,
            "at": now(),
        }
        self.data.setdefault("decompositions", []).append(record)
        self._save(
            "decomposition_validated",
            "agent",
            {"version": record["version"], "verdict": record["verdict"]},
        )
        return record

    def declare_work_product(
        self, *, product: str, sections: list, traceability: str
    ) -> dict:
        """Declare the deliverable and its sections (Stage 9).

        Names the work product, the sections it will contain, and how
        its conclusions trace to authorities, facts, and assumptions.
        Declaring again replaces the declaration (audited).
        """
        entry = {
            "product": str(product or ""),
            "sections": [str(s or "") for s in (sections or [])],
            "traceability": str(traceability or ""),
        }
        problems = validate_work_product(entry)
        if problems:
            raise CaseError("; ".join(problems))
        entry = dict(entry, at=now())
        self.data["work_product"] = entry
        self._save(
            "work_product_declared", "agent", {"product": entry["product"]}
        )
        return entry

    # ---- legal branches: which law governs, per issue ----
    def open_branch(
        self,
        issue: str,
        regime: str,
        status: str = "unknown",
        basis: str = "",
        basis_kind: str = "connecting_facts",
    ) -> dict:
        """Open one candidate governing regime for an issue.

        `regime` is a registered region key (location alone never
        implies it: there is no location-to-regime mapping anywhere).
        `basis` records the connecting facts, clause, authority, or
        operator statement the candidacy rests on — required, because
        an unexplained regime is an assumption. `status` tracks whether
        the regime is established for the issue. Different issues may
        depend on different regimes. Opening or resolving a branch
        stales the issue's completed tasks: conclusions may rest on it.
        """
        entry = self._item(issue)
        del entry
        if regime not in REGIONS:
            raise CaseError(
                f"regime {regime!r} is not registered; "
                f"known regimes: {', '.join(sorted(REGIONS))}"
            )
        if status not in MATTER_STATUSES:
            raise CaseError(
                f"status {status!r} must be one of: {', '.join(MATTER_STATUSES)}"
            )
        if basis_kind not in BRANCH_BASIS_KINDS:
            raise CaseError(
                f"basis_kind {basis_kind!r} must be one of: "
                f"{', '.join(BRANCH_BASIS_KINDS)}"
            )
        if not str(basis or "").strip():
            raise CaseError("a branch needs its basis: what makes this regime a candidate")
        branches = self.data.setdefault("branches", {})
        try:
            seq = int(self.data.get("branch_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["branch_seq"] = seq + 1
        branch_id = f"B{self.data['branch_seq']}"
        branches[branch_id] = {
            "id": branch_id,
            "issue": issue,
            "regime": regime,
            "regime_label": REGIONS[regime]["label"],
            "status": status,
            "basis": str(basis),
            "basis_kind": basis_kind,
            "history": [{"at": now(), "from": None, "to": status, "reason": "opened"}],
            "at": now(),
        }
        self._bump_branch_version()
        self._save("branch_opened", "agent", {"branch": branch_id, "regime": regime})
        self._stale_completed_tasks(f"branch {branch_id} opened", [issue])
        return branches[branch_id]

    def resolve_branch(self, branch_id: str, status: str, note: str = "") -> dict:
        """Move a branch to a new status with an explicit note."""
        branches = self.data.setdefault("branches", {})
        if branch_id not in branches:
            raise CaseError(f"unknown branch {branch_id!r}")
        if status not in MATTER_STATUSES:
            raise CaseError(
                f"status {status!r} must be one of: {', '.join(MATTER_STATUSES)}"
            )
        branch = branches[branch_id]
        if branch.get("status") == status:
            raise CaseError(f"branch {branch_id} is already {status}")
        if not str(note or "").strip():
            raise CaseError("resolving a branch needs a note")
        branch.setdefault("history", []).append(
            {
                "at": now(),
                "from": branch.get("status"),
                "to": status,
                "reason": str(note),
            }
        )
        branch["status"] = status
        self._bump_branch_version()
        self._save(
            "branch_resolved",
            "agent",
            {"branch": branch_id, "status": status},
        )
        self._stale_completed_tasks(
            f"branch {branch_id} is now {status}", [branch.get("issue", "")]
        )
        return branch

    def _bump_branch_version(self) -> int:
        try:
            version = int(self.data.get("branch_version", 0)) + 1
        except (TypeError, ValueError):
            version = 1
        self.data["branch_version"] = version
        return version

    def branch_version(self) -> int:
        """The current legal-branch version (0 when none opened yet)."""
        try:
            return int(self.data.get("branch_version", 0))
        except (TypeError, ValueError):
            return 0

    def memo_branch_conditions(self, memo: dict) -> list:
        """Unresolved branches a memo depends on, as readable conditions.

        Every branch cited by findings or the conclusion whose status is
        unknown or disputed stays visible: the memo is conditional on
        it. Unknown branch ids are ignored here; the memo rules flag them.
        """
        branches = self.data.get("branches", {})
        cited = set()
        findings = memo.get("findings") or [] if isinstance(memo, dict) else []
        for finding in findings:
            if isinstance(finding, dict) and finding.get("branch"):
                cited.add(finding.get("branch"))
        depends = memo.get("depends_on") or [] if isinstance(memo, dict) else []
        if isinstance(depends, list):
            cited.update(d for d in depends if isinstance(d, str))
        conditions = []
        for branch_id in sorted(cited):
            branch = branches.get(branch_id)
            if not isinstance(branch, dict):
                continue
            if branch.get("status") in ("unknown", "disputed"):
                conditions.append(
                    f"{branch_id} ({branch.get('regime_label', branch.get('regime'))}): "
                    f"{branch.get('status')}"
                )
        return conditions

    # ---- supplied-authority provenance ----
    def note_source(
        self,
        document_id: str,
        repo: str,
        citation: str,
        version: str,
        language: str = "",
    ) -> dict:
        """Record where a supplied authority document came from.

        The document must be analyzed first; the repository must be
        registered with a citation and a version (today's text is not
        automatically the relevant text). Provenance is model-asserted:
        operator confirmation belongs to a later increment.
        """
        try:
            text = document_text.page_text(self.root, document_id, 1)
        except document_text.DocumentError as error:
            raise CaseError(str(error)) from None
        if text is None:
            raise CaseError(
                f"{document_id} has not been analyzed yet; call analyze_document first"
            )
        problems = validate_provenance(repo, citation, version, language)
        if problems:
            raise CaseError("; ".join(problems))
        document = document_text.get_document(self.root, document_id)
        sources = self.data.setdefault("sources", {})
        sources[document_id] = {
            **self.data.get("public_sources", {}).get(document_id, {}),
            "document_id": document_id,
            "repo": repo,
            "citation": str(citation),
            "version": str(version),
            "language": str(language or ""),
            "sha256": document["sha256"],
            "asserted_by": "model",
            "at": now(),
        }
        self._save("source_noted", "agent", {"document_id": document_id, "repo": repo})
        return sources[document_id]

    def source_drift(self) -> dict:
        """Noted sources whose inbox bytes no longer match the pin.

        Returns {document_id: {"recorded": sha, "current": sha}}
        for every noted source whose file changed or vanished
        (current None when the file is gone). Legacy records
        without a pin are skipped: they predate binding and the
        fact-level pins still guard their quotes. Re-noting the
        source re-pins and clears the drift.
        """
        drift = {}
        sources = self.data.get("sources") or {}
        if not isinstance(sources, dict):
            return drift
        for document_id, entry in sources.items():
            if not isinstance(entry, dict) or not entry.get("sha256"):
                continue
            try:
                current = document_text.get_document(
                    self.root, document_id
                )["sha256"]
            except document_text.DocumentError:
                current = None
            if current != entry["sha256"]:
                drift[str(document_id)] = {
                    "recorded": entry["sha256"],
                    "current": current,
                }
        return drift

    # ---- deterministic computations ----
    def record_computation(
        self, *, op: str, inputs: dict, rule: str = "", assumptions: str = ""
    ) -> dict:
        """Run and record one deterministic computation (Item 5, G5).

        Dates and money are computed by agent_kit.compute, never in
        model prose: the tool records through compute.replay, the
        filing gate re-runs it, and a record that does not replay
        blocks filing. `rule` names the statute, clause, or
        instruction the inputs come from; `assumptions` records what
        was supplied (notably the explicit holiday list behind a
        business-day count). Malformed inputs are refused, naming
        what is missing.
        """
        from . import compute as _compute

        if not isinstance(inputs, dict):
            raise CaseError("computation inputs must be an object")
        try:
            result = _compute.replay(op, dict(inputs))
        except _compute.ComputeError as error:
            raise CaseError(str(error)) from None
        try:
            seq = int(self.data.get("computation_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["computation_seq"] = seq + 1
        entry = {
            "id": f"C{self.data['computation_seq']}",
            "op": str(op),
            "inputs": dict(inputs),
            "result": result,
            "rule": str(rule or ""),
            "assumptions": str(assumptions or ""),
            "at": now(),
        }
        self.data.setdefault("computations", []).append(entry)
        self._save(
            "computation_recorded",
            "agent",
            {"computation": entry["id"], "op": str(op)},
        )
        return entry

    # ---- proposition-level authority registry ----
    def assess_authority(
        self,
        authority_id: str,
        *,
        page: int,
        quote: str,
        treatment_read: bool,
        detail: str = "",
    ) -> dict:
        """Record one verification act for a registered authority.

        A "verified" status is a claim that someone looked: this
        records the look — the page, the exact quote found there,
        whether treatment sources were read — and pins the
        document's sha256. The quote is re-checked against the
        current transcript (not trusted from the caller), so the
        assessment is falsifiable: a swapped file or a
        mis-copied quote fails here, not at filing. On success the
        assessment links itself onto the authority and flips it
        to verified: register provisional first, assess, and the
        record carries its own proof. Treatment sources must be
        read (treatment_read=True): verified implies treatment
        checked, and the assessment is the act that earns both.
        Returns the AA-id record.
        """
        if not treatment_read:
            raise CaseError(
                "assessment needs its treatment sources read first"
            )
        authorities = self.data.get("authorities") or {}
        target = authorities.get(str(authority_id or ""))
        if not isinstance(target, dict):
            raise CaseError(
                f"{authority_id} is not a registered authority: "
                "register the proposition first"
            )
        document_id = str(target.get("document_id") or "")
        try:
            page_number = int(page)
        except (TypeError, ValueError):
            raise CaseError("assessment page must be an integer") from None
        try:
            text = document_text.page_text(self.root, document_id, page_number)
        except document_text.DocumentError as error:
            raise CaseError(str(error)) from None
        if text is None:
            raise CaseError(
                f"{document_id} page {page_number} is not transcribed"
            )
        if not quote or not str(quote).strip():
            raise CaseError("assessment needs the exact quote confirmed")
        if not document_text.quote_on_page(str(quote), text):
            raise CaseError(
                f"assessment quote not found on {document_id} page "
                f"{page_number}; copy the exact words"
            )
        document = document_text.get_document(self.root, document_id)
        try:
            seq = int(self.data.get("assessment_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["assessment_seq"] = seq + 1
        record = {
            "id": f"AA{self.data['assessment_seq']}",
            "authority": str(authority_id),
            "page": page_number,
            "quote": str(quote),
            "sha256": document["sha256"],
            "treatment_read": bool(treatment_read),
            "detail": str(detail or ""),
            "outcome": "passed",
            "at": now(),
        }
        self.data.setdefault("authority_assessments", {})[record["id"]] = record
        target["assessment"] = record["id"]
        target["verification"] = "verified"
        target["treatment_checked"] = True
        if str(target.get("treatment") or "").strip().lower() in (
            "",
            "not checked",
        ):
            target["treatment"] = (
                f"see {record['id']}: "
                f"{str(detail or '').strip() or 'treatment read'}"
            )
        self._save(
            "authority_assessed",
            "agent",
            {"assessment": record["id"], "authority": str(authority_id)},
        )
        return record

    def register_authority(
        self,
        *,
        proposition: str,
        document_id: str,
        citation: str,
        version: str,
        effective_date: str,
        level: str,
        verification: str,
        treatment_checked: bool,
        treatment: str,
        assessment: str = "",
    ) -> dict:
        """Register one relied-upon legal proposition with its authority.

        Layered verification: the document needs noted provenance
        first (note_source); the registry adds the proposition, its
        authority level, effective date, verification status, and
        subsequent treatment. An "assessment" link names the
        supporting assessment record (see assess_authority);
        registration records the claim as stated, and the
        filing gate — not the registry — refuses a relied-upon
        "verified" status whose link does not resolve to a
        passing assessment. Legal-rule
        facts fail the filing gate unless they name the registered
        authority they rely on: the gate resolves each fact's A-id
        link instead of guessing from document membership. The
        citation string is parsed by agent_kit.citations and the
        normalized forms stored with the extractor version
        (parsed_citations may be empty for non-standard strings;
        parsing is structure, not proof the authority exists —
        see gap G3).
        """
        from . import citations as _citations

        entry = {
            "proposition": str(proposition or ""),
            "document_id": str(document_id or ""),
            "citation": str(citation or ""),
            "version": str(version or ""),
            "effective_date": str(effective_date or ""),
            "level": str(level or ""),
            "verification": str(verification or ""),
            "treatment_checked": bool(treatment_checked),
            "treatment": str(treatment or ""),
            "assessment": str(assessment or ""),
        }
        problems = validate_authority(entry)
        if problems:
            raise CaseError("; ".join(problems))
        if entry["document_id"] not in (self.data.get("sources") or {}):
            raise CaseError(
                f"{entry['document_id']} has no noted provenance; "
                "call note_source first"
            )
        try:
            seq = int(self.data.get("authority_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["authority_seq"] = seq + 1
        entry = dict(
            entry,
            id=f"A{self.data['authority_seq']}",
            at=now(),
            parsed_citations=_citations.extract(entry["citation"]),
            citation_backend=_citations.CITATION_BACKEND,
        )
        self.data.setdefault("authorities", {})[entry["id"]] = entry
        self._save(
            "authority_registered",
            "agent",
            {"authority": entry["id"], "level": entry["level"],
             "verification": entry["verification"]},
        )
        return entry

    # ---- authorization ledger ----
    def request_approval(self, *, action: str, scope: str, reason: str) -> dict:
        """Request human approval for an action with consequences.

        Records the proposed action, its scope, and why it is
        needed. No execute tools exist today, so requests document
        intent and wait; decide_approval records the human decision.
        """
        entry = {
            "action": str(action or ""),
            "scope": str(scope or ""),
            "reason": str(reason or ""),
        }
        problems = validate_approval_request(entry)
        if problems:
            raise CaseError("; ".join(problems))
        try:
            seq = int(self.data.get("approval_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["approval_seq"] = seq + 1
        entry = dict(
            entry, id=f"APR{self.data['approval_seq']}",
            decision="", decided_by="", note="", at=now(),
        )
        self.data.setdefault("approvals", []).append(entry)
        self._save(
            "approval_requested", "agent",
            {"approval": entry["id"], "action": entry["action"]},
        )
        return entry

    def decide_approval(
        self, *, approval_id: str, decision: str, decided_by: str, note: str = ""
    ) -> dict:
        """Grant or deny a pending approval request. One decision each."""
        entry = {
            "decision": str(decision or ""),
            "decided_by": str(decided_by or ""),
        }
        problems = validate_approval_decision(entry)
        if problems:
            raise CaseError("; ".join(problems))
        for record in self.data.get("approvals", []):
            if isinstance(record, dict) and record.get("id") == approval_id:
                if record.get("decision"):
                    raise CaseError(
                        f"{approval_id} is already decided: "
                        "request again for a new decision"
                    )
                record["decision"] = entry["decision"]
                record["decided_by"] = entry["decided_by"]
                record["note"] = str(note or "")
                record["decided_at"] = now()
                self._save(
                    "approval_decided", "agent",
                    {"approval": approval_id, "decision": entry["decision"]},
                )
                return record
        raise CaseError(f"unknown approval request {approval_id!r}")

    # ---- research query journal ----
    def log_query(
        self,
        *,
        query_class: str,
        target: str,
        plan: dict,
        issue: str = "",
        scope: str = "",
        regime: str = "",
        branch: str = "",
        outcome: str = "",
        summary: str = "",
        coverage: str = "",
    ) -> dict:
        """Journal one research query: its class, plan, and eventual outcome.

        The plan is stored with the entry so later review can see what
        was attempted, not just what was found. Queries log open, or
        closed in one call when the work is quick (outcome needs its
        summary). Observed contribution is recorded at resolution.
        """
        if query_class not in QUERY_CLASSES:
            raise CaseError(f"unknown query class {query_class!r}")
        if not str(target or "").strip():
            raise CaseError("a query needs its target: what would answer it")
        if branch and branch not in self.data.get("branches", {}):
            raise CaseError(f"unknown branch {branch!r}")
        if outcome and outcome not in QUERY_OUTCOMES:
            raise CaseError(
                f"outcome {outcome!r} must be one of: {', '.join(QUERY_OUTCOMES)}"
            )
        if outcome and not str(summary or "").strip():
            raise CaseError("a closed query needs its summary")
        try:
            seq = int(self.data.get("query_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["query_seq"] = seq + 1
        entry = {
            "id": f"Q{self.data['query_seq']}",
            "class": query_class,
            "target": str(target),
            "plan": dict(plan),
            "issue": str(issue or ""),
            "scope": str(scope or ""),
            "regime": str(regime or ""),
            "branch": str(branch or ""),
            "status": "closed" if outcome else "open",
            "outcome": str(outcome or ""),
            "summary": str(summary or ""),
            "coverage": str(coverage or ""),
            "at": now(),
        }
        self.data.setdefault("queries", []).append(entry)
        self._save(
            "query_logged",
            "agent",
            {"query": entry["id"], "class": query_class, "status": entry["status"]},
        )
        return entry

    def resolve_query(
        self, query_id: str, outcome: str, summary: str, coverage: str = ""
    ) -> dict:
        """Close an open query with what it contributed."""
        if outcome not in QUERY_OUTCOMES:
            raise CaseError(
                f"outcome {outcome!r} must be one of: {', '.join(QUERY_OUTCOMES)}"
            )
        if not str(summary or "").strip():
            raise CaseError("resolving a query needs its summary")
        for entry in self.data.get("queries", []):
            if isinstance(entry, dict) and entry.get("id") == query_id:
                if entry.get("status") != "open":
                    raise CaseError(f"query {query_id} is already resolved")
                entry["status"] = "closed"
                entry["outcome"] = outcome
                entry["summary"] = str(summary)
                entry["coverage"] = str(coverage or "")
                entry["answered_at"] = now()
                self._save(
                    "query_resolved", "agent", {"query": query_id, "outcome": outcome}
                )
                return entry
        raise CaseError(f"unknown query {query_id!r}")

    # ---- evidence obligations ----
    def set_evidence_needs(
        self,
        item: str,
        required: list,
        adverse: list | None = None,
        superseded: list | None = None,
    ) -> dict:
        """Freeze what a checklist item needs before it can conclude.

        required: [{label, passages: [(document_id, page), ...]}];
        each label needs at least one of its passages recorded.
        adverse: [(document_id, page), ...] that should surface.
        superseded: [(document_id, page), ...] that must not decide.
        The filing gate holds asserted findings on this item to
        these needs: unmet needs fail unless gaps disclose them.
        """
        self._item(item)
        needs = {
            "item": str(item),
            "required": [
                {
                    "label": str(need.get("label", "?")),
                    "passages": [
                        _need_pair(pair)
                        for pair in (need.get("passages", []) or [])
                    ],
                }
                for need in (required or [])
                if isinstance(need, dict)
            ],
            "adverse": [_need_pair(pair) for pair in (adverse or [])],
            "superseded": [
                _need_pair(pair) for pair in (superseded or [])
            ],
        }
        store = self.data.setdefault("evidence_needs", {})
        prior = store.get(str(item))
        if isinstance(prior, dict):
            # Re-freezing keeps the superseded needs visible, so
            # narrowing needs mid-run to pass the gate leaves a trail.
            entry = {k: v for k, v in prior.items() if k != "history"}
            entry["superseded_at"] = now()
            prior_history = prior.get("history", [])
            prior_history = prior_history if isinstance(
                prior_history, list) else []
            needs["history"] = [*prior_history, entry]
        store[str(item)] = needs
        self._save(
            "evidence_needs_set", "agent", {"item": str(item)})
        return needs

    def evidence_status(self, item: str) -> dict:
        """Judge recorded facts against the item's frozen needs.

        Returns the sufficiency verdict plus the needs echo.
        Refused when the item carries no needs: declare them
        first with set_evidence_needs.
        """
        from . import sufficiency as _sufficiency

        self._item(item)
        needs = (self.data.get("evidence_needs") or {}).get(str(item))
        if not isinstance(needs, dict):
            raise CaseError(
                f"{item} carries no evidence needs; "
                "set them before asking status"
            )
        required = needs.get("required", [])
        adverse = needs.get("adverse", [])
        superseded = needs.get("superseded", [])
        if not all(isinstance(v, list) for v in (
                required, adverse, superseded)):
            raise CaseError(
                f"{item} carries corrupt evidence needs; re-freeze them"
            )
        returned = []
        for fact in (self.data.get("facts") or []):
            if not isinstance(fact, dict):
                continue
            if str(fact.get("item", "")) != str(item):
                continue
            if fact.get("withdrawn"):
                continue
            try:
                page = int(fact.get("page", 0))
            except (TypeError, ValueError):
                continue
            returned.append((str(fact.get("document_id")), page))
        verdict = _sufficiency.assess(
            required, returned, adverse=adverse, superseded=superseded)
        return {"item": str(item), **verdict}

    # ---- durable tasks ----
    def ensure_root_task(self) -> dict:
        """The run's root investigation task, created on first use."""
        tasks = self.data.setdefault("tasks", {})
        if ROOT_TASK not in tasks:
            tasks[ROOT_TASK] = {
                "id": ROOT_TASK,
                "parent": None,
                "kind": "investigate",
                "issues": [],
                "question": "Investigate the matter and file the memo.",
                "acceptance": (
                    "Memo filed and decided, or the run paused with "
                    "its obligations recorded."
                ),
                "status": "open",
                "blocked_on": "",
                "dependencies": [],
                "inputs": {},
                "attempts": [],
                "history": [],
                "lease": None,
                "seq": 0,
            }
            self._save("task_opened", "system", {"task": ROOT_TASK})
        return tasks[ROOT_TASK]

    def task(self, task_id: str) -> dict:
        """One task by id. Refused when unknown."""
        tasks = self.data.setdefault("tasks", {})
        if task_id not in tasks:
            raise CaseError(f"unknown task {task_id!r}")
        return tasks[task_id]

    def _transition(self, task: dict, to: str, reason: str) -> bool:
        if task.get("status") == to:
            return False
        task.setdefault("history", []).append(
            {"at": now(), "from": task.get("status"), "to": to, "reason": reason}
        )
        task["status"] = to
        return True

    def sync_issue_tasks(self) -> dict:
        """One task per checklist item: open new ones, withdraw dropped ones.

        Called whenever the checklist is set. Dropped items have no
        facts or requests (parse_checklist refuses otherwise), so
        withdrawing their tasks loses no evidence.
        """
        self.ensure_root_task()
        tasks = self.data.setdefault("tasks", {})
        by_issue = {
            issue: task
            for task in tasks.values()
            if isinstance(task, dict) and task.get("kind") == "issue"
            for issue in (task.get("issues") or [])
        }
        opened, withdrawn, revived = [], [], []
        for item, entry in self.data.get("checklist", {}).items():
            existing = by_issue.get(item)
            if existing is not None:
                if existing.get("status") == "withdrawn":
                    self._transition(existing, "open", "checklist item re-added")
                    revived.append(existing["id"])
                continue
            self.data["task_seq"] = self._next_task_seq()
            task_id = f"T{self.data['task_seq']}"
            tasks[task_id] = {
                "id": task_id,
                "parent": ROOT_TASK,
                "kind": "issue",
                "issues": [item],
                "question": entry.get("question", ""),
                "acceptance": (
                    "A supported answer, or an explicitly unresolved "
                    "statement of what is unknown. "
                    f"Evidence needed: {entry.get('evidence_needed', '')}"
                ),
                "status": "open",
                "blocked_on": "",
                "dependencies": [],
                "inputs": {},
                "attempts": [],
                "history": [],
                "lease": None,
                "seq": self.data["task_seq"],
            }
            opened.append(task_id)
        checklist = self.data.get("checklist", {})
        for issue, task in by_issue.items():
            if issue not in checklist and task.get("status") != "withdrawn":
                self._transition(task, "withdrawn", f"checklist item {issue} removed")
                withdrawn.append(task["id"])
        if opened or withdrawn or revived:
            self._save(
                "tasks_synced",
                "system",
                {"opened": opened, "withdrawn": withdrawn, "revived": revived},
            )
        return {"opened": opened, "withdrawn": withdrawn, "revived": revived}

    def _next_task_seq(self) -> int:
        """Next task sequence number. Ids are never recycled, even after withdrawal."""
        try:
            seq = int(self.data.get("task_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        live = [
            int(t["id"][1:])
            for t in self.data.get("tasks", {}).values()
            if isinstance(t, dict)
            and isinstance(t.get("id"), str)
            and t["id"].startswith("T")
            and t["id"][1:].isdigit()
        ]
        return max([seq, *live, 0]) + 1

    def add_dependency(self, task_id: str, depends_on: str) -> dict:
        """Task `task_id` cannot start until `depends_on` completes.

        Circular prerequisites are refused: revision loops create new
        versioned work instead of dependency cycles.
        """
        task, other = self.task(task_id), self.task(depends_on)
        if task_id == depends_on:
            raise CaseError(f"task {task_id} cannot depend on itself")
        if depends_on in (task.get("dependencies") or []):
            return task
        if self._reaches(depends_on, task_id):
            raise CaseError(
                f"task {depends_on} already depends on {task_id}: "
                "dependencies cannot circle"
            )
        task.setdefault("dependencies", []).append(depends_on)
        self._save(
            "task_dependency",
            "agent",
            {"task": task_id, "depends_on": depends_on},
        )
        return task

    def _reaches(self, task_id: str, target: str) -> bool:
        seen, stack = set(), [task_id]
        tasks = self.data.get("tasks", {})
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            task = tasks.get(current)
            if isinstance(task, dict):
                stack.extend(task.get("dependencies") or [])
        return False

    def input_versions(self) -> dict:
        """The versions a completion pins: evidence, matter, checklist, branches."""
        return {
            "evidence": _evidence_fingerprint(self.data.get("facts", [])),
            "matter": self.matter_version(),
            "checklist": int(self.data.get("checklist_version", 0)),
            "branches": self.branch_version(),
        }

    def _stale_completed_tasks(self, reason: str, items: list | None = None) -> list:
        staled = []
        for task in self.data.get("tasks", {}).values():
            if not (isinstance(task, dict) and task.get("status") == "completed"):
                continue
            if items is not None and not set(task.get("issues") or []) & set(items):
                continue
            self._transition(task, "stale", reason)
            staled.append(task["id"])
        if staled:
            self._save("tasks_staled", "system", {"tasks": staled, "reason": reason})
        return staled

    def _tasks_for_item(self, item: str) -> list:
        return [
            task
            for task in self.data.get("tasks", {}).values()
            if isinstance(task, dict)
            and task.get("kind") == "issue"
            and item in (task.get("issues") or [])
            and task.get("status") != "withdrawn"
        ]

    def _block_on_request(self, item: str, request_id: str) -> None:
        moved = []
        for task in self._tasks_for_item(item):
            if task.get("status") in ("open", "stale"):
                task["blocked_on"] = request_id
                self._transition(task, "blocked", f"waiting on request {request_id}")
                moved.append(task["id"])
            elif task.get("status") == "completed":
                self._transition(
                    task, "stale", f"request {request_id} opened for answered work"
                )
                moved.append(task["id"])
        if moved:
            self._save(
                "task_blocked", "agent", {"tasks": moved, "request": request_id}
            )

    def _unblock_request(self, request_id: str) -> None:
        moved = []
        for task in self.data.get("tasks", {}).values():
            if (
                isinstance(task, dict)
                and task.get("status") == "blocked"
                and task.get("blocked_on") == request_id
            ):
                task["blocked_on"] = ""
                self._transition(task, "open", f"request {request_id} answered")
                moved.append(task["id"])
        if moved:
            self._save("task_unblocked", "human", {"tasks": moved, "request": request_id})

    def fail_task(self, task_id: str, reason: str) -> dict:
        """Mark a task failed with an explicit reason: its question cannot proceed.

        A failed task is terminal until explicitly reopened: selection
        skips it rather than retrying blindly.
        """
        task = self.task(task_id)
        if task.get("status") in ("completed", "withdrawn"):
            raise CaseError(f"task {task_id} is {task.get('status')}: cannot fail it")
        if not str(reason or "").strip():
            raise CaseError("failing needs a reason")
        self._transition(task, "failed", str(reason))
        self._save("task_failed", "agent", {"task": task_id})
        return task

    # ---- worker leases ----
    @staticmethod
    def lease_live(lease: dict | None, epoch_now: float) -> bool:
        if not isinstance(lease, dict):
            return False
        try:
            return float(lease.get("expires", 0)) > float(epoch_now)
        except (TypeError, ValueError):
            return False

    def acquire_lease(
        self,
        task_id: str,
        worker: str,
        ttl: float,
        reservation: int,
        epoch_now: float,
        versions: dict,
    ) -> dict:
        """Lease a task to one worker: the fencing token for this job.

        Only open or stale tasks lease; terminal, blocked, and running
        tasks refuse. A live lease held by another worker refuses with
        LeaseBusy; an expired one is superseded (fencing: the old
        token never validates again). The token is monotonic per case.
        """
        task = self.task(task_id)
        if task.get("status") not in ("open", "stale"):
            raise LeaseError(
                f"task {task_id} is {task.get('status')}: cannot lease it"
            )
        current = task.get("lease")
        if self.lease_live(current, epoch_now):
            raise LeaseBusy(
                f"task {task_id} is leased to {current.get('worker')}"
            )
        try:
            seq = int(self.data.get("lease_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        self.data["lease_seq"] = seq + 1
        lease = {
            "worker": str(worker),
            "token": self.data["lease_seq"],
            "granted": now(),
            "expires": float(epoch_now) + float(ttl),
            "reservation": int(reservation),
            "versions": dict(versions),
        }
        task["lease"] = lease
        self._save(
            "lease_acquired",
            "system",
            {"task": task_id, "worker": str(worker), "token": lease["token"]},
        )
        return dict(lease)

    def release_lease(self, task_id: str, token: int) -> dict | None:
        """Release a lease by token. A mismatched token is stale, never released."""
        task = self.task(task_id)
        current = task.get("lease")
        if not isinstance(current, dict) or current.get("token") != token:
            raise StaleLease(
                f"task {task_id}: lease token {token} is expired or superseded"
            )
        task["lease"] = None
        self._save(
            "lease_released", "system", {"task": task_id, "token": token}
        )
        return dict(current)

    def break_lease(self, task_id: str, reason: str) -> None:
        """Audited override: clear whatever lease a task holds, live or not."""
        task = self.task(task_id)
        if not isinstance(task.get("lease"), dict):
            return
        if not str(reason or "").strip():
            raise CaseError("breaking a lease needs a reason")
        task["lease"] = None
        self._save("lease_broken", "system", {"task": task_id, "reason": str(reason)})

    def reopen_task(self, task_id: str, reason: str) -> dict:
        """Return a failed task to open with an explicit reason."""
        task = self.task(task_id)
        if task.get("status") != "failed":
            raise CaseError(f"task {task_id} is {task.get('status')}, not failed")
        if not str(reason or "").strip():
            raise CaseError("reopening needs a reason")
        self._transition(task, "open", str(reason))
        self._save("task_reopened", "agent", {"task": task_id})
        return task

    # ---- attempts: one worker, one slice at a time ----
    def _open_attempt(self, task: dict) -> dict | None:
        for attempt in task.get("attempts") or []:
            if isinstance(attempt, dict) and "ended" not in attempt:
                return attempt
        return None

    def begin_attempt(
        self, task_id: str, worker: str, allocated_turns: int = 0
    ) -> dict:
        """Start one attempt on a task: the worker's lease for this slice.

        One writer runs at a time: beginning while an unrelated task
        runs is refused, but a task may run under a running ancestor
        (the coordinator committing worker results while the root
        slice runs). An attempt left open by a crashed slice is
        marked interrupted before the new one begins, so recovery is
        explicit. Concurrent worker fencing lives in the lease row.
        """
        task = self.task(task_id)
        if task.get("status") in ("completed", "failed", "withdrawn"):
            raise CaseError(f"task {task_id} is {task.get('status')}: nothing to run")
        if task.get("status") == "blocked":
            raise CaseError(
                f"task {task_id} is blocked ({task.get('blocked_on')}); "
                "unblock it first"
            )
        ancestors = set()
        parent = task.get("parent")
        while isinstance(parent, str) and parent and parent not in ancestors:
            ancestors.add(parent)
            node = self.data.get("tasks", {}).get(parent)
            parent = node.get("parent") if isinstance(node, dict) else None
        for other in self.data.get("tasks", {}).values():
            if (
                isinstance(other, dict)
                and other.get("id") != task_id
                and other.get("status") == "running"
                and other.get("id") not in ancestors
            ):
                raise CaseError(f"task {other.get('id')} is already running")
        stale = self._open_attempt(task)
        if stale is not None:
            stale.update(
                {
                    "ended": now(),
                    "status": "interrupted",
                    "summary": "slice never committed; superseded",
                }
            )
            self._save(
                "attempt_interrupted",
                "system",
                {"task": task_id, "attempt": stale.get("n")},
            )
        try:
            turn_before = int(load_progress(self.root, self.data["run_id"])["turn"])
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            turn_before = 0
        attempt = {
            "n": len(task.get("attempts") or []) + 1,
            "worker": str(worker),
            "allocated_turns": int(allocated_turns or 0),
            "started": now(),
            "turn_before": turn_before,
            "tokens_before": tokens_spent(self.root, self.data["run_id"]),
        }
        task.setdefault("attempts", []).append(attempt)
        self._transition(task, "running", f"attempt {attempt['n']} begun by {worker}")
        self._save(
            "attempt_begun",
            "system",
            {"task": task_id, "attempt": attempt["n"], "worker": str(worker)},
        )
        return attempt

    def commit_attempt(
        self,
        task_id: str,
        *,
        status: str,
        summary: str,
        stop_reason: str = "",
        turns_used: int | None = None,
        tokens_used: int | None = None,
    ) -> dict:
        """Close the open attempt with its outcome. Attempt history only.

        Committing never completes the task itself: issue tasks
        complete through a passed assessment (mark_answered), the root
        through filing and review. The task returns to open so the
        next slice can run. Turns and tokens default to this slice's
        measured share; the coordinator passes explicit worker usage.
        """
        if status not in ("completed", "partial", "blocked", "failed"):
            raise CaseError(
                f"attempt status {status!r} must be completed, partial, "
                "blocked, or failed"
            )
        if not str(summary or "").strip():
            raise CaseError("an attempt needs a summary of what happened")
        task = self.task(task_id)
        attempt = self._open_attempt(task)
        if attempt is None:
            raise CaseError(f"task {task_id} has no open attempt to commit")
        if turns_used is None:
            try:
                current = int(load_progress(self.root, self.data["run_id"])["turn"])
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                current = int(attempt.get("turn_before", 0) or 0)
            turns_used = current - int(attempt.get("turn_before", 0) or 0)
        if tokens_used is None:
            try:
                spent = tokens_spent(self.root, self.data["run_id"])
            except (OSError, ValueError):
                spent = None
            tokens_used = (
                None
                if spent is None
                else spent - int(attempt.get("tokens_before", 0) or 0)
            )
        attempt.update(
            {
                "ended": now(),
                "status": status,
                "summary": str(summary),
                "stop_reason": str(stop_reason or ""),
                "turns_used": int(turns_used or 0),
                "tokens_used": tokens_used,
            }
        )
        if task.get("status") == "running":
            # A filing or review may have settled the task mid-slice;
            # committing history never reopens it.
            self._transition(
                task, "open", f"attempt {attempt['n']} {status}: {summary}"
            )
        self._save(
            "attempt_committed",
            "system",
            {"task": task_id, "attempt": attempt["n"], "status": status},
        )
        return attempt

    def claims_staleness(self) -> dict:
        """Staleness of the latest claims check against current state.

        {} when no claims check with a version binding exists yet;
        otherwise quality/staleness.assess of that check versus the
        current facts and filed memo.
        """
        from quality.staleness import assess

        latest = None
        for record in self.data.get("memo_checks", []):
            if (
                isinstance(record, dict)
                and record.get("check") == "claims"
                and isinstance(record.get("scope"), dict)
            ):
                latest = record
        if latest is None:
            return {}
        staleness = assess(
            latest, self.data.get("facts", []), self.data.get("memo")
        )
        changed_at = self._model_changed_after(latest.get("at", ""))
        if changed_at:
            assessed = (latest.get("scope") or {}).get("assessed") or []
            for unit in assessed:
                staleness["stale"].setdefault(
                    unit,
                    "assessed under a different model; config changed "
                    f"{changed_at}, reassess before relying on it",
                )
        return staleness

    def _model_changed_after(self, check_at: str) -> str:
        """Latest resume-time model change after a claims check ("" when none)."""
        try:
            notes = (
                json.loads(
                    (
                        self.root
                        / "workspace/runs"
                        / self.data["run_id"]
                        / "progress.json"
                    ).read_text()
                ).get("notes")
                or {}
            )
        except (OSError, ValueError, KeyError):
            return ""
        latest = ""
        for record in notes.get("config_changes") or []:
            if not isinstance(record, dict):
                continue
            at = str(record.get("at", ""))
            changes = record.get("changes") or {}
            if at > str(check_at or "") and "model" in changes and at > latest:
                latest = at
        return latest

    def approval_status(self) -> dict:
        """Whether the current approval still covers the current state.

        {"bound": False} when the current memo carries no approval.
        Otherwise the snapshot, a current flag (None for legacy
        approvals that predate version binding), and the claims
        staleness for the review banner.
        """
        from quality.records import evidence_fingerprint, memo_hash

        memo = self.data.get("memo") or {}
        review = memo.get("review") or {}
        if review.get("decision") != "approved":
            return {"bound": False}
        snapshot = review.get("snapshot")
        if not isinstance(snapshot, dict):
            return {
                "bound": True,
                "current": None,
                "note": "legacy approval predates version binding",
                "snapshot": None,
            }
        content = memo_hash(
            {key: memo.get(key) for key in ("conclusion", "findings", "gaps")}
        )
        evidence = evidence_fingerprint(self.data)
        staleness = self.claims_staleness() or {
            "stale": {},
            "unassessed": [],
            "unknown": False,
        }
        return {
            "bound": True,
            "current": (
                content == snapshot.get("content")
                and evidence == snapshot.get("evidence")
            ),
            "snapshot": snapshot,
            "stale": staleness.get("stale", {}),
            "unassessed": staleness.get("unassessed", []),
        }

    def memo_refusals(self) -> int:
        """Substantive memo refusals: unavailable checks never count as refusals."""
        return sum(
            1
            for c in self.data.get("memo_checks", [])
            if not c["passed"] and c.get("outcome", "failed") != "unavailable"
        )

    def mark_read(self, document_ids: list) -> None:
        """Remember which documents the agent has read (the escalation rule needs it).

        Only successful reads count; a success also clears that document's
        failed-read entry, if any.
        """
        read = self.data.setdefault("read_ids", [])
        new = [d for d in document_ids if d not in read]
        failed = self.data.get("failed_reads", {})
        cleared = [d for d in document_ids if d in failed]
        for doc_id in cleared:
            del failed[doc_id]
        if new:
            read += new
        if new or cleared:
            self._save(
                "documents_read",
                "agent",
                {"documents": new, "cleared_failures": cleared},
            )

    def mark_read_failed(self, failures: dict) -> None:
        """Remember documents a read attempt could not complete.

        Values are notes or {"note", "class"} with a READ_FAILURE_CLASSES
        class. Each call counts one attempt per document. Entries are
        {"note", "attempts", "class"}; legacy string entries read as one
        unknown-class attempt and migrate on the next failure.
        """
        if not failures:
            return
        failed = self.data.setdefault("failed_reads", {})
        for doc_id, value in failures.items():
            if isinstance(value, dict):
                note = str(value.get("note", ""))
                failure_class = str(value.get("class", "unknown"))
                if failure_class not in READ_FAILURE_CLASSES:
                    failure_class = "unknown"
            else:
                note, failure_class = str(value), "unknown"
            entry = failed.get(doc_id)
            attempts = self.read_attempts(doc_id)
            if isinstance(entry, dict):
                entry["note"] = note
                entry["attempts"] = attempts + 1
                entry["class"] = failure_class
            else:
                failed[doc_id] = {
                    "note": note,
                    "attempts": attempts + 1,
                    "class": failure_class,
                }
        self._save("documents_read_failed", "agent", {"documents": list(failures)})

    def read_attempts(self, doc_id: str) -> int:
        """Failed-read attempts for a document (0 when never failed).

        Legacy string entries count as one attempt. Malformed dict
        entries raise instead of silently reading as zero.
        """
        entry = self.data.get("failed_reads", {}).get(doc_id)
        if entry is None:
            return 0
        if isinstance(entry, dict):
            try:
                attempts = int(entry.get("attempts", 0))
            except (TypeError, ValueError):
                raise CaseError(
                    f"failed_reads entry for {doc_id} is malformed: "
                    "attempts must be an integer"
                ) from None
            if attempts < 0:
                raise CaseError(
                    f"failed_reads entry for {doc_id} is malformed: "
                    "attempts must not be negative"
                )
            return attempts
        return 1

    def failure_class(self, doc_id: str) -> str:
        """The failure class for a document: restricted wins by current flag state."""
        if self.flagged(doc_id):
            return "restricted"
        entry = self.data.get("failed_reads", {}).get(doc_id)
        if isinstance(entry, dict):
            failure_class = entry.get("class", "unknown")
            if failure_class in READ_FAILURE_CLASSES:
                return failure_class
        return "unknown"

    def progress(self) -> dict:
        """Where the checklist stands: answered, escalated and open items, and next step."""
        checklist = self.data["checklist"]
        checks = self.data.get("memo_checks", [])
        by_status: dict[str, list] = {"answered": [], "escalated": [], "open": []}
        for key, entry in checklist.items():
            by_status.setdefault(effective_status(entry, checks), []).append(key)
        done = len(by_status["answered"]) + len(by_status["escalated"])
        if by_status["open"]:
            next_step = (
                f"Open: {', '.join(by_status['open'])}. Get one fact for each open item "
                "before adding more to any item."
            )
        else:
            next_step = (
                "Every item is answered or escalated: file the memo now. "
                "Add another fact only if one could be disputed."
            )
        collected_only = [
            item for item in by_status["open"] if checklist[item]["facts"]
        ]
        if collected_only:
            next_step += (
                f" Collected but unassessed: {', '.join(collected_only)} "
                "(quotes recorded, support not yet assessed)."
            )
        contested = self.contested_items()
        if contested:
            next_step += (
                f" Contested: {', '.join(contested)} "
                "(supporting and opposing evidence collected)."
            )
        since = self.data.get("facts_since_assessment", 0)
        if since >= 3:
            next_step += (
                f" {since} facts recorded since the last claims assessment; "
                "if you have review_draft, check a draft before gathering more."
            )
        return {
            "answered": len(by_status["answered"]),
            "escalated": len(by_status["escalated"]),
            "open": by_status["open"],
            "contested": contested,
            "total": len(checklist),
            "summary": f"{done} of {len(checklist)} items answered or escalated. {next_step}",
            "facts_since_assessment": since,
        }

    def record_fact(
        self,
        item: str,
        document_id: str,
        page: int,
        quote: str,
        claim: str,
        stance: str = "supports",
        basis: str = "",
        supersedes: str = "",
        kind: str = "fact",
        authority: str = "",
    ) -> dict:
        """Collect evidence for a checklist item. Refused unless the quote is on that page.

        Recording never settles the item: collection, the investigator's
        stance, assessed support, and issue resolution are separate states,
        and only a passed assessment marks an item answered. At most
        MAX_FACTS_PER_CASE live facts per case (FactLimitError); memo
        citations stay a separate, smaller selection. Recording the exact
        same evidence twice is idempotent: the existing fact comes back,
        unchanged. `basis` optionally records why this passage matters
        (for example, an independent observation versus a repeated
        statement). `supersedes` optionally names a live fact this one
        corrects, linking the versions without withdrawing the original.
        `kind` says what the claim is: a fact, a legal rule, an
        applicability conclusion, or an inference. `authority` names the
        registered authority a legal-rule fact relies on (its A-id):
        the filing gate resolves the link instead of guessing from
        document membership, so registering P1 can never validate a
        different P2 from the same document. The same passage
        recorded under a different kind is a different assertion, not
        a duplicate. The returned fact carries `progress` (see
        progress()) so the model sees what is still open. Fidelity is
        "extraction": the quote matched saved extraction text, which is
        narrower than an independent check against the original page
        (not implemented).
        """
        entry = self._item(item)
        if stance not in FACT_STANCES:
            raise CaseError(
                f"stance {stance!r} must be one of: {', '.join(FACT_STANCES)}"
            )
        if kind not in CLAIM_KINDS:
            raise CaseError(
                f"kind {kind!r} must be one of: {', '.join(CLAIM_KINDS)}"
            )
        try:
            page = int(page)
            if not claim or not str(claim).strip():
                raise CaseError("claim must be non-empty")
            if self.flagged(document_id):
                raise CaseError(
                    f"{document_id} is flagged as confidential and can never be cited"
                )
            document = document_text.get_document(self.root, document_id)
            if redactions.is_quarantined(self.root, document_id):
                raise CaseError(
                    f"{document_id} is quarantined; a reviewer must "
                    "release it before its quotes can be recorded"
                )
            text = document_text.page_text(self.root, document_id, page)
            if text is None:
                raise CaseError(
                    f"{document_id} has not been analyzed yet; call analyze_document first"
                )
            if not document_text.quote_on_page(quote, text):
                raise CaseError(
                    f"quote not found on {document_id} page {page}; copy the exact words from the document"
                )
        except (CaseError, document_text.DocumentError, TypeError, ValueError) as error:
            self.data["rejected_facts"].append(
                {
                    "item": item,
                    "document_id": document_id,
                    "page": page,
                    "quote": quote,
                    "reason": str(error),
                    "at": now(),
                }
            )
            self._save(
                "fact_rejected",
                "agent",
                {"item": item, "document_id": document_id, "reason": str(error)},
            )
            raise CaseError(str(error)) from None
        for fid in entry["facts"]:
            existing = next(
                (f for f in self.data["facts"] if f.get("id") == fid), None
            )
            if (
                existing is not None
                and existing.get("document_id") == document_id
                and existing.get("page") == page
                and document_text.normalize(str(existing.get("quote") or ""))
                == document_text.normalize(quote)
                and stance_of(existing) == stance
                and existing.get("kind", "fact") == kind
            ):
                return dict(existing, progress=self.progress())
        if len(self.data["facts"]) >= MAX_FACTS_PER_CASE:
            raise FactLimitError(
                f"the case already has {MAX_FACTS_PER_CASE} facts, the most it takes. "
                + self.progress()["summary"]
            )
        supersedes = str(supersedes or "").strip()
        if supersedes and not any(
            f.get("id") == supersedes for f in self.data["facts"]
        ):
            raise CaseError(f"no live fact {supersedes} to supersede")
        self.data["fact_seq"] = self._next_fact_seq()
        authority = str(authority or "").strip()
        fact = {
            "id": f"F{self.data['fact_seq']}",
            "item": item,
            "document_id": document_id,
            "page": page,
            "quote": quote,
            "claim": str(claim),
            "stance": stance,
            "kind": kind,
            "basis": str(basis or ""),
            "supersedes": supersedes,
            "fidelity": "extraction",
            "sha256": document["sha256"],
            "at": now(),
        }
        if authority:
            fact["authority"] = authority
        self.data["facts"].append(fact)
        entry["facts"].append(fact["id"])
        self.data["facts_since_assessment"] = (
            self.data.get("facts_since_assessment", 0) + 1
        )
        self._save(
            "fact_recorded",
            "agent",
            {
                "fact": fact["id"],
                "item": item,
                "document_id": document_id,
                "page": page,
            },
        )
        self._stale_completed_tasks(f"new evidence {fact['id']}", [item])
        return dict(fact, progress=self.progress())

    def record_facts(self, facts: list) -> dict:
        """Collect up to MAX_FACTS_PER_BATCH proposed facts in one call.

        Each entry goes through record_fact in order, with identical
        per-entry validation (item, analyzed non-confidential
        document, analyzed page, exact quote, non-empty claim, stance,
        live supersedes target, case-wide cap). One bad entry fails
        only itself; the rest still commit, so an entry may supersede
        an earlier entry of the same batch. A malformed, empty, or
        over-cap batch is refused as a whole. Retrying an accepted
        batch returns the same fact ids without new facts. Recording
        never settles an item. The result is compact — per-entry ids
        or errors plus counts — while full evidence stays persisted
        in the case record; every returned id resolves there.
        """
        if not isinstance(facts, list) or not facts:
            raise CaseError("facts must be a non-empty list of fact proposals")
        if len(facts) > MAX_FACTS_PER_BATCH:
            raise CaseError(
                f"a batch holds at most {MAX_FACTS_PER_BATCH} facts; "
                "split it and retry"
            )
        results = []
        for index, proposal in enumerate(facts):
            if not isinstance(proposal, dict):
                results.append(
                    {
                        "index": index,
                        "ok": False,
                        "op": f"op-entry-{index}",
                        "error": (
                            "each fact proposal must be an object with "
                            "item, document_id, page, quote, and claim"
                        ),
                    }
                )
                continue
            op = batch_op_id(proposal)
            try:
                fact = self.record_fact(
                    str(proposal.get("item", "")),
                    str(proposal.get("document_id", "")),
                    proposal.get("page"),
                    str(proposal.get("quote", "")),
                    str(proposal.get("claim", "")),
                    str(proposal.get("stance", "supports")),
                    str(proposal.get("basis", "") or ""),
                    str(proposal.get("supersedes", "") or ""),
                    str(proposal.get("kind", "fact")),
                    str(proposal.get("authority", "") or ""),
                )
            except CaseError as error:
                results.append(
                    {"index": index, "ok": False, "op": op, "error": str(error)}
                )
                continue
            results.append(
                {"index": index, "ok": True, "op": op, "id": fact["id"]}
            )
        accepted = [r["id"] for r in results if r["ok"]]
        self._save(
            "fact_batch_recorded",
            "agent",
            {
                "accepted": len(accepted),
                "facts": accepted,
                "errors": len(results) - len(accepted),
            },
        )
        return {
            "accepted": len(accepted),
            "errors": len(results) - len(accepted),
            "results": results,
            "facts_since_assessment": self.data.get("facts_since_assessment", 0),
            "open": self.progress()["open"],
        }

    def _next_fact_seq(self) -> int:
        """Next fact sequence number. Ids are never recycled, even after withdrawal."""
        try:
            seq = int(self.data.get("fact_seq", 0))
        except (TypeError, ValueError):
            seq = 0
        live = [
            int(f["id"][1:])
            for f in self.data.get("facts", [])
            if isinstance(f, dict)
            and isinstance(f.get("id"), str)
            and f["id"].startswith("F")
            and f["id"][1:].isdigit()
        ]
        withdrawn = [
            int(f["id"][1:])
            for f in self.data.get("withdrawn_facts", [])
            if isinstance(f, dict)
            and isinstance(f.get("id"), str)
            and f["id"].startswith("F")
            and f["id"][1:].isdigit()
        ]
        return max([seq, *live, *withdrawn, 0]) + 1

    def mark_answered(self, items: list, resolutions: dict | None = None) -> list:
        """Mark open items answered after a passed assessment. Escalated items stay put.

        `resolutions` optionally records, per item, the finding status
        that earned the answer ("asserted" or "unresolved"): a supported
        statement of uncertainty resolves its question without
        pretending a positive answer.
        """
        marked = []
        for item in items:
            entry = self.data["checklist"].get(item)
            if (
                isinstance(entry, dict)
                and entry.get("status") == "open"
                and item not in marked
            ):
                entry["status"] = "answered"
                if resolutions and item in resolutions:
                    entry["resolution"] = resolutions[item]
                marked.append(item)
        if marked:
            self._save("items_answered", "verification", {"items": marked})
        # A passed assessment completes the items' tasks with the
        # versions it saw — including already-answered items it
        # reassessed, whose stale tasks become current again.
        versions = self.input_versions()
        completed = []
        for item in items:
            entry = self.data["checklist"].get(item)
            if not (isinstance(entry, dict) and entry.get("status") == "answered"):
                continue
            for task in self._tasks_for_item(item):
                if task.get("status") in ("open", "stale"):
                    task["inputs"] = dict(versions)
                    self._transition(task, "completed", "marked answered")
                    completed.append(task["id"])
        if completed:
            self._save("tasks_completed", "verification", {"tasks": completed})
        return marked

    def shared_quotes(self) -> dict:
        """Normalized quotes collected from more than one document.

        Several documents repeating one originating statement are
        potentially dependent sources, not automatically independent
        corroboration. Returns {quote: [fact ids]} for live facts only.
        """
        by_quote: dict[str, list] = {}
        for fact in self.data.get("facts", []):
            if not isinstance(fact, dict):
                continue
            key = document_text.normalize(str(fact.get("quote") or ""))
            by_quote.setdefault(key, []).append(fact)
        shared = {}
        for quote, facts in by_quote.items():
            docs = {f.get("document_id") for f in facts}
            if quote and len(docs) > 1:
                shared[quote] = [f.get("id") for f in facts]
        return shared

    def flag_privileged(self, document_id: str, reason: str) -> dict:
        document_text.get_document(self.root, document_id)
        if self.flagged(document_id):
            return next(
                f
                for f in self.data["flags"]
                if f["document_id"] == document_id and f["kind"] == "privileged"
            )
        flag = {
            "kind": "privileged",
            "document_id": document_id,
            "reason": str(reason),
            "review": "pending",
            "at": now(),
        }
        self.data["flags"].append(flag)
        withdrawn = [
            f for f in self.data["facts"] if f["document_id"] == document_id
        ]
        self.data["facts"] = [
            f for f in self.data["facts"] if f["document_id"] != document_id
        ]
        archive = self.data.setdefault("withdrawn_facts", [])
        withdrawn_ids = []
        for fact in withdrawn:
            withdrawn_ids.append(fact["id"])
            archive.append(
                {
                    **fact,
                    "withdrawn_at": now(),
                    "withdrawn_reason": f"privileged: {reason}",
                }
            )
        affected = []
        for item, entry in self.data["checklist"].items():
            before = list(entry["facts"])
            entry["facts"] = [f for f in entry["facts"] if f not in withdrawn_ids]
            if entry["facts"] != before:
                affected.append(item)
                if entry.get("status") == "answered":
                    # The answer's basis may be gone; re-answering needs
                    # re-assessment. Reopening stays coarse (fail-safe);
                    # the precise impact below says what actually broke.
                    entry["status"] = "open"
        memo = self.data.get("memo") or {}
        impact = {}
        for fact_id in withdrawn_ids:
            trace = trace_fact_impact(self.data, memo, fact_id)
            impact[fact_id] = {
                "item": trace["item"],
                "cited_by": trace["cited_by"],
                "answers": trace["answers"],
            }
        flag["impact"] = impact
        self._save(
            "document_flagged",
            "agent",
            {
                "document_id": document_id,
                "withdrawn_facts": withdrawn_ids,
                "impact": impact,
            },
        )
        if affected:
            self._stale_completed_tasks(
                f"evidence withdrawn ({document_id})", affected
            )
        return flag

    def note_redaction_scan(
        self, document_id: str, finding_count: int, pages: list
    ) -> dict:
        """Record a bad-redaction scan for reviewer sign-off.

        Unlike privilege, a bad redaction excludes nothing: the
        visible content stays usable. The flag warns that the
        transcript may hold text the rectangle was meant to hide.
        Re-scans refresh the pending flag instead of piling on.
        """
        for flag in self.data["flags"]:
            if (
                isinstance(flag, dict)
                and flag.get("kind") == "bad_redaction"
                and flag.get("document_id") == document_id
                and flag.get("review") == "pending"
            ):
                flag["findings"] = int(finding_count)
                flag["pages"] = list(pages)
                flag["at"] = now()
                self._save(
                    "redactions_noted",
                    "agent",
                    {"document_id": document_id,
                     "findings": int(finding_count)},
                )
                return flag
        flag = {
            "kind": "bad_redaction",
            "document_id": document_id,
            "reason": (
                f"{finding_count} rectangle(s) covering live text: "
                "the transcript may hold text the redaction hid"
            ),
            "findings": int(finding_count),
            "pages": list(pages),
            "review": "pending",
            "at": now(),
        }
        self.data["flags"].append(flag)
        self._save(
            "redactions_noted",
            "agent",
            {"document_id": document_id,
             "findings": int(finding_count)},
        )
        return flag

    def add_signature_check(self, result: dict) -> dict:
        record = dict(result, at=now())
        self.data["signature_checks"].append(record)
        if result.get("verdict") in ("inconsistent", "unclear", "unsigned"):
            self.data["flags"].append(
                {
                    "kind": "signature",
                    "document_id": result.get("document_id"),
                    "reason": f"{result.get('verdict')}: {result.get('reason', '')}",
                    "review": "pending",
                    "at": now(),
                }
            )
        self._save(
            "signature_checked",
            "agent",
            {
                "document_id": result.get("document_id"),
                "verdict": result.get("verdict"),
            },
        )
        return record

    def open_request(
        self,
        item: str,
        need: str,
        rule: str,
        visible_ids: list,
        evidence_so_far: list | None = None,
        purpose: str | None = None,
        target: str = "",
        rationale: str = "",
        read_exception: str = "",
    ) -> dict:
        entry = self._item(item)
        if not need or not str(need).strip():
            raise CaseError("need must say exactly what evidence is missing")
        if purpose is not None and purpose not in REQUEST_PURPOSES:
            raise CaseError(
                f"purpose {purpose!r} must be one of: {', '.join(REQUEST_PURPOSES)}"
            )
        request = {
            "id": f"R{len(self.data['requests']) + 1}",
            "item": item,
            "rule": rule,
            "need": str(need),
            "purpose": purpose,
            "target": str(target or ""),
            "rationale": str(rationale or ""),
            "read_exception": str(read_exception or ""),
            "visible_ids": list(visible_ids),
            "evidence_so_far": evidence_so_far or list(self.data["facts"]),
            "status": "open",
            "response": None,
            "at": now(),
        }
        self.data["requests"].append(request)
        entry["request"] = request["id"]
        if entry["status"] == "open":
            entry["status"] = "escalated"
        self._save(
            "request_opened",
            "agent",
            {"request": request["id"], "item": item, "rule": rule},
        )
        self._block_on_request(item, request["id"])
        return request

    def open_requests(self) -> list:
        return [r for r in self.data["requests"] if r["status"] == "open"]

    def contested_items(self) -> list:
        """Items with both supporting and opposing collected facts."""
        by_id = {f["id"]: f for f in self.data["facts"] if isinstance(f, dict)}
        contested = []
        for item, entry in self.data["checklist"].items():
            stances = {
                stance_of(by_id[fid])
                for fid in entry.get("facts", [])
                if fid in by_id
            }
            if "supports" in stances and "opposes" in stances:
                contested.append(item)
        return contested

    def file_memo(self, memo: dict, status: str, addresses: list | None = None) -> dict:
        """File the memo. Refused if a finding cites more than MAX_CITATIONS_PER_FINDING facts.

        A previous filing moves to memo_history (newest last) with the moment it
        was superseded, so revisions stay comparable. `addresses` names
        reviewer comment targets this revision addresses: matching open
        comments on the superseded memo are marked addressed with the
        new revision number; the rest stay open.
        """
        for finding in memo.get("findings") or []:
            citations = finding.get("citations") if isinstance(finding, dict) else None
            if (
                isinstance(citations, list)
                and len(citations) > MAX_CITATIONS_PER_FINDING
            ):
                raise CaseError(
                    f"the finding for {finding.get('item')} cites {len(citations)} facts; "
                    f"cite its {MAX_CITATIONS_PER_FINDING} strongest at most"
                )
        history = self.data.setdefault("memo_history", [])
        prior = self.data.get("memo")
        if prior is not None:
            archived = dict(copy.deepcopy(prior), superseded=now())
            targets = {str(a) for a in (addresses or []) if str(a).strip()}
            review = archived.get("review") or {}
            for comment in review.get("comments") or []:
                if (
                    isinstance(comment, dict)
                    and comment.get("status", "open") == "open"
                    and str(comment.get("target", "")) in targets
                ):
                    comment["status"] = "addressed"
                    comment["addressed_in"] = len(history) + 1
            history.append(archived)
        branches = self.data.get("branches", {})
        record = dict(
            memo,
            status=status,
            filed=now(),
            review=None,
            evidence_version=_evidence_fingerprint(self.data.get("facts", [])),
            branch_states={
                bid: {
                    "issue": b.get("issue"),
                    "regime": b.get("regime"),
                    "status": b.get("status"),
                }
                for bid, b in branches.items()
                if isinstance(b, dict)
            },
            conditions=self.memo_branch_conditions(memo),
        )
        self.data["memo"] = record
        self._save("memo_filed", "agent", {"status": status})
        if status == "unverified":
            # Filed without verification: the run ends here.
            root = self.ensure_root_task()
            root["inputs"] = self.input_versions()
            if self._transition(root, "completed", "memo filed unverified"):
                self._save("tasks_completed", "system", {"tasks": [ROOT_TASK]})
        return record

    # ---- people ----
    def answer_request(self, request_id: str, response: str) -> dict:
        for request in self.data["requests"]:
            if request["id"] == request_id:
                request["status"] = "answered"
                request["response"] = response
                request["answered"] = now()
                self._save("request_answered", "human", {"request": request_id})
                self._unblock_request(request_id)
                return request
        raise CaseError(f"no request {request_id}")

    def review_memo(
        self,
        approve: bool,
        notes: str = "",
        flag_decisions: dict | None = None,
        qualifications: list | None = None,
        comments: list | None = None,
    ) -> dict:
        """Record the reviewer's decision. Qualifications persist with an approval;
        structured comments ({target, text}) go back to the agent on a rejection.
        An approval binds the report content and evidence snapshot reviewed,
        so later changes mark the approval stale instead of silently
        extending it."""
        from quality.records import evidence_fingerprint, memo_hash

        memo = self.data.get("memo")
        if not memo:
            raise CaseError("no memo has been filed")
        clean = []
        for entry in comments or []:
            if isinstance(entry, dict):
                target, text = str(entry.get("target") or "general"), str(
                    entry.get("text") or ""
                )
            else:
                target, text = "general", str(entry)
            if text.strip():
                clean.append(
                    {
                        "target": target.strip() or "general",
                        "text": text,
                        "status": "open",
                    }
                )
        memo["review"] = {
            "decision": "approved" if approve else "rejected",
            "notes": notes,
            "qualifications": [str(q) for q in (qualifications or []) if str(q).strip()],
            "comments": clean,
            "at": now(),
        }
        if approve:
            claims_outcome = None
            for record in self.data.get("memo_checks", []):
                if isinstance(record, dict) and record.get("check") == "claims":
                    claims_outcome = record.get("outcome")
            memo["review"]["snapshot"] = {
                "content": memo_hash(
                    {key: memo.get(key) for key in ("conclusion", "findings", "gaps")}
                ),
                "evidence": evidence_fingerprint(self.data),
                "claims_outcome": claims_outcome,
            }
        memo["status"] = "approved" if approve else "rejected"
        for flag in self.data["flags"]:
            flag["review"] = (flag_decisions or {}).get(
                flag["document_id"], "confirmed" if approve else flag["review"]
            )
        self._save("memo_reviewed", "human", {"decision": memo["status"]})
        root = self.ensure_root_task()
        if approve:
            root["inputs"] = self.input_versions()
            if self._transition(root, "completed", "memo approved"):
                self._save("tasks_completed", "system", {"tasks": [ROOT_TASK]})
        elif self._transition(root, "open", "memo rejected: rework"):
            self._save("task_reopened", "human", {"task": ROOT_TASK})
        return memo

    # ---- artifacts (views over this record; the record stays the one store) ----
    def artifact_open(
        self, title: str = "", template: str = "", source: str = ""
    ) -> dict:
        """Open an artifact instance (one commit authority).

        Audits bind the filed memo; boards bind recorded evidence;
        playbooks bind one contract review, horizon scans one
        matrix, and governance inventories one intake
        (explicit source, or the single source when only
        one exists).
        """
        from . import artifacts

        template = template or artifacts.TEMPLATE_AUDIT
        if template == artifacts.TEMPLATE_AUDIT:
            memo = self.data.get("memo")
            if not memo:
                raise CaseError("no memo has been filed")
            instance = artifacts.open_instance(
                self.data, self.root, memo, title, self.data["run_id"]
            )
        else:
            open_fn = artifacts.handlers(template)["open"]
            instance = open_fn(
                self.data, self.data["run_id"], title, source
            )
        self.data.setdefault("artifacts", {})[instance["id"]] = instance
        self._save("artifact_opened", "human", {"instance": instance["id"]})
        return instance

    def artifact_get(self, instance_id: str) -> dict:
        """One instance by id; unknown ids fail closed."""
        instance = (self.data.get("artifacts") or {}).get(instance_id)
        if not isinstance(instance, dict):
            raise CaseError(f"unknown artifact {instance_id!r}")
        if instance.get("matter", self.data["run_id"]) != self.data["run_id"]:
            raise CaseError(f"artifact {instance_id!r} belongs to another matter")
        return instance

    def artifact_save(
        self, instance: dict, event: str, detail: str = ""
    ) -> dict:
        """Persist a mutated instance with an audit event."""
        self.data.setdefault("artifacts", {})[instance["id"]] = instance
        self._save(event, "human", {"instance": instance["id"], "detail": detail})
        return instance

    # ---- collaboration backbone (C-0) ----
    def _bump_seq(self, key: str) -> int:
        try:
            seq = int(self.data.get(key, 0))
        except (TypeError, ValueError):
            seq = 0
        self.data[key] = seq + 1
        return seq + 1

    @staticmethod
    def _participant(author: dict | None) -> dict:
        """Validate a human/agent identity (docs/collaboration/05)."""
        author = author if isinstance(author, dict) else {}
        kind = author.get("type", "human")
        who = str(author.get("id") or "").strip()
        if kind not in PARTICIPANT_TYPES:
            raise CaseError(f"participant type must be human or agent, got {kind!r}")
        if not who:
            raise CaseError("the participant must be named")
        if kind == "agent":
            if author.get("role"):
                raise CaseError("agents carry an agent_role, not a human role")
            role = author.get("agent_role") or ""
            if role and role not in AGENT_ROLES:
                raise CaseError(f"unknown agent role {role!r}")
            return {"type": kind, "id": who, "agent_role": role}
        role = author.get("role") or DEFAULT_HUMAN_ROLE
        if role not in HUMAN_ROLES:
            raise CaseError(f"unknown human role {role!r}")
        return {"type": kind, "id": who, "role": role}

    @staticmethod
    def _bounded(text: str, limit: int, what: str) -> str:
        text = str(text or "")
        if len(text) > limit:
            raise CaseError(
                f"{what} is too long ({len(text)} of {limit} characters max)"
            )
        return text

    def _collab_endpoint(self, otype: str, oid: str) -> None:
        """Raise unless (otype, oid) names a stored object."""
        if otype not in EDGE_ENDPOINT_TYPES:
            raise CaseError(f"unknown object type {otype!r}")
        if otype == "draft":
            objs = self.data.get("composer_drafts") or []
        elif otype == "artifact":
            if not isinstance((self.data.get("artifacts") or {}).get(oid), dict):
                raise CaseError(f"unknown artifact {oid!r}")
            return
        elif otype == "thread":
            objs = self.data.get("threads") or []
        elif otype == "directive":
            objs = self.data.get("directives") or []
        elif otype == "context":
            objs = self.data.get("context_items") or []
        else:
            if self._find_objection(oid) is None:
                raise CaseError(f"unknown objection {oid!r}")
            return
        if not any(isinstance(o, dict) and o.get("id") == oid for o in objs):
            raise CaseError(f"unknown {otype} {oid!r}")

    def _record_edge(
        self,
        src_type: str,
        src_id: str,
        dst_type: str,
        dst_id: str,
        relation: str,
        author: dict,
    ) -> dict:
        """Append one activity edge without saving (one save per call)."""
        if relation not in EDGE_RELATIONS:
            raise CaseError(f"unknown edge relation {relation!r}")
        self._collab_endpoint(src_type, src_id)
        self._collab_endpoint(dst_type, dst_id)
        edge = {
            "id": f"E{self._bump_seq('edge_seq')}",
            "src": {"type": src_type, "id": src_id},
            "dst": {"type": dst_type, "id": dst_id},
            "relation": relation,
            "author": author,
            "at": now(),
        }
        self.data.setdefault("activity_edges", []).append(edge)
        return edge

    def edge_add(
        self,
        src_type: str,
        src_id: str,
        dst_type: str,
        dst_id: str,
        relation: str,
        author: dict | None = None,
    ) -> dict:
        """Record an explicit activity edge between stored objects."""
        edge = self._record_edge(
            src_type, src_id, dst_type, dst_id, relation,
            self._participant(author),
        )
        self._save("edge_added", edge["author"]["id"], {"edge": edge["id"]})
        return dict(edge)

    def activity_edges(self) -> list:
        """Copies of every activity edge, oldest first."""
        return [dict(e) for e in self.data.get("activity_edges") or []]

    # -- composer drafts (optimistic concurrency, sent is final) --
    def composer_save(
        self,
        title: str,
        body_md: str,
        author: dict | None = None,
        draft_id: str = "",
        base_version: int | None = None,
    ) -> dict:
        """Create a draft, or save a new version of one.

        Pass base_version to detect stale writes: a save whose
        base no longer matches the stored version fails instead
        of overwriting. Sent drafts are immutable; open a new
        draft instead. Every version is kept in history.
        """
        who = self._participant(author)
        title = self._bounded(title, MAX_COLLAB_TITLE, "the title")
        body_md = self._bounded(body_md, MAX_COLLAB_BODY, "the draft")
        if not str(body_md or "").strip():
            raise CaseError("the draft must say something")
        drafts = self.data.setdefault("composer_drafts", [])
        if not draft_id:
            draft = {
                "id": f"DR{self._bump_seq('draft_seq')}",
                "title": str(title or ""),
                "body_md": str(body_md),
                "author": who,
                "version": 1,
                "state": "draft",
                "verification_status": "pending",
                "created_at": now(),
                "updated_at": now(),
                "history": [
                    {"version": 1, "body_md": str(body_md),
                     "author": who, "at": now()}
                ],
            }
            drafts.append(draft)
            self._save("draft_created", who["id"], {"draft": draft["id"]})
            return dict(draft)
        draft = next(
            (d for d in drafts if isinstance(d, dict) and d.get("id") == draft_id),
            None,
        )
        if draft is None:
            raise CaseError(f"unknown draft {draft_id!r}")
        if draft.get("state") == "sent":
            raise CaseError(f"{draft_id} is already sent: open a new draft")
        if base_version is not None and draft.get("version") != base_version:
            raise CaseError(
                f"{draft_id} changed since version {base_version}: "
                f"current version is {draft.get('version')}"
            )
        draft["version"] = _safe_int(draft.get("version"), "draft version") + 1
        draft["body_md"] = str(body_md)
        if title:
            draft["title"] = str(title)
        draft["updated_at"] = now()
        # New content is unverified, whatever the old version was.
        draft["verification_status"] = "pending"
        draft.setdefault("history", []).append(
            {"version": draft["version"], "body_md": str(body_md),
             "author": who, "at": now()}
        )
        self._save(
            "draft_saved", who["id"],
            {"draft": draft_id, "version": draft["version"]},
        )
        return dict(draft)

    def composer_send(self, draft_id: str, author: dict | None = None) -> dict:
        """Mark a draft sent (handed to an agent); sent is final."""
        who = self._participant(author)
        draft = next(
            (d for d in self.data.get("composer_drafts") or []
             if isinstance(d, dict) and d.get("id") == draft_id),
            None,
        )
        if draft is None:
            raise CaseError(f"unknown draft {draft_id!r}")
        if draft.get("state") == "sent":
            raise CaseError(f"{draft_id} is already sent")
        draft["state"] = "sent"
        draft["updated_at"] = now()
        self._save("draft_sent", who["id"], {"draft": draft_id})
        return dict(draft)

    def composer_get(self, draft_id: str) -> dict:
        """One draft with its full version history; unknown ids fail."""
        draft = next(
            (d for d in self.data.get("composer_drafts") or []
             if isinstance(d, dict) and d.get("id") == draft_id),
            None,
        )
        if draft is None:
            raise CaseError(f"unknown draft {draft_id!r}")
        return copy.deepcopy(draft)

    def composer_drafts(self, state: str = "") -> list:
        """Copies of drafts, optionally filtered by state."""
        drafts = [
            dict(d) for d in self.data.get("composer_drafts") or []
            if isinstance(d, dict)
        ]
        if state:
            if state not in DRAFT_STATES:
                raise CaseError(f"unknown draft state {state!r}")
            drafts = [d for d in drafts if d.get("state") == state]
        return drafts

    def _draft(self, draft_id: str) -> dict:
        draft = next(
            (d for d in self.data.get("composer_drafts") or []
             if isinstance(d, dict) and d.get("id") == draft_id),
            None,
        )
        if draft is None:
            raise CaseError(f"unknown draft {draft_id!r}")
        return draft

    def _mark_draft(self, draft_id: str, status: str) -> None:
        """Flip one draft's verification status without saving."""
        self._draft(draft_id)["verification_status"] = status

    def draft_verify(
        self,
        draft_id: str,
        verdict: str,
        by: dict | None = None,
        rationale: str = "",
    ) -> dict:
        """Record a verification verdict on a draft's current content.

        Re-verification clears stale: that is how edited or
        invalidated drafts earn their status back. A challenged
        verdict needs its stated reason. Verification is not
        approval — approvals stay on the existing machinery.
        """
        who = self._participant(by)
        if verdict not in ("passed", "challenged"):
            raise CaseError(f"verdicts are passed or challenged, got {verdict!r}")
        if verdict == "challenged" and not str(rationale or "").strip():
            raise CaseError("a challenged verdict needs its reason")
        draft = self._draft(draft_id)
        draft["verification_status"] = verdict
        draft["verified_by"] = who
        draft["verified_at"] = now()
        draft["verify_rationale"] = str(rationale or "")
        self._save("draft_verified", who["id"], {"draft": draft_id})
        return dict(draft)

    # -- threads (append-only messages, explicit resolve/reopen) --
    @staticmethod
    def _anchor(anchor: dict | None) -> dict:
        anchor = anchor if isinstance(anchor, dict) else {}
        start = anchor.get("start", 0)
        end = anchor.get("end", 0)
        try:
            start, end = int(start), int(end)
        except (TypeError, ValueError):
            raise CaseError("the anchor offsets must be numbers")
        if start < 0 or end < 0 or end < start:
            raise CaseError("the anchor offsets must satisfy 0 <= start <= end")
        return {
            "start": start,
            "end": end,
            "quoted": str(anchor.get("quoted") or ""),
            "context": str(anchor.get("context") or ""),
        }

    def _thread(self, thread_id: str) -> dict:
        thread = next(
            (t for t in self.data.get("threads") or []
             if isinstance(t, dict) and t.get("id") == thread_id),
            None,
        )
        if thread is None:
            raise CaseError(f"unknown thread {thread_id!r}")
        return thread

    def _draft_version_body(self, draft_id: str, version: int) -> str:
        """One draft version's body; unknown versions fail."""
        draft = self._draft(draft_id)
        for snap in draft.get("history") or []:
            if isinstance(snap, dict) and snap.get("version") == version:
                return str(snap.get("body_md") or "")
        raise CaseError(
            f"{draft_id} has no v{version}: "
            f"current version is v{draft.get('version')}"
        )

    @staticmethod
    def _parse_version(version_id: str, what: str) -> int:
        match = re.fullmatch(r"v(\d+)", str(version_id or "").strip())
        if not match:
            raise CaseError(f"{what} must look like v2, got {version_id!r}")
        return int(match.group(1))

    def _check_anchor(
        self, object_type: str, object_id: str, version_id: str, anchor: dict
    ) -> None:
        """Fail closed when a draft anchor does not match its object.

        Version bindings must name a stored version; a quoted
        anchor must equal the bound (or current) text at its
        offsets. Artifacts carry no comparable text here, so
        their anchors record on trust and report unknown drift.
        """
        if object_type == "artifact":
            if version_id:
                versions = (
                    self.data.get("artifacts") or {}
                ).get(object_id, {}).get("versions") or []
                if not isinstance(versions, list) or not versions:
                    raise CaseError(
                        f"{object_id} keeps no versions: leave the thread unbound"
                    )
                number = self._parse_version(version_id, "the artifact version")
                if number < 1 or number > len(versions):
                    raise CaseError(
                        f"{object_id} has no {version_id}: "
                        f"{len(versions)} version(s) stored"
                    )
            return
        current = int(self._draft(object_id).get("version") or 0)
        body = self._draft(object_id).get("body_md") or ""
        if version_id:
            number = self._parse_version(version_id, "the draft version")
            body = self._draft_version_body(object_id, number)
        start, end = anchor.get("start", 0), anchor.get("end", 0)
        quoted = anchor.get("quoted") or ""
        if quoted or end:
            if end > len(body):
                raise CaseError(
                    f"anchor ends at {end} but "
                    f"{version_id or 'the current text'} "
                    f"is {len(body)} characters"
                )
            actual = body[start:end]
            if quoted and actual != quoted:
                raise CaseError(
                    f"quoted text does not match at {start}-{end}: "
                    f"object holds {actual[:100]!r}"
                )

    def _thread_freshness(self, thread: dict) -> dict:
        """version_current / anchor_match (null = unknown)."""
        return thread_freshness_for(self.data, thread)

    def thread_open(
        self,
        object_type: str,
        object_id: str,
        body_md: str,
        author: dict | None = None,
        version_id: str = "",
        anchor: dict | None = None,
        challenge: bool = False,
    ) -> dict:
        """Open a thread on a stored draft or artifact.

        With challenge=True the thread records a challenged
        edge against the object; the disagreement stays
        visible until resolved, never auto-resolved.
        Version bindings and quoted anchors are verified
        against the object; mismatches fail instead of
        recording a thread that points at nothing.
        """
        who = self._participant(author)
        if object_type not in THREAD_OBJECT_TYPES:
            raise CaseError(f"threads attach to drafts or artifacts, got {object_type!r}")
        self._collab_endpoint(object_type, object_id)
        body_md = self._bounded(body_md, MAX_COLLAB_BODY, "the opening message")
        if not str(body_md or "").strip():
            raise CaseError("the opening message must say something")
        checked_anchor = self._anchor(anchor)
        self._check_anchor(object_type, object_id, version_id, checked_anchor)
        thread = {
            "id": f"TH{self._bump_seq('thread_seq')}",
            "object_type": object_type,
            "object_id": object_id,
            "version_id": str(version_id or ""),
            "anchor": checked_anchor,
            "state": "open",
            "messages": [
                {"id": "M1", "author": who, "body_md": str(body_md),
                 "at": now(), "edited_at": ""}
            ],
            "created_at": now(),
        }
        self.data.setdefault("threads", []).append(thread)
        if challenge:
            self._record_edge(
                "thread", thread["id"], object_type, object_id,
                "challenged", who,
            )
            if object_type == "draft" and self._draft(
                    object_id).get("verification_status") != "stale":
                self._mark_draft(object_id, "challenged")
        self._save("thread_opened", who["id"], {"thread": thread["id"]})
        return dict(thread)

    def thread_reply(
        self,
        thread_id: str,
        body_md: str,
        author: dict | None = None,
        challenge: bool = False,
    ) -> dict:
        """Append a reply; resolved threads must be reopened first."""
        who = self._participant(author)
        body_md = self._bounded(body_md, MAX_COLLAB_BODY, "the reply")
        if not str(body_md or "").strip():
            raise CaseError("the reply must say something")
        thread = self._thread(thread_id)
        if thread.get("state") != "open":
            raise CaseError(f"{thread_id} is resolved: reopen it first")
        messages = thread.setdefault("messages", [])
        message = {
            "id": f"M{len(messages) + 1}",
            "author": who,
            "body_md": str(body_md),
            "at": now(),
            "edited_at": "",
        }
        messages.append(message)
        if challenge:
            self._record_edge(
                "thread", thread_id, thread["object_type"],
                thread["object_id"], "challenged", who,
            )
            if thread["object_type"] == "draft" and self._draft(
                    thread["object_id"]).get(
                        "verification_status") != "stale":
                self._mark_draft(thread["object_id"], "challenged")
        self._save("thread_replied", who["id"], {"thread": thread_id})
        return dict(thread)

    def thread_edit_message(
        self,
        thread_id: str,
        message_id: str,
        body_md: str,
        author: dict | None = None,
    ) -> dict:
        """Correct one message; the edit is stamped, never silent.

        Open threads only — resolved threads must be reopened
        first, so a correction cannot slip into closed review.
        """
        who = self._participant(author)
        body_md = self._bounded(body_md, MAX_COLLAB_BODY, "the correction")
        if not str(body_md or "").strip():
            raise CaseError("the corrected message must say something")
        thread = self._thread(thread_id)
        if thread.get("state") != "open":
            raise CaseError(f"{thread_id} is resolved: reopen it first")
        message = next(
            (m for m in thread.get("messages") or []
             if isinstance(m, dict) and m.get("id") == message_id),
            None,
        )
        if message is None:
            raise CaseError(f"{thread_id} has no message {message_id!r}")
        message["body_md"] = str(body_md)
        message["edited_at"] = now()
        message["edited_by"] = who
        self._save("thread_edited", who["id"], {"thread": thread_id})
        return dict(thread)

    def thread_resolve(
        self, thread_id: str, author: dict | None = None, resolution: str = ""
    ) -> dict:
        """Resolve an open thread (the discussion stays readable)."""
        who = self._participant(author)
        thread = self._thread(thread_id)
        if thread.get("state") != "open":
            raise CaseError(f"{thread_id} is already resolved")
        thread["state"] = "resolved"
        thread["resolution"] = str(resolution or "")
        thread["resolved_by"] = who
        self._save("thread_resolved", who["id"], {"thread": thread_id})
        return dict(thread)

    def thread_reopen(
        self, thread_id: str, author: dict | None = None, reason: str = ""
    ) -> dict:
        """Reopen a resolved thread; reopening needs its reason."""
        who = self._participant(author)
        if not str(reason or "").strip():
            raise CaseError("reopening needs the reason it was wrongly closed")
        thread = self._thread(thread_id)
        if thread.get("state") != "resolved":
            raise CaseError(f"{thread_id} is already open")
        thread["state"] = "open"
        self._save("thread_reopened", who["id"], {"thread": thread_id})
        return dict(thread)

    def threads(self, state: str = "") -> list:
        """Copies of threads, optionally filtered by state."""
        found = [
            dict(t) for t in self.data.get("threads") or []
            if isinstance(t, dict)
        ]
        if state:
            if state not in THREAD_STATES:
                raise CaseError(f"unknown thread state {state!r}")
            found = [t for t in found if t.get("state") == state]
        return found

    # -- directives (typed lifecycle, ADR-004) --
    def _directive(self, directive_id: str) -> dict:
        directive = next(
            (d for d in self.data.get("directives") or []
             if isinstance(d, dict) and d.get("id") == directive_id),
            None,
        )
        if directive is None:
            raise CaseError(f"unknown directive {directive_id!r}")
        return directive

    def directive_submit(
        self,
        kind: str,
        body_md: str,
        author: dict | None = None,
        base_refs: list | None = None,
        from_thread: str = "",
    ) -> dict:
        """Submit a directive (proposed). Base refs must exist; a
        from-thread link records a from edge automatically."""
        who = self._participant(author)
        if kind not in DIRECTIVE_KINDS:
            raise CaseError(f"unknown directive kind {kind!r}")
        body_md = self._bounded(body_md, MAX_COLLAB_BODY, "the directive")
        if not str(body_md or "").strip():
            raise CaseError("the directive must say what is wanted")
        refs = []
        for ref in base_refs or []:
            if not isinstance(ref, dict):
                raise CaseError("base refs must be {type, id} objects")
            self._collab_endpoint(ref.get("type", ""), ref.get("id", ""))
            refs.append({"type": ref["type"], "id": ref["id"]})
        directive = {
            "id": f"DIR{self._bump_seq('directive_seq')}",
            "kind": kind,
            "body_md": str(body_md),
            "author": who,
            "base_refs": refs,
            "from_thread": "",
            "state": "proposed",
            "ack_by": {},
            "applied_at": "",
            "invalidation": [],
            "rationale": "",
            "superseded_by": "",
            "created_at": now(),
        }
        self.data.setdefault("directives", []).append(directive)
        if from_thread:
            self._collab_endpoint("thread", from_thread)
            directive["from_thread"] = from_thread
            self._record_edge(
                "directive", directive["id"], "thread", from_thread,
                "from", who,
            )
        self._save("directive_submitted", who["id"], {"directive": directive["id"]})
        return dict(directive)

    def directive_ack(self, directive_id: str, by: dict | None = None) -> dict:
        """Acknowledge a proposed directive (an agent takes it up)."""
        who = self._participant(by)
        if who["type"] != "agent":
            raise CaseError("only an agent can acknowledge a directive")
        directive = self._directive(directive_id)
        if directive.get("state") != "proposed":
            raise CaseError(
                f"{directive_id} is {directive.get('state')}: only proposed work can be acknowledged"
            )
        directive["state"] = "acknowledged"
        directive["ack_by"] = who
        self._save("directive_acknowledged", who["id"], {"directive": directive_id})
        return dict(directive)

    def directive_apply(
        self,
        directive_id: str,
        by: dict | None = None,
        invalidation: list | None = None,
    ) -> dict:
        """Apply an acknowledged directive; invalidated objects are
        named explicitly and each gets an invalidated edge."""
        who = self._participant(by)
        if who["type"] != "agent":
            raise CaseError("only an agent can apply a directive")
        directive = self._directive(directive_id)
        if directive.get("state") != "acknowledged":
            raise CaseError(
                f"{directive_id} is {directive.get('state')}: only acknowledged work can be applied"
            )
        invalidated = []
        for ref in invalidation or []:
            if not isinstance(ref, dict):
                raise CaseError("invalidation entries must be {type, id} objects")
            self._collab_endpoint(ref.get("type", ""), ref.get("id", ""))
            invalidated.append({"type": ref["type"], "id": ref["id"]})
            self._record_edge(
                "directive", directive_id, ref["type"], ref["id"],
                "invalidated", who,
            )
            if ref["type"] == "draft":
                self._mark_draft(ref["id"], "stale")
        directive["state"] = "applied"
        directive["invalidation"] = invalidated
        directive["applied_at"] = now()
        self._save("directive_applied", who["id"], {"directive": directive_id})
        return dict(directive)

    def directive_refuse(
        self, directive_id: str, by: dict | None = None, rationale: str = ""
    ) -> dict:
        """Refuse a directive; refusing needs its stated reason."""
        who = self._participant(by)
        if not str(rationale or "").strip():
            raise CaseError("refusing needs the reason it cannot be done")
        directive = self._directive(directive_id)
        if directive.get("state") not in ("proposed", "acknowledged"):
            raise CaseError(
                f"{directive_id} is {directive.get('state')}: only open work can be refused"
            )
        directive["state"] = "refused"
        directive["rationale"] = str(rationale)
        self._save("directive_refused", who["id"], {"directive": directive_id})
        return dict(directive)

    def directive_supersede(
        self, directive_id: str, by: dict | None = None, superseded_by: str = ""
    ) -> dict:
        """Supersede a directive with a newer one (named, must exist)."""
        who = self._participant(by)
        directive = self._directive(directive_id)
        if directive.get("state") not in ("proposed", "acknowledged", "applied"):
            raise CaseError(
                f"{directive_id} is {directive.get('state')}: only live work can be superseded"
            )
        replacement = self._directive(superseded_by)
        if replacement["id"] == directive_id:
            raise CaseError("a directive cannot supersede itself")
        directive["state"] = "superseded"
        directive["superseded_by"] = replacement["id"]
        self._record_edge(
            "directive", directive_id, "directive", replacement["id"],
            "superseded", who,
        )
        self._save("directive_superseded", who["id"], {"directive": directive_id})
        return dict(directive)

    def pending_directives(self, run_id: str) -> list:
        """Proposed/acknowledged directives not yet steered into this run.

        Delivery is once per run: a resume re-injects only
        directives submitted (or never delivered) since. The
        lifecycle still advances through ack/apply — injection
        delivers the message, it never applies the work.
        """
        return [
            dict(d)
            for d in self.data.get("directives") or []
            if isinstance(d, dict)
            and d.get("state") in ("proposed", "acknowledged")
            and run_id not in (d.get("injected_runs") or [])
        ]

    def mark_directives_injected(self, directive_ids: list, run_id: str) -> list:
        """Record delivery of directives into a run (unknown ids fail)."""
        if not directive_ids:
            return []
        marked = []
        for directive_id in directive_ids:
            directive = self._directive(directive_id)
            runs = directive.setdefault("injected_runs", [])
            if run_id not in runs:
                runs.append(run_id)
            marked.append(directive["id"])
        self._save(
            "directives_injected", "loop",
            {"directives": marked, "run": run_id},
        )
        return marked

    def directives(self, state: str = "") -> list:
        """Copies of directives, optionally filtered by state."""
        found = [
            dict(d) for d in self.data.get("directives") or []
            if isinstance(d, dict)
        ]
        if state:
            if state not in DIRECTIVE_STATES:
                raise CaseError(f"unknown directive state {state!r}")
            found = [d for d in found if d.get("state") == state]
        return found

    # -- context items (quarantined until a human admits them) --
    def context_add(
        self,
        source: str,
        author: dict | None = None,
        body_md: str = "",
        file_ref: str = "",
    ) -> dict:
        """Stage context from the composer, an upload, or a thread.

        New context is quarantined: it must be admitted by a
        named human before anything may treat it as evidence.
        """
        who = self._participant(author)
        if source not in CONTEXT_SOURCES:
            raise CaseError(f"unknown context source {source!r}")
        body = self._bounded(body_md, MAX_COLLAB_BODY, "the context").strip()
        ref = str(file_ref or "").strip()
        if bool(body) == bool(ref):
            raise CaseError("context needs exactly one of body text or a file reference")
        item = {
            "id": f"CI{self._bump_seq('context_seq')}",
            "source": source,
            "body_md": body,
            "file_ref": ref,
            "author": who,
            "state": "quarantined",
            "admitted_by": {},
            "created_at": now(),
        }
        self.data.setdefault("context_items", []).append(item)
        self._save("context_staged", who["id"], {"context": item["id"]})
        return dict(item)

    def context_admit(self, item_id: str, by: dict | None = None) -> dict:
        """Admit quarantined context (an attorney or admin vouches for it)."""
        who = self._participant(by)
        if who["type"] != "human" or who["role"] not in ADMIT_ROLES:
            raise CaseError("only an attorney or admin can admit quarantined context")
        item = next(
            (c for c in self.data.get("context_items") or []
             if isinstance(c, dict) and c.get("id") == item_id),
            None,
        )
        if item is None:
            raise CaseError(f"unknown context item {item_id!r}")
        if item.get("state") != "quarantined":
            raise CaseError(f"{item_id} is already {item.get('state')}")
        item["state"] = "admitted"
        item["admitted_by"] = who
        self._save("context_admitted", who["id"], {"context": item_id})
        return dict(item)

    def context_reject(
        self, item_id: str, by: dict | None = None, reason: str = ""
    ) -> dict:
        """Reject quarantined context; rejecting needs its reason."""
        who = self._participant(by)
        if not str(reason or "").strip():
            raise CaseError("rejecting needs the reason it is unusable")
        item = next(
            (c for c in self.data.get("context_items") or []
             if isinstance(c, dict) and c.get("id") == item_id),
            None,
        )
        if item is None:
            raise CaseError(f"unknown context item {item_id!r}")
        if item.get("state") != "quarantined":
            raise CaseError(f"{item_id} is already {item.get('state')}")
        item["state"] = "rejected"
        self._save("context_rejected", who["id"], {"context": item_id})
        return dict(item)

    def context_items(self, state: str = "") -> list:
        """Copies of context items, optionally filtered by state."""
        found = [
            dict(c) for c in self.data.get("context_items") or []
            if isinstance(c, dict)
        ]
        if state:
            if state not in CONTEXT_STATES:
                raise CaseError(f"unknown context state {state!r}")
            found = [c for c in found if c.get("state") == state]
        return found

    def collab_summary(self) -> dict:
        """Display-ready collaboration state (no recompute in the UI)."""
        return {
            "drafts": [
                {
                    "id": d.get("id"),
                    "title": d.get("title", ""),
                    "body_md": d.get("body_md", ""),
                    "state": d.get("state"),
                    "verification_status": d.get(
                        "verification_status", "pending"),
                    "version": d.get("version"),
                    "author": d.get("author", {}),
                    "updated_at": d.get("updated_at", ""),
                    "versions": len(d.get("history") or []),
                }
                for d in self.data.get("composer_drafts") or []
                if isinstance(d, dict)
            ],
            "threads": [
                {
                    "id": t.get("id"),
                    "object_type": t.get("object_type"),
                    "object_id": t.get("object_id"),
                    "version_id": t.get("version_id", ""),
                    "anchor": t.get("anchor", {}),
                    **self._thread_freshness(t),
                    "state": t.get("state"),
                    "messages": [
                        {
                            "id": m.get("id"),
                            "author": m.get("author", {}),
                            "body_md": m.get("body_md", ""),
                            "at": m.get("at", ""),
                            "edited_at": m.get("edited_at", ""),
                        }
                        for m in t.get("messages") or []
                        if isinstance(m, dict)
                    ],
                }
                for t in self.data.get("threads") or []
                if isinstance(t, dict)
            ],
            "directives": [
                {
                    "id": d.get("id"),
                    "kind": d.get("kind"),
                    "state": d.get("state"),
                    "author": d.get("author", {}),
                    "from_thread": d.get("from_thread", ""),
                    "superseded_by": d.get("superseded_by", ""),
                }
                for d in self.data.get("directives") or []
                if isinstance(d, dict)
            ],
            "context": [
                {
                    "id": c.get("id"),
                    "source": c.get("source"),
                    "state": c.get("state"),
                    "author": c.get("author", {}),
                }
                for c in self.data.get("context_items") or []
                if isinstance(c, dict)
            ],
            "edges": len(self.data.get("activity_edges") or []),
        }

    # ---- reads ----
    def summary(self) -> dict:
        by_id = {f["id"]: f for f in self.data["facts"]}
        contested = set(self.contested_items())
        checks = self.data.get("memo_checks", [])
        return {
            "checklist": {
                k: {
                    "status": effective_status(v, checks),
                    "facts": len(v["facts"]),
                    "supported": any(
                        stance_of(by_id.get(fid, {})) == "supports"
                        for fid in v["facts"]
                    ),
                    "contested": k in contested,
                    "request": v["request"],
                }
                for k, v in self.data["checklist"].items()
            },
            "facts": len(self.data["facts"]),
            "flags": [
                {"kind": f["kind"], "document_id": f["document_id"]}
                for f in self.data["flags"]
            ],
            "open_requests": [r["id"] for r in self.open_requests()],
            "memo": (self.data["memo"] or {}).get("status"),
            "shared_quotes": self.shared_quotes(),
        }
