"""playbook deviation & exception queue (one artifact slice; split from artifacts.py, behavior unchanged)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_kit import document_text
from agent_kit.case_record import CaseError
from agent_kit.progress import now

from .kernel import (
    TEMPLATE_AUDIT,
    TEMPLATE_BOARD,
    TEMPLATE_CAL,
    TEMPLATE_CT,
    TEMPLATE_DL,
    TEMPLATE_DR,
    TEMPLATE_EC,
    TEMPLATE_GV,
    TEMPLATE_HZ,
    TEMPLATE_IN,
    TEMPLATE_JX,
    TEMPLATE_PB,
    TEMPLATE_PT,
    TEMPLATE_QA,
    TEMPLATE_SP,
    TEMPLATE_TS,
    TEMPLATE_AUDIT_VERSION,
    TEMPLATE_BOARD_VERSION,
    TEMPLATE_JX_VERSION,
    TEMPLATE_CT_VERSION,
    TEMPLATE_PB_VERSION,
    TEMPLATE_CAL_VERSION,
    TEMPLATE_IN_VERSION,
    TEMPLATE_DL_VERSION,
    TEMPLATE_DR_VERSION,
    TEMPLATE_HZ_VERSION,
    TEMPLATE_GV_VERSION,
    TEMPLATE_EC_VERSION,
    TEMPLATE_TS_VERSION,
    TEMPLATE_PT_VERSION,
    TEMPLATE_QA_VERSION,
    TEMPLATE_SP_VERSION,
    _blank_review,
    _excerpt,
    _flagged,
    _inbox_shas,
    _next_instance_id,
    _stable,
    _structural_outcome,
    audit_citation,
    register,
)


PB_SEVERITY = ("info", "standard", "critical")
PB_VERDICTS = ("undecided", "conforms", "deviates", "waived")


def _pb_source(data: dict, source_id: str) -> dict:
    source = (data.get("artifacts", {}) or {}).get(source_id)
    if not isinstance(source, dict):
        raise CaseError(f"unknown source instance {source_id!r}")
    if source.get("template") != TEMPLATE_CT:
        raise CaseError(
            f"{source_id} is a {source.get('template')}, not a contract review"
        )
    return source


def open_pb_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a playbook over one contract review (pure; caller saves).

    Needs a contract-review instance in the same matter. An
    explicit source wins; otherwise the single contract review
    is used, and several require naming one.
    """
    contracts = [
        key
        for key, entry in (data.get("artifacts", {}) or {}).items()
        if isinstance(entry, dict) and entry.get("template") == TEMPLATE_CT
    ]
    if source_id:
        _pb_source(data, source_id)
    elif len(contracts) == 1:
        source_id = contracts[0]
    elif not contracts:
        raise CaseError("the playbook needs a contract-review instance")
    else:
        raise CaseError(
            "several contract reviews exist; name one with --source"
        )
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_PB,
        "template_version": TEMPLATE_PB_VERSION,
        "matter": run_id,
        "title": title or "Playbook Deviation & Exception Queue",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "source_instance": source_id,
        "rules": [],
        "deviations": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": source_id}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "rules": [], "deviations": []}
    )
    return instance


def pb_get_rule(instance: dict, rule_id: str) -> dict:
    rule = next(
        (r for r in instance.get("rules") or [] if r.get("rule_id") == rule_id),
        None,
    )
    if rule is None:
        raise CaseError(f"unknown rule {rule_id!r}")
    return rule


def pb_get_deviation(instance: dict, dev_id: str) -> dict:
    deviation = next(
        (d for d in instance.get("deviations") or [] if d.get("dev_id") == dev_id),
        None,
    )
    if deviation is None:
        raise CaseError(f"unknown deviation {dev_id!r}")
    return deviation


def _pb_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "rules": copy.deepcopy(instance.get("rules")),
            "deviations": copy.deepcopy(instance.get("deviations")),
        }
    )


def _pb_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_rule_id(instance: dict) -> str:
    taken = {rule.get("rule_id", "") for rule in instance.get("rules") or []}
    seq = 1
    while f"R{seq}" in taken:
        seq += 1
    return f"R{seq}"


def _next_dev_id(instance: dict) -> str:
    taken = {d.get("dev_id", "") for d in instance.get("deviations") or []}
    seq = 1
    while f"D{seq}" in taken:
        seq += 1
    return f"D{seq}"


def add_rule(
    instance: dict, topic: str, position: str, severity: str
) -> dict:
    """Add one reviewer-written playbook position."""
    if not str(topic or "").strip():
        raise CaseError("a rule needs its topic")
    if not str(position or "").strip():
        raise CaseError("a rule needs its expected position")
    if severity not in PB_SEVERITY:
        raise CaseError(f"severity must be one of: {', '.join(PB_SEVERITY)}")
    rule = {
        "rule_id": _next_rule_id(instance),
        "topic": str(topic),
        "position": str(position),
        "position_version": 1,
        "severity": severity,
        "retracted": False,
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("rules", []).append(rule)
    _pb_version(instance, f"added {rule['rule_id']}")
    _pb_touch(instance, "rule_added", rule["rule_id"])
    return rule


def set_rule(instance: dict, rule_id: str, **fields: Any) -> dict:
    """Revise a rule's topic, position, or severity.

    A new position bumps the position version, which stales
    every deviation assessed against the old wording.
    """
    rule = pb_get_rule(instance, rule_id)
    if rule.get("retracted"):
        raise CaseError(f"{rule_id} is retracted")
    changed = []
    for key in ("topic", "severity"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if key == "severity" and value not in PB_SEVERITY:
                raise CaseError(f"severity must be one of: {', '.join(PB_SEVERITY)}")
            if value != rule.get(key):
                rule[key] = value
                changed.append(key)
    if "position" in fields and fields["position"] is not None:
        value = str(fields["position"])
        if not value.strip():
            raise CaseError("a rule needs its expected position")
        if value != rule.get("position"):
            rule["position"] = value
            rule["position_version"] = int(rule.get("position_version", 1)) + 1
            changed.append("position")
    if not changed:
        return rule
    rule["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _pb_version(instance, f"set {rule_id}")
    _pb_touch(instance, "rule_set", rule_id)
    return rule


def retract_rule(instance: dict, rule_id: str, reason: str) -> dict:
    """Retire a rule with its reason; deviations stay visible."""
    rule = pb_get_rule(instance, rule_id)
    if not str(reason or "").strip():
        raise CaseError("retracting a rule needs its reason")
    rule["retracted"] = True
    rule["history"].append({"at": now(), "change": "retracted", "detail": str(reason)})
    _pb_version(instance, f"retracted {rule_id}")
    _pb_touch(instance, "rule_retracted", rule_id)
    return rule


def _clause_pin(clause: dict) -> str:
    return _stable(
        {
            "quote": clause.get("quote"),
            "heading": clause.get("heading"),
            "effect": clause.get("effect"),
            "risk": clause.get("risk"),
            "risk_note": clause.get("risk_note"),
            "review_status": clause.get("review_status"),
            "retracted": clause.get("retracted"),
        }
    )


def _pb_clause(data: dict, instance: dict, clause_id: str) -> dict | None:
    source = _pb_source(data, instance.get("source_instance") or "")
    return next(
        (
            c
            for c in source.get("clauses") or []
            if c.get("clause_id") == clause_id
        ),
        None,
    )


def assess(
    instance: dict,
    data: dict,
    clause_id: str,
    rule_id: str,
    verdict: str,
    rationale: str = "",
) -> dict:
    """Assess one clause against one rule (reviewer judgment).

    The first assessment opens a deviation; a later one
    supersedes it with fresh pins. Either side's later
    revision stales the verdict until it is re-assessed.
    """
    clause = _pb_clause(data, instance, clause_id)
    if clause is None:
        raise CaseError(
            f"clause {clause_id!r} is not in {instance.get('source_instance')}"
        )
    rule = pb_get_rule(instance, rule_id)
    if rule.get("retracted"):
        raise CaseError(f"{rule_id} is retracted")
    if verdict not in PB_VERDICTS:
        raise CaseError(f"verdict must be one of: {', '.join(PB_VERDICTS)}")
    deviation = next(
        (
            d
            for d in instance.get("deviations") or []
            if d.get("clause_id") == clause_id and d.get("rule_id") == rule_id
        ),
        None,
    )
    if deviation is None:
        deviation = {
            "dev_id": _next_dev_id(instance),
            "clause_id": clause_id,
            "rule_id": rule_id,
            "history": [],
        }
        instance.setdefault("deviations", []).append(deviation)
        change = "assessed"
    else:
        change = "re-assessed"
    deviation["verdict"] = verdict
    deviation["rationale"] = str(rationale or "")
    deviation["clause_pin"] = _clause_pin(clause)
    deviation["rule_position_version"] = rule.get("position_version", 1)
    deviation["history"].append(
        {"at": now(), "change": change, "detail": verdict}
    )
    _pb_version(instance, f"{change} {deviation['dev_id']}")
    _pb_touch(instance, "deviation_assessed", deviation["dev_id"])
    return deviation


def deviation_status(instance: dict, data: dict, deviation: dict) -> dict:
    """Whether a deviation's inputs changed or vanished.

    Retracted rules and clauses orphan the deviation: it
    leaves the queue but stays visible with its reason.
    Revisions stale it: the verdict stands until re-assessed.
    """
    reasons = []
    orphaned = False
    rule = next(
        (
            r
            for r in instance.get("rules") or []
            if r.get("rule_id") == deviation.get("rule_id")
        ),
        None,
    )
    clause = _pb_clause(data, instance, deviation.get("clause_id") or "")
    if rule is None or rule.get("retracted"):
        reasons.append("the rule was retracted")
        orphaned = True
    elif rule.get("position_version", 1) != deviation.get("rule_position_version"):
        reasons.append("the rule was revised since assessment")
    if clause is None or clause.get("retracted"):
        reasons.append("the clause was retracted")
        orphaned = True
    elif _clause_pin(clause) != deviation.get("clause_pin"):
        reasons.append("the clause was revised since assessment")
    if not orphaned and not str(deviation.get("rationale") or "").strip():
        reasons.append("no rationale recorded")
    return {
        "stale": bool(reasons) and not orphaned,
        "orphaned": orphaned,
        "reasons": reasons,
    }


def pb_exceptions(instance: dict, data: dict) -> list[dict]:
    """Deviating or undecided items awaiting decision.

    Conforms and waivers are decided and stay out; orphaned
    deviations are visible elsewhere, not queued. Critical
    severity sorts first.
    """
    order = {"critical": 0, "standard": 1, "info": 2}
    queue = []
    for deviation in instance.get("deviations") or []:
        status = deviation_status(instance, data, deviation)
        if status["orphaned"]:
            continue
        if deviation.get("verdict") not in ("deviates", "undecided"):
            continue
        rule = pb_get_rule(instance, deviation.get("rule_id") or "")
        queue.append(
            {
                "dev_id": deviation.get("dev_id"),
                "clause_id": deviation.get("clause_id"),
                "rule_id": deviation.get("rule_id"),
                "topic": rule.get("topic"),
                "severity": rule.get("severity"),
                "verdict": deviation.get("verdict"),
                "stale": status["stale"],
                "reasons": status["reasons"],
            }
        )
    queue.sort(
        key=lambda e: (order.get(e["severity"], 9), e["dev_id"] or "")
    )
    return queue


def pb_summary(instance: dict, data: dict) -> dict:
    """Counts plus the exception queue."""
    deviations = instance.get("deviations") or []
    live_rules = [
        r for r in instance.get("rules") or [] if not r.get("retracted")
    ]
    stale = sum(
        1
        for d in deviations
        if deviation_status(instance, data, d)["stale"]
    )
    orphaned = sum(
        1
        for d in deviations
        if deviation_status(instance, data, d)["orphaned"]
    )
    return {
        "rules": len(live_rules),
        "deviations": len(deviations),
        "stale": stale,
        "orphaned": orphaned,
        "exceptions": pb_exceptions(instance, data),
    }


def pb_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a playbook review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "rules": instance.get("rules"),
                "deviations": instance.get("deviations"),
                "source": instance.get("source_instance"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def pb_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two playbook versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    changes = []
    before_rules = {r.get("rule_id"): r for r in versions[old].get("rules") or []}
    after_rules = {r.get("rule_id"): r for r in versions[new].get("rules") or []}
    for rule_id in sorted(set(before_rules) | set(after_rules)):
        if rule_id not in before_rules:
            changes.append({"rule_id": rule_id, "change": "added"})
            continue
        if rule_id not in after_rules:
            changes.append({"rule_id": rule_id, "change": "removed"})
            continue
        first, second = before_rules[rule_id], after_rules[rule_id]
        for key in ("topic", "position", "severity", "retracted"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "rule_id": rule_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_dev = {d.get("dev_id"): d for d in versions[old].get("deviations") or []}
    after_dev = {d.get("dev_id"): d for d in versions[new].get("deviations") or []}
    for dev_id in sorted(set(before_dev) | set(after_dev)):
        if dev_id not in before_dev:
            changes.append({"dev_id": dev_id, "change": "added"})
            continue
        if dev_id not in after_dev:
            changes.append({"dev_id": dev_id, "change": "removed"})
            continue
        first, second = before_dev[dev_id], after_dev[dev_id]
        for key in ("verdict", "rationale"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "dev_id": dev_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def pb_export(
    instance: dict, data: dict, version: int | None = None
) -> dict:
    """Versioned playbook report with queue, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        rules = instance.get("rules") or []
        deviations = instance.get("deviations") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        rules = match.get("rules") or []
        deviations = match.get("deviations") or []
        historical = True
    out_rules = [
        {
            "rule_id": r.get("rule_id"),
            "topic": r.get("topic"),
            "position": r.get("position"),
            "severity": r.get("severity"),
            "retracted": bool(r.get("retracted")),
        }
        for r in rules
    ]
    out_deviations = []
    for deviation in deviations:
        limitations = []
        if not historical:
            status = deviation_status(instance, data, deviation)
            limitations.extend(status["reasons"])
            if status["orphaned"]:
                limitations.append("orphaned: out of the queue")
        else:
            rule = next(
                (r for r in rules if r.get("rule_id") == deviation.get("rule_id")),
                {},
            )
            if (rule or {}).get("retracted"):
                limitations.append("the rule was retracted")
        out_deviations.append(
            {
                "dev_id": deviation.get("dev_id"),
                "clause_id": deviation.get("clause_id"),
                "rule_id": deviation.get("rule_id"),
                "verdict": deviation.get("verdict"),
                "rationale": deviation.get("rationale"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else pb_summary(instance, data)
    review = instance.get("review") or {}
    return {
        "template": instance.get("template"),
        "template_version": instance.get("template_version"),
        "instance": instance.get("id"),
        "matter": instance.get("matter"),
        "title": instance.get("title"),
        "source_instance": instance.get("source_instance"),
        "exported_version": version or (versions[-1].get("v") if versions else 1),
        "historical": historical,
        "evidence_fingerprint": evidence_fingerprint(data) if not historical else None,
        "summary": summary,
        "rules": out_rules,
        "deviations": out_deviations,
        "review": {
            "decision": review.get("decision"),
            "notes": review.get("notes"),
            "qualifications": review.get("qualifications"),
            "at": review.get("at"),
            "snapshot": review.get("snapshot"),
            "scope": (
                "the reviewed snapshot only; later revisions need a new review"
                if review.get("decision") == "approved"
                else "no approval recorded"
            ),
        },
        "limits": [
            "synthetic demonstration: positions and verdicts are "
            "reviewer judgment, not legal advice",
            "a conforms verdict records the reviewer's call against "
            "one pinned rule wording, never automatic compliance",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_PB,
    snapshot=pb_snapshot,
    open=open_pb_instance,
    diff=pb_diff,
    export=pb_export,
    export_takes_root=False,
    count_key="deviations",
    review_keys=('rules', 'deviations'),
)

__all__ = ['PB_SEVERITY', 'PB_VERDICTS', '_pb_source', 'open_pb_instance', 'pb_get_rule', 'pb_get_deviation', '_pb_version', '_pb_touch', '_next_rule_id', '_next_dev_id', 'add_rule', 'set_rule', 'retract_rule', '_clause_pin', '_pb_clause', 'assess', 'deviation_status', 'pb_exceptions', 'pb_summary', 'pb_snapshot', 'pb_diff', 'pb_export']
