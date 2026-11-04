"""First-principles legal reasoning: deterministic planning and gating.

This module makes the agent's default method explicit and checkable:
decompose the problem into foundational questions, answer each one
against rules and evidence, apply the controlling rules element by
element, challenge the result adversarially, and disclose what stays
unresolved — before filing.

Pure functions only: no model calls, no I/O, no wall-clock reads.
The tools in `agent_kit/tools/` validate through here before
committing; `verification/verify_reasoning.py` gates filing through
`run_gate`. Everything here runs offline.
"""

from __future__ import annotations

import re

from agent_kit.compute import ComputeError, replay
from agent_kit.sufficiency import assess as _assess_sufficiency
from agent_kit.textnorm import NORMALIZER_VERSION
from agent_kit.textnorm import normalize as _normalize_text

GATE_VERSION = "reasoning-v1"

#: Normalizer the citation/fact join key is built against (closeout
#: §5). Pinned by test: a textnorm bump must consciously update the
#: join sites (here, verify_memo, audit) instead of silently
#: changing what counts as a match.
QUOTE_KEY_NORMALIZER = NORMALIZER_VERSION

# ---- task types (§10: same principles, adapted per task) ---------------------

TASK_TYPES = (
    "investigation",
    "research",
    "contract",
    "litigation",
    "drafting",
    "risk",
    "strategy",
)

TASK_GUIDANCE = {
    "investigation": (
        "Establish what happened from the documents: objective, facts, "
        "evidence for and against, unknowns, and the available action."
    ),
    "research": (
        "Identify the issue, jurisdiction, controlling rule, relevant "
        "and contrary authorities, and remaining research gaps."
    ),
    "contract": (
        "Analyze operative language, definitions, conditions, "
        "obligations, exceptions, termination, remedies, ambiguities, "
        "and how clauses interact."
    ),
    "litigation": (
        "Analyze claims, elements, defenses, burdens, evidence, "
        "procedure, deadlines, remedies, opposing arguments, and options."
    ),
    "drafting": (
        "Identify the objective, legal requirements, audience, operative "
        "provisions, factual support, ambiguities, adverse readings, and "
        "consequences of the wording."
    ),
    "risk": (
        "Identify obligations, plausible scenarios, likelihood and "
        "severity of consequences, mitigations, uncertainties, and "
        "residual risks."
    ),
    "strategy": (
        "Compare options against objectives, legal constraints, costs, "
        "timing, evidence, practical consequences, and downside exposure."
    ),
}

# ---- foundational question classes (§3) --------------------------------------

QUESTION_CLASSES = (
    "objective",
    "jurisdiction",
    "governing_law",
    "law_status",
    "claim",
    "elements",
    "facts",
    "evidence",
    "deadlines",
    "exceptions",
    "adverse",
    "unknowns",
    "action",
)

QUESTION_PROMPTS = {
    "objective": "What precise question must be answered, and what outcome is sought?",
    "jurisdiction": "Which jurisdiction, court, tribunal, or regulator is relevant?",
    "governing_law": "Which law governs, and why does it apply to this issue?",
    "law_status": "What is that law's current status (in force, amended, repealed)?",
    "claim": "What claim, defense, right, obligation, standard, or remedy is at issue?",
    "elements": "What elements, conditions, or factors must be satisfied?",
    "facts": "Which material facts are established, alleged, disputed, or missing?",
    "evidence": "What evidence supports or contradicts each material fact?",
    "deadlines": "Which deadlines, limitation periods, or procedural rules apply?",
    "exceptions": "What exceptions, defenses, or alternative readings could change the result?",
    "adverse": "What adverse authority or counterargument must be addressed?",
    "unknowns": "What further information could materially change the conclusion?",
    "action": "What action is legally available, sensible, and proportionate to the risks?",
}

#: Smallest sufficient set per task: required classes. Classes outside
#: the set may still be answered when material; classes inside it must
#: be answered or explicitly marked unresolved with a resolution path.
REQUIRED_CLASSES: dict[str, tuple[str, ...]] = {
    "investigation": ("objective", "facts", "evidence", "adverse", "unknowns", "action"),
    "research": (
        "objective",
        "jurisdiction",
        "governing_law",
        "law_status",
        "claim",
        "adverse",
        "unknowns",
    ),
    "contract": (
        "objective",
        "claim",
        "elements",
        "facts",
        "evidence",
        "exceptions",
        "unknowns",
        "action",
    ),
    "litigation": (
        "objective",
        "jurisdiction",
        "governing_law",
        "claim",
        "elements",
        "facts",
        "evidence",
        "deadlines",
        "exceptions",
        "adverse",
        "unknowns",
        "action",
    ),
    "drafting": (
        "objective",
        "jurisdiction",
        "governing_law",
        "claim",
        "elements",
        "exceptions",
        "unknowns",
        "action",
    ),
    "risk": ("objective", "governing_law", "claim", "exceptions", "unknowns", "action"),
    "strategy": (
        "objective",
        "jurisdiction",
        "governing_law",
        "claim",
        "adverse",
        "unknowns",
        "action",
    ),
}

ANSWER_STATUSES = ("answered", "unresolved")

#: Per-question answer fields (§4 A–G): question, governing rule/source,
#: evidence, answer, basis, contrary/uncertainty, effect on conclusion.
ANSWER_FIELDS = (
    "question",
    "rule_or_source",
    "evidence",
    "answer",
    "basis",
    "contrary",
    "effect",
)

# ---- evidence discipline (§8) -------------------------------------------------

EVIDENCE_LABELS = (
    "established",
    "alleged",
    "supported",
    "inference",
    "assumption",
    "disputed",
    "missing",
)

EVIDENCE_GUIDANCE = {
    "established": "Verified against a source the record accepts as proving it.",
    "alleged": "Asserted by a party or witness; not yet proven.",
    "supported": "Backed by recorded evidence, but not conclusive alone.",
    "inference": "A reasoned step from evidence; record why it follows.",
    "assumption": "Taken as true without proof; disclose it and its effect.",
    "disputed": "Contradicted by other evidence; explain the conflict.",
    "missing": "Needed but absent; name what would supply it.",
}

# ---- element-by-element analysis (§5) ----------------------------------------

ELEMENT_FIELDS = (
    "test",
    "element",
    "rule",
    "source",
    "facts_evidence",
    "application",
    "supporting",
    "opposing",
    "missing",
    "finding",
    "effect",
)

# ---- adversarial review (§7) --------------------------------------------------

ADVERSARIAL_FIELDS = (
    "strongest_against",
    "fact_risks",
    "adverse_authority",
    "overlooked",
    "opposing_counsel",
    "judge_needs",
    "alternative_theory",
    "reversing_fact",
)

ADVERSARIAL_PROMPTS = {
    "strongest_against": "What is the strongest argument against this conclusion?",
    "fact_risks": "Which material facts might be wrong, incomplete, or read differently?",
    "adverse_authority": "What binding authority might undermine the analysis?",
    "overlooked": "What exceptions, defenses, or procedural barriers were overlooked?",
    "opposing_counsel": "What would opposing counsel argue?",
    "judge_needs": "What evidence would a skeptical decision-maker require?",
    "alternative_theory": "What alternative legal theory could produce a different result?",
    "reversing_fact": "What new fact would most likely reverse the conclusion?",
}

# ---- uncertainty (§9) ---------------------------------------------------------

#: Narrow patterns for artificial numeric confidence. Deliberately
#: tight: amounts, dates, counts, and proportions in evidence must
#: never match — only confidence language with a number.
CONFIDENCE_PATTERNS = (
    re.compile(r"\b\d{1,3}\s*%\s*(confident|certain|likely|sure)\b", re.I),
    re.compile(r"\bconfiden(ce|t)\b[^.\n]{0,40}\b\d{1,3}\s*%", re.I),
    re.compile(r"\bconfiden(ce|t)\s*(score|level)?\s*[:=]\s*0?\.\d+", re.I),
    re.compile(r"\b\d{1,3}%\s*probability\b", re.I),
    re.compile(r"\bprobability\s+of\s+[^.\n]{1,60}?\s+is\s+\d{1,3}\s*%", re.I),
)


def detect_numeric_confidence(text: str) -> list[str]:
    """Artificial numeric-confidence phrases in free text, else []."""
    found = []
    for pattern in CONFIDENCE_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            found.append(" ".join(match.group(0).split()))
    return found


# ---- planning -----------------------------------------------------------------

def plan_questions(
    task_type: str,
    matter: dict | None = None,
    checklist: dict | None = None,
) -> list[dict]:
    """The smallest sufficient foundational-question set for a task.

    Pure function of its inputs: required classes for the task type,
    each with its prompt and why it is material. Matter context
    narrows the wording (a known forum focuses the jurisdiction
    question); checklist items suggest issue links. Raises ValueError
    for an unknown task type.
    """
    if task_type not in REQUIRED_CLASSES:
        raise ValueError(
            f"unknown task type {task_type!r}; "
            f"known types: {', '.join(TASK_TYPES)}"
        )
    fields = (matter or {}).get("fields", {}) if isinstance(matter, dict) else {}
    values = {
        name: entry.get("value", "")
        for name, entry in fields.items()
        if isinstance(entry, dict)
    }
    items = list((checklist or {})) if isinstance(checklist, dict) else []
    planned = []
    for qclass in REQUIRED_CLASSES[task_type]:
        prompt = QUESTION_PROMPTS[qclass]
        if qclass == "jurisdiction" and values.get("forum"):
            prompt += f" (assignment notes forum: {values['forum']})"
        if qclass == "governing_law" and values.get("governing_law"):
            prompt += f" (assignment notes: {values['governing_law']})"
        planned.append(
            {
                "class": qclass,
                "prompt": prompt,
                "why": _why_material(qclass),
                "issue_hint": items[0] if items and qclass in ("facts", "evidence") else "",
            }
        )
    return planned


def _why_material(qclass: str) -> str:
    return {
        "objective": "A misframed question gets a correct answer to the wrong problem.",
        "jurisdiction": "The forum decides which rules and procedures apply.",
        "governing_law": "Conclusions under the wrong regime are conclusions about another case.",
        "law_status": "Repealed or amended text does not govern current events.",
        "claim": "The claim selects the test every later step applies.",
        "elements": "An unanalyzed element is an unproven claim.",
        "facts": "Rules apply to facts; unestablished facts cannot carry a conclusion.",
        "evidence": "Each material fact needs its support and its contrary weighed.",
        "deadlines": "A time-barred claim fails whatever its merits.",
        "exceptions": "An exception can reverse an otherwise sound result.",
        "adverse": "Unaddressed adverse authority is the core appellate risk.",
        "unknowns": "Unknown material facts bound how much the conclusion can claim.",
        "action": "Advice must be lawful, sensible, and proportionate to risk.",
    }[qclass]


# ---- validation (shape-level; tools call these before committing) --------------

def _missing(data: dict, fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if not str(data.get(f, "") or "").strip()]


def validate_answer(answer: dict) -> list[str]:
    """Deterministic problems with one foundational answer, else [].

    Every A–G field must be present and nonempty; the class must be
    registered; the status must be answered or unresolved; an
    unresolved answer must name what would resolve it.
    """
    if not isinstance(answer, dict):
        return ["the answer must be an object"]
    problems = []
    if answer.get("class") not in QUESTION_CLASSES:
        problems.append(
            f"class {answer.get('class')!r} must be one of: "
            + ", ".join(QUESTION_CLASSES)
        )
    for field in _missing(answer, ANSWER_FIELDS):
        problems.append(f"{field} is required: {ANSWER_FIELD_GUIDE[field]}")
    status = answer.get("status", "answered")
    if status not in ANSWER_STATUSES:
        problems.append(
            f"status {status!r} must be one of: {', '.join(ANSWER_STATUSES)}"
        )
    if status == "unresolved" and not str(answer.get("resolution", "") or "").strip():
        problems.append(
            "an unresolved question must name its resolution: "
            "what information or research would resolve it"
        )
    return problems


ANSWER_FIELD_GUIDE = {
    "question": "state the question precisely (§4A)",
    "rule_or_source": "name the governing rule, evidence, or document needed (§4B)",
    "evidence": "what the authoritative sources and tools returned (§4C)",
    "answer": "the answer the available information supports (§4D)",
    "basis": "the legal or factual basis for that answer (§4E)",
    "contrary": "contrary evidence, exceptions, uncertainty, or missing proof (§4F)",
    "effect": "how the answer affects the ultimate conclusion (§4G)",
}


def validate_element(analysis: dict) -> list[str]:
    """Deterministic problems with one element analysis, else []."""
    if not isinstance(analysis, dict):
        return ["the analysis must be an object"]
    problems = []
    for field in _missing(analysis, ELEMENT_FIELDS):
        problems.append(f"{field} is required: {ELEMENT_FIELD_GUIDE[field]}")
    return problems


ELEMENT_FIELD_GUIDE = {
    "test": "the controlling legal test under analysis",
    "element": "which element, requirement, or factor this analyzes",
    "rule": "the governing rule for this element",
    "source": "its authoritative source",
    "facts_evidence": "the relevant facts and evidence",
    "application": "the rule applied to those facts",
    "supporting": "the strongest supporting argument",
    "opposing": "the strongest opposing argument",
    "missing": "missing proof, exceptions, or unresolved issues ('none identified' if so)",
    "finding": "the provisional finding on this element",
    "effect": "its effect on the overall result",
}


def validate_adversarial(review: dict) -> list[str]:
    """Deterministic problems with one adversarial review, else []."""
    if not isinstance(review, dict):
        return ["the review must be an object"]
    problems = []
    for field in _missing(review, ADVERSARIAL_FIELDS):
        problems.append(f"{field} is required: {ADVERSARIAL_PROMPTS[field]}")
    if "revision_needed" not in review or not isinstance(
        review.get("revision_needed"), bool
    ):
        problems.append("revision_needed is required: true or false")
    if not str(review.get("revision", "") or "").strip():
        problems.append(
            "revision is required: what changed, or why no change was needed"
        )
    dropped = review.get("dropped", [])
    if dropped is None:
        dropped = []
    if not isinstance(dropped, list):
        problems.append("dropped must be a list of dismissed objections")
    else:
        for pos, item in enumerate(dropped):
            if not isinstance(item, dict):
                problems.append(f"dropped[{pos}] must be an object")
                continue
            if not str(item.get("objection", "") or "").strip():
                problems.append(
                    f"dropped[{pos}] needs the objection that was raised"
                )
            if not str(item.get("counter_evidence", "") or "").strip():
                problems.append(
                    f"dropped[{pos}] needs the counter-evidence that "
                    "disproved it"
                )
    return problems


# ---- the filing gate -----------------------------------------------------------

def reasoning_mode(case_data: dict) -> bool:
    """Whether the reasoning gate engages for this run.

    Engaged once the run does first-principles work: a task type is
    set or any foundational answer is recorded. Legacy runs — no
    task type, no answers — stay on the memo-rules + claims path.
    """
    data = case_data if isinstance(case_data, dict) else {}
    if str(data.get("task_type", "") or "").strip():
        return True
    return bool(data.get("foundational"))


def run_gate(case_data: dict, memo: dict) -> dict:
    """The deterministic pre-filing reasoning gate.

    Returns {"gate", "mode", "passed", "problems", "advisory"}.
    In legacy mode the gate passes with one advisory note. In
    reasoning mode every problem below fails the filing; advisory
    notes name what still needs human judgment. Structural only:
    it checks that the reasoning exists, is complete, and is
    linked — never that it is correct.
    """
    data = case_data if isinstance(case_data, dict) else {}
    memo = memo if isinstance(memo, dict) else {}
    if not reasoning_mode(data):
        advisory = [
            "reasoning gate not engaged: no task type and no "
            "foundational answers recorded"
        ] + _advise_contested_one_sided(data, memo)
        legacy_findings = [
            f for f in (memo.get("findings") or []) if isinstance(f, dict)
        ]
        filed = data.get("memo")
        review = (
            filed.get("review") if isinstance(filed, dict) else {}
        )
        historical = (
            isinstance(review, dict)
            and review.get("decision") == "approved"
        )
        if data.get("created") and legacy_findings and not historical:
            # Findings with no task identity: nothing assessed this
            # filing, so it fails closed and names identification as
            # the remedy. Runs carrying an operator-approved review
            # keep their identified historical policy (closeout §7.1).
            return {
                "gate": GATE_VERSION,
                "mode": "legacy",
                "passed": False,
                "problems": [
                    "memo carries findings with no task type and no "
                    "foundational answers: identify the task before "
                    "the filing can be assessed"
                ],
                "advisory": advisory,
            }
        return {
            "gate": GATE_VERSION,
            "mode": "legacy",
            "passed": True,
            "problems": [],
            "advisory": advisory,
        }
    problems: list[str] = []
    advisory: list[str] = []
    task_type = str(data.get("task_type", "") or "").strip()
    answers = [a for a in (data.get("foundational") or []) if isinstance(a, dict)]
    elements = [e for e in (data.get("elements") or []) if isinstance(e, dict)]
    reviews = [r for r in (data.get("adversarial") or []) if isinstance(r, dict)]
    findings = [f for f in (memo.get("findings") or []) if isinstance(f, dict)]

    problems.extend(
        _check_required_classes(
            task_type, answers, data.get("assessment_policy")
        )
    )
    problems.extend(_check_unresolved(answers))
    problems.extend(_check_finding_traceability(findings, answers))
    problems.extend(_check_evidence_obligations(data, memo))
    problems.extend(_check_element_coverage(findings, elements))
    problems.extend(_check_adversarial(reviews))
    problems.extend(_check_uncertainty_disclosure(data, answers, memo))
    problems.extend(_check_numeric_confidence(memo))
    problems.extend(_check_authority_provenance(data))
    registry_problems, registry_advisory = _check_authority_registry(data)
    problems.extend(registry_problems)
    advisory.extend(registry_advisory)
    stale_problems, stale_advisory = orchestration_staleness(data, memo)
    problems.extend(stale_problems)
    advisory.extend(stale_advisory)
    problems.extend(_check_withdrawn_quotes(data, memo))
    problems.extend(_check_inference_basis(data))
    computation_problems, computation_advisory = _check_computations(data)
    problems.extend(computation_problems)
    advisory.extend(computation_advisory)
    advisory.extend(_advise_staleness(data, reviews))
    advisory.extend(_advise_contested_one_sided(data, memo))
    advisory.extend(_advise_human_judgment())
    mode = "reasoning"
    if identified_mode(data):
        mode = "identified"
        identified_problems, identified_advisory = run_identified_checks(data)
        problems.extend(identified_problems)
        advisory.extend(identified_advisory)

    return {
        "gate": GATE_VERSION,
        "mode": mode,
        "passed": not problems,
        "problems": problems,
        "advisory": advisory,
    }


def _check_required_classes(
    task_type: str, answers: list, pin: object = None
) -> list[str]:
    if isinstance(pin, dict) and pin.get("task_type"):
        return _check_pinned_classes(task_type, answers, pin)
    if pin is not None:
        # Present but unusable: fail closed, never silently fall
        # back to the live class table (closeout §7).
        return [
            "assessment policy pin is malformed (expected the pinned "
            "task_type, gate, and required classes): reclassify to "
            "re-pin under the current gate"
        ]
    if not task_type:
        return [
            "no task type is set: record one with answer_foundational "
            f"(one of: {', '.join(TASK_TYPES)})"
        ]
    if task_type not in REQUIRED_CLASSES:
        return [f"unknown task type {task_type!r}"]
    covered = {a.get("class") for a in answers}
    return [
        f"foundational question {qclass!r} is unanswered: "
        f"{QUESTION_PROMPTS[qclass]} Answer it or mark it unresolved "
        "with its resolution path."
        for qclass in REQUIRED_CLASSES[task_type]
        if qclass not in covered
    ]


def _check_pinned_classes(
    task_type: str, answers: list, pin: dict
) -> list[str]:
    """Enforce the pinned assessment policy, never the live label."""
    pinned_type = str(pin.get("task_type", "") or "")
    if task_type != pinned_type:
        return [
            f"task type {task_type!r} differs from the pinned assessment "
            f"policy ({pinned_type!r}): reclassify with a recorded basis "
            "instead of changing the label"
        ]
    pinned_gate = str(pin.get("gate", "") or "")
    if pinned_gate != GATE_VERSION:
        return [
            f"assessment policy pinned under {pinned_gate!r}, gate is now "
            f"{GATE_VERSION!r}: reclassify to re-pin under the current gate"
        ]
    required = pin.get("required_classes")
    if (
        not isinstance(required, list)
        or not required
        or any(c not in QUESTION_PROMPTS for c in required)
    ):
        return [
            "assessment policy is malformed (required classes unreadable): "
            "reclassify to re-pin under the current gate"
        ]
    covered = {a.get("class") for a in answers}
    return [
        f"foundational question {qclass!r} is unanswered: "
        f"{QUESTION_PROMPTS[qclass]} Answer it or mark it unresolved "
        "with its resolution path."
        for qclass in required
        if qclass not in covered
    ]


def _check_unresolved(answers: list) -> list[str]:
    problems = []
    for entry in answers:
        if entry.get("status") == "unresolved" and not str(
            entry.get("resolution", "") or ""
        ).strip():
            problems.append(
                f"question {entry.get('id', '?')} is unresolved without a "
                "resolution path: name what would resolve it"
            )
    return problems


def _check_finding_traceability(findings: list, answers: list) -> list[str]:
    by_issue: dict[str, list] = {}
    for entry in answers:
        issue = str(entry.get("issue", "") or "")
        if issue:
            by_issue.setdefault(issue, []).append(entry)
    problems = []
    for finding in findings:
        if finding.get("status") != "asserted":
            continue
        item = finding.get("item", "")
        if not by_issue.get(str(item)):
            problems.append(
                f"finding {item} is asserted with no linked foundational "
                "answer: record the question it rests on with issue "
                f"{item!r} first"
            )
    return problems


def _check_evidence_obligations(data: dict, memo: dict) -> list[str]:
    needs = data.get("evidence_needs") or {}
    if not isinstance(needs, dict) or not needs:
        return []
    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)]
    gaps = memo.get("gaps", "")
    if isinstance(gaps, list):
        gaps = "; ".join(str(g) for g in gaps)
    disclosed = bool(str(gaps or "").strip())
    problems = []
    for finding in memo.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if finding.get("status") != "asserted":
            continue
        item = str(finding.get("item", ""))
        item_needs = needs.get(item)
        if not isinstance(item_needs, dict):
            continue
        required = item_needs.get("required", [])
        adverse = item_needs.get("adverse", [])
        superseded = item_needs.get("superseded", [])
        if not all(isinstance(v, list) for v in (
                required, adverse, superseded)):
            problems.append(
                f"finding {item} carries corrupt evidence needs: "
                "re-freeze them with set_evidence_needs"
            )
            continue
        returned = []
        for fact in facts:
            if str(fact.get("item", "")) != item:
                continue
            if fact.get("withdrawn"):
                continue
            try:
                page = int(fact.get("page", 0))
            except (TypeError, ValueError):
                # Fail closed: an uncountable fact keeps the need open.
                continue
            returned.append((str(fact.get("document_id")), page))
        verdict = _assess_sufficiency(
            required, returned, adverse=adverse, superseded=superseded)
        if verdict["sufficient"] and verdict["adverse_covered"]:
            continue
        short = "; ".join(verdict.get("reasons", [])) or "unmet needs"
        if disclosed:
            continue
        problems.append(
            f"finding {item} is asserted with {short}: record the "
            "evidence or name the shortfall in gaps"
        )
    return problems


def _check_element_coverage(findings: list, elements: list) -> list[str]:
    by_issue = {str(e.get("issue", "")) for e in elements}
    problems = []
    for finding in findings:
        if finding.get("status") != "asserted":
            continue
        if finding.get("kind", "fact") not in ("legal_rule", "applicability"):
            continue
        item = str(finding.get("item", ""))
        if item not in by_issue:
            problems.append(
                f"finding {item} asserts a {finding.get('kind')} without an "
                "element analysis: analyze the controlling test "
                "element by element first"
            )
    return problems


def _check_adversarial(reviews: list) -> list[str]:
    if not reviews:
        return [
            "no adversarial review recorded: challenge the preliminary "
            "conclusion before filing (strongest argument against, "
            "opposing counsel, skeptical judge, alternative theory, "
            "reversing fact)"
        ]
    latest = reviews[-1]
    if latest.get("revision_needed") and not str(
        latest.get("revision", "") or ""
    ).strip():
        return ["the adversarial review demands revision but names none"]
    return []


def _check_uncertainty_disclosure(data: dict, answers: list, memo: dict) -> list[str]:
    uncertain_matter = any(
        isinstance(entry, dict) and entry.get("status") != "known"
        for entry in (data.get("matter", {}) or {}).get("fields", {}).values()
    )
    unresolved = any(a.get("status") == "unresolved" for a in answers)
    open_findings = any(
        f.get("status") in ("unresolved", "escalated") for f in (memo.get("findings") or [])
    )
    gaps = memo.get("gaps", "")
    if isinstance(gaps, list):
        gaps = "; ".join(str(g) for g in gaps)
    if (uncertain_matter or unresolved or open_findings) and not str(gaps or "").strip():
        return [
            "uncertainty is undisclosed: assumptions, unknowns, or open "
            "findings exist but gaps is empty — name them as gaps"
        ]
    return []


def _memo_texts(memo: dict) -> list[tuple[str, str]]:
    texts = [("conclusion", str(memo.get("conclusion", "") or ""))]
    for finding in memo.get("findings") or []:
        if isinstance(finding, dict):
            texts.append(
                (f"finding {finding.get('item', '?')}", str(finding.get("summary", "") or ""))
            )
    gaps = memo.get("gaps", "")
    if isinstance(gaps, list):
        gaps = "; ".join(str(g) for g in gaps)
    texts.append(("gaps", str(gaps or "")))
    return texts


def _check_numeric_confidence(memo: dict) -> list[str]:
    problems = []
    for label, text in _memo_texts(memo):
        for phrase in detect_numeric_confidence(text):
            problems.append(
                f"{label} expresses numeric confidence ({phrase!r}): "
                "remove it or ground it in a defined, validated basis"
            )
    return problems


def _check_authority_provenance(data: dict) -> list[str]:
    sources = data.get("sources") or {}
    problems = []
    for fact in data.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("kind", "fact") != "legal_rule":
            continue
        doc = fact.get("document_id", "")
        if doc not in sources:
            problems.append(
                f"legal-rule fact {fact.get('id', '?')} cites {doc} with no "
                "recorded provenance: note its repository, citation, and "
                "version first"
            )
    return problems


def _check_authority_registry(data: dict) -> tuple[list[str], list[str]]:
    """Legal-rule facts must name the authority they rely on (§7/§11).

    The join is an explicit link, not document membership and not
    text equality: the fact's `authority` names a registered A-id
    and the gate resolves it. Text equality was tried and rejected
    — claims legitimately summarize ("excerpt states breach
    elements") while propositions state the rule, so wording
    equality refuses sound filings. A missing link with a real
    claim fails closed; a missing link with no claim at all is a
    legacy record and keeps the old document-membership reading.
    """
    registry = data.get("authorities") or {}
    by_doc: dict[str, list] = {}
    by_id: dict[str, dict] = {}
    known: set[str] = set()
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        by_doc.setdefault(str(entry.get("document_id", "")), []).append(entry)
        known.add(str(key))
        by_id[str(key)] = entry
        if entry.get("id"):
            known.add(str(entry.get("id")))
            by_id[str(entry.get("id"))] = entry
    problems, advisory = [], []
    seen_docs: set[str] = set()
    seen_unparsed: set[str] = set()
    identification = data.get("identification")
    if not isinstance(identification, dict):
        identification = {}
    matter_display = str(
        identification.get("jurisdiction", "")
        or data.get("jurisdiction", "")
    )
    matter_jurisdiction = _normalize_text(matter_display)
    for fact in data.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("kind", "fact") != "legal_rule":
            continue
        doc = str(fact.get("document_id", "") or "")
        entries = by_doc.get(doc, [])
        link = str(fact.get("authority") or "").strip()
        problem = None
        if link:
            if link not in known:
                problem = (
                    f"legal-rule fact {fact.get('id', '?')} names {link}, "
                    "which is not a registered authority: register the "
                    "proposition first, then name its A-id"
                )
            else:
                # Applicability (closeout §11): a linked authority
                # from another jurisdiction, or effective after the
                # event, fails the filing until explained.
                target = by_id.get(link, {})
                entry_jurisdiction = _normalize_text(
                    str(target.get("jurisdiction") or "")
                )
                if (
                    matter_jurisdiction
                    and entry_jurisdiction
                    and entry_jurisdiction != matter_jurisdiction
                ):
                    problems.append(
                        f"authority {link} is {target.get('jurisdiction')} "
                        "but the matter jurisdiction is "
                        f"{matter_display}: confirm it applies before filing"
                    )
                effective = str(
                    target.get("effective")
                    or target.get("effective_date")
                    or ""
                ).strip()
                event_date = str(fact.get("event_date") or "").strip()
                if effective and event_date and effective > event_date:
                    problems.append(
                        f"authority {link} effective {effective} postdates "
                        f"the {fact.get('id', '?')} event {event_date}: "
                        "confirm it governs before filing"
                    )
                # Parsed-empty with the key present means the extractor
                # ran and found no legal citation; entries recorded
                # before parsing existed carry no key and stay silent.
                if (
                    link not in seen_unparsed
                    and target.get("verification") == "verified"
                    and "parsed_citations" in target
                    and not target.get("parsed_citations")
                ):
                    seen_unparsed.add(link)
                    advisory.append(
                        f"authority {link} claims verified but its citation "
                        f"{target.get('citation', '')!r} parsed as no legal "
                        "citation: confirm the citation text"
                    )
                # Assessment resolution: a relied-upon "verified"
                # status must resolve to a passing assessment
                # record, not a bare assertion. Byte drift is
                # guarded at the memo layer (source_drift fails
                # filing when the pinned file changes).
                if target.get("verification") == "verified":
                    assessments = data.get("authority_assessments") or {}
                    assessment_id = str(
                        target.get("assessment") or ""
                    ).strip()
                    assessment = assessments.get(assessment_id)
                    if not assessment_id or not isinstance(
                        assessment, dict
                    ):
                        problems.append(
                            f"authority {link} claims verified with no "
                            "supporting assessment on record: assess it "
                            "against its source before filing"
                        )
                    elif assessment.get("outcome") != "passed":
                        problems.append(
                            f"authority {link} assessment "
                            f"{assessment_id} did not pass: re-assess "
                            "before filing"
                        )
        elif _normalize_text(str(fact.get("claim") or "")):
            problem = (
                f"legal-rule fact {fact.get('id', '?')} cites {doc} but "
                "names no registered authority: name the relied-upon "
                "registration's A-id on the fact"
            )
        elif not entries:
            problem = (
                f"legal-rule fact {fact.get('id', '?')} cites {doc} with no "
                "registered authority: register the proposition, level, "
                "effective date, and treatment first"
            )
        if problem is not None:
            problems.append(problem)
        elif (
            entries
            and doc not in seen_docs
            and not any(
                e.get("level") == "binding"
                and e.get("verification") == "verified"
                for e in entries
            )
        ):
            advisory.append(
                f"{doc} has no binding verified authority registered — "
                "confirm the limitation is disclosed"
            )
        seen_docs.add(doc)
    return problems, advisory


def _check_computations(data: dict) -> tuple[list[str], list[str]]:
    """Recorded computations must replay (Item 5, G5).

    Re-runs every computation through the same dispatch the tool
    recorded through: a tampered result, an unknown op, or
    malformed inputs fail loudly instead of filing. A computation
    with no governing rule advises — dates and money must trace
    to the statute, clause, or instruction behind them.
    """
    problems: list[str] = []
    advisory: list[str] = []
    computations = data.get("computations") or []
    if not isinstance(computations, list):
        return ["the computations record is malformed: must be a list"], []
    for entry in computations:
        if not isinstance(entry, dict):
            problems.append("a computation record is malformed: must be an object")
            continue
        label = str(entry.get("id") or "?")
        try:
            actual = replay(str(entry.get("op") or ""), entry.get("inputs"))
        except ComputeError as error:
            problems.append(f"computation {label} is malformed: {error}")
            continue
        if actual != entry.get("result"):
            problems.append(
                f"computation {label} does not replay: recorded "
                f"{entry.get('result')!r} but {entry.get('op')} gives "
                f"{actual!r} — re-record it, do not edit results by hand"
            )
        if not str(entry.get("rule") or "").strip():
            advisory.append(
                f"computation {label} records no rule: name the statute, "
                "clause, or instruction the inputs come from"
            )
    return problems, advisory


def _check_inference_basis(data: dict) -> list[str]:
    problems = []
    for fact in data.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("kind", "fact") != "inference":
            continue
        if not str(fact.get("basis", "") or "").strip():
            problems.append(
                f"inference {fact.get('id', '?')} records no basis: "
                "say why the inference follows from the evidence"
            )
    return problems


def _advise_staleness(data: dict, reviews: list) -> list[str]:
    if not reviews:
        return []
    latest = str(reviews[-1].get("at", "") or "")
    if not latest:
        return []
    later = [
        f.get("id", "?")
        for f in (data.get("facts") or [])
        if isinstance(f, dict) and str(f.get("at", "") or "") > latest
    ]
    if later:
        return [
            "evidence recorded after the latest adversarial review "
            f"({', '.join(sorted(later))}): re-check the challenge "
            "before relying on it"
        ]
    return []


def _advise_human_judgment() -> list[str]:
    return [
        "human review still required: framing, jurisdiction, "
        "authority weight, conclusion logic, risks, and professional "
        "obligations are not deterministically checkable"
    ]


#: Mirrors case_record.FACT_STANCES (this module stays import-free;
#: the mirror is pinned by test so drift fails loudly).
FACT_STANCES_MIRROR = ("supports", "opposes", "context")


def _page_key(page: object) -> object:
    """Canonical page for join keys: 1 and "1" are the same page."""
    try:
        return int(page)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(page or "")


def quote_key(document_id: object, page: object, quote: object) -> tuple:
    """Citation/fact join key on the shared text contract.

    Quote text folds through textnorm (same function object the
    memo rules use via document_text); pages coerce so int and str
    representations join. Every join site (impact traces, memo
    rules, audit) must build keys this one way.
    """
    return (
        str(document_id or ""),
        _page_key(page),
        _normalize_text(str(quote or "")),
    )


#: Back-compat alias; new code uses quote_key.
_quote_key = quote_key


#: What trace_fact_impact covers (closeout §8): direct citation
#: links plus issue-level answers only. Worded to state the
#: boundary without overclaiming multi-hop analysis.
_TRACE_SCOPE = (
    "direct citation links (cited_by) and issue-level answers "
    "(answers) on the fact's item; conclusion, artifact, and "
    "multi-hop links are out of scope"
)


def trace_fact_impact(
    case_data: dict, memo: dict, fact_id: str
) -> dict:
    """What rests on one fact: finer relevance tracking.

    Answers "what breaks if this fact goes": the finding items
    citing it, the foundational answers on its item, whether it
    is live, withdrawn, or superseded, and by what. Withdrawal
    callers use this instead of reopening every answer on every
    affected item; the auditor uses it to catch filed conclusions
    resting on withdrawn or superseded evidence.
    """
    data = case_data if isinstance(case_data, dict) else {}
    memo = memo if isinstance(memo, dict) else {}
    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)]
    withdrawn = [
        f for f in (data.get("withdrawn_facts") or []) if isinstance(f, dict)
    ]
    fact = next((f for f in facts if f.get("id") == fact_id), None)
    status = "live"
    if fact is None:
        fact = next((f for f in withdrawn if f.get("id") == fact_id), None)
        status = "withdrawn" if fact is not None else "unknown"
    successors = sorted(
        str(other.get("id") or "")
        for other in facts
        if str(other.get("supersedes") or "") == fact_id
        and str(other.get("id") or "")
    )
    superseded_by = successors[0] if successors else None
    if fact is not None:
        if superseded_by is not None:
            status = "superseded"
    if fact is None:
        return {
            "fact": fact_id,
            "status": status,
            "item": "",
            "cited_by": [],
            "answers": [],
            "superseded_by": None,
            "superseded_by_all": [],
            "scope": _TRACE_SCOPE,
        }
    item = str(fact.get("item", "") or "")
    target = _quote_key(
        fact.get("document_id"), fact.get("page"), fact.get("quote")
    )
    cited_by = []
    for finding in memo.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        for citation in finding.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            if _quote_key(
                citation.get("document_id"),
                citation.get("page"),
                citation.get("quote"),
            ) != target:
                continue
            if str(finding.get("item", "") or "") != item:
                continue
            cited_by.append(
                f"{finding.get('item', '?')} ({finding.get('status', '?')})"
            )
            break
    answers = sorted(
        a.get("id", "?")
        for a in (data.get("foundational") or [])
        if isinstance(a, dict) and str(a.get("issue", "") or "") == item
    )
    return {
        "fact": fact_id,
        "status": status,
        "item": item,
        "cited_by": sorted(cited_by),
        "answers": answers,
        # First successor for back-compat; branches need the full
        # set (closeout §5: the old loop kept the last match only).
        "superseded_by": superseded_by,
        "superseded_by_all": successors,
        "scope": _TRACE_SCOPE,
    }


def _advise_contested_one_sided(data: dict, memo: dict) -> list[str]:
    """Advisory: asserted findings on contested items citing one side.

    Weighing opposition needs judgment, so this never blocks — the
    memo rules deliberately do not block contested items either.
    It names what the reviewer must check: which side was cited and
    which sits undisclosed in the record. Quote matching
    approximates document_text.normalize (no curly-quote folding):
    on mismatch the advisory degrades to silence, never to noise.
    """
    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)]
    if not facts:
        return []
    stances: dict[str, set] = {}
    for fact in facts:
        stance = fact.get("stance", "supports")
        if stance not in FACT_STANCES_MIRROR:
            stance = "supports"
        stances.setdefault(str(fact.get("item", "")), set()).add(stance)
    contested = {
        item
        for item, got in stances.items()
        if "supports" in got and "opposes" in got
    }
    if not contested:
        return []

    by_quote: dict[tuple, list] = {}
    for fact in facts:
        by_quote.setdefault(
            _quote_key(
                fact.get("document_id"),
                fact.get("page"),
                fact.get("quote"),
            ),
            [],
        ).append(fact)
    notes = []
    for finding in memo.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if finding.get("status") != "asserted":
            continue
        item = finding.get("item")
        if item not in contested:
            continue
        cited = set()
        for citation in finding.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            for fact in by_quote.get(
                _quote_key(
                    citation.get("document_id"),
                    citation.get("page"),
                    citation.get("quote"),
                ),
                [],
            ):
                if str(fact.get("item", "")) != str(item):
                    continue
                stance = fact.get("stance", "supports")
                if stance not in FACT_STANCES_MIRROR:
                    stance = "supports"
                cited.add(stance)
        missing = {"supports", "opposes"} - cited
        if missing and cited:
            notes.append(
                f"{item} is contested but asserted citing only "
                f"{' and '.join(sorted(cited))} evidence: reviewer check — "
                f"{' and '.join(sorted(missing))} evidence in the record "
                "is not cited"
            )
    return notes


# ---- universal task identification (Stages 1-6, 9) -----------------------------
#
# Identification precedes analysis: what the user actually wants, which
# legal categories it spans, what must be settled first, what is
# missing and how to get it, which workflows apply, and what work
# product to deliver. The taxonomy below is illustrative, never
# exhaustive: the "other" category plus free-text subcategories keep
# unfamiliar and emerging work classifiable. Guidance (thresholds,
# workflows, products) suggests; the agent determines what matters.

TASK_TAXONOMY: dict[str, dict] = {
    "research": {
        "label": "Legal research and interpretation",
        "gate": "research",
        "subcategories": ("statutory interpretation", "case-law analysis", "regulatory guidance"),
        "thresholds": ("jurisdiction", "current status of the law"),
        "workflows": ("legal_research", "statute_case_analysis", "quality_control"),
        "products": ("research_report", "direct_answer"),
    },
    "advice": {
        "label": "Legal advice and issue analysis",
        "gate": "investigation",
        "subcategories": ("issue spotting", "liability assessment", "options analysis"),
        "thresholds": ("issue framing", "governing law"),
        "workflows": ("element_analysis", "fact_evidence", "risk_assessment", "quality_control"),
        "products": ("direct_answer", "action_plan"),
    },
    "contract": {
        "label": "Contract drafting, review, interpretation, negotiation",
        "gate": "contract",
        "subcategories": ("drafting", "review", "interpretation", "negotiation"),
        "thresholds": ("governing law", "formation and validity", "authority to bind"),
        "workflows": ("contract_interpretation", "fact_evidence", "drafting_verification", "risk_assessment", "quality_control"),
        "products": ("draft_agreement", "direct_answer"),
    },
    "litigation": {
        "label": "Litigation, claims, defenses, dispute resolution",
        "gate": "litigation",
        "subcategories": ("claim analysis", "defense analysis", "motion practice", "discovery", "trial preparation"),
        "thresholds": ("jurisdiction", "standing", "limitation periods", "procedural prerequisites"),
        "workflows": ("procedure_deadlines", "fact_evidence", "element_analysis", "statute_case_analysis", "adversarial_review", "quality_control"),
        "products": ("litigation_analysis", "legal_memorandum"),
    },
    "memoranda": {
        "label": "Legal memoranda, opinions, written submissions",
        "gate": "drafting",
        "subcategories": ("legal memorandum", "opinion letter", "brief", "submission"),
        "thresholds": ("issue framing", "governing law"),
        "workflows": ("legal_research", "element_analysis", "drafting_verification", "adversarial_review", "quality_control"),
        "products": ("legal_memorandum",),
    },
    "corporate": {
        "label": "Corporate transactions and governance",
        "gate": "strategy",
        "subcategories": ("mergers and acquisitions", "governance", "entity formation", "financing"),
        "thresholds": ("governing regime", "authority", "filing deadlines"),
        "workflows": ("fact_evidence", "risk_assessment", "strategy_comparison", "drafting_verification", "quality_control"),
        "products": ("action_plan", "draft_agreement"),
    },
    "regulatory": {
        "label": "Regulatory compliance and investigations",
        "gate": "risk",
        "subcategories": ("compliance review", "investigation response", "licensing"),
        "thresholds": ("governing regime", "applicable obligations", "reporting deadlines"),
        "workflows": ("legal_research", "fact_evidence", "risk_assessment", "quality_control"),
        "products": ("risk_matrix", "action_plan"),
    },
    "employment": {
        "label": "Employment and labor matters",
        "gate": "investigation",
        "subcategories": ("hiring and termination", "workplace policies", "labor disputes"),
        "thresholds": ("governing regime", "procedural prerequisites", "deadlines"),
        "workflows": ("fact_evidence", "element_analysis", "procedure_deadlines", "quality_control"),
        "products": ("direct_answer", "action_plan"),
    },
    "criminal": {
        "label": "Criminal law and procedure",
        "gate": "litigation",
        "subcategories": ("defense", "procedure", "plea and sentencing"),
        "thresholds": ("jurisdiction", "limitation periods", "procedural bars"),
        "workflows": ("procedure_deadlines", "fact_evidence", "element_analysis", "adversarial_review", "quality_control"),
        "products": ("litigation_analysis",),
    },
    "liability": {
        "label": "Civil liability, damages, remedies",
        "gate": "litigation",
        "subcategories": ("negligence", "damages quantification", "remedies"),
        "thresholds": ("jurisdiction", "standing", "limitation periods"),
        "workflows": ("element_analysis", "fact_evidence", "risk_assessment", "quality_control"),
        "products": ("litigation_analysis", "risk_matrix"),
    },
    "ip": {
        "label": "Intellectual property",
        "gate": "research",
        "subcategories": ("patents", "trademarks", "copyright", "trade secrets"),
        "thresholds": ("jurisdiction", "current status of the law"),
        "workflows": ("legal_research", "fact_evidence", "element_analysis", "quality_control"),
        "products": ("research_report", "risk_matrix"),
    },
    "tax": {
        "label": "Taxation",
        "gate": "research",
        "subcategories": ("planning", "controversy", "compliance"),
        "thresholds": ("jurisdiction", "current status of the law", "filing deadlines"),
        "workflows": ("legal_research", "statute_case_analysis", "fact_evidence", "quality_control"),
        "products": ("research_report", "action_plan"),
    },
    "family": {
        "label": "Family and succession matters",
        "gate": "investigation",
        "subcategories": ("divorce and custody", "succession planning", "estate administration"),
        "thresholds": ("jurisdiction", "governing regime", "deadlines"),
        "workflows": ("fact_evidence", "procedure_deadlines", "drafting_verification", "quality_control"),
        "products": ("action_plan", "draft_agreement"),
    },
    "administrative": {
        "label": "Administrative and public law",
        "gate": "research",
        "subcategories": ("judicial review", "agency proceedings", "public law"),
        "thresholds": ("jurisdiction", "standing", "procedural prerequisites"),
        "workflows": ("legal_research", "procedure_deadlines", "element_analysis", "quality_control"),
        "products": ("research_report", "litigation_analysis"),
    },
    "evidence": {
        "label": "Evidence analysis and factual investigations",
        "gate": "investigation",
        "subcategories": ("document review", "witness analysis", "chronology"),
        "thresholds": ("scope", "privilege", "admissibility prerequisites"),
        "workflows": ("fact_evidence", "quality_control"),
        "products": ("direct_answer", "action_plan"),
    },
    "risk_strategy": {
        "label": "Legal risk assessment and strategy",
        "gate": "risk",
        "subcategories": ("risk assessment", "strategy comparison", "scenario planning"),
        "thresholds": ("objectives", "constraints", "material scenarios"),
        "workflows": ("risk_assessment", "strategy_comparison", "fact_evidence", "quality_control"),
        "products": ("risk_matrix", "negotiation_strategy"),
    },
    "enforcement": {
        "label": "Enforcement, appeals, post-judgment matters",
        "gate": "litigation",
        "subcategories": ("appeals", "judgment enforcement", "post-judgment relief"),
        "thresholds": ("jurisdiction", "standing", "appeal deadlines"),
        "workflows": ("procedure_deadlines", "statute_case_analysis", "drafting_verification", "quality_control"),
        "products": ("action_plan", "litigation_analysis"),
    },
    "other": {
        "label": "Other or emerging legal work",
        "gate": "investigation",
        "subcategories": (),
        "thresholds": ("what would make further analysis unnecessary",),
        "workflows": ("fact_evidence", "quality_control"),
        "products": ("direct_answer",),
    },
}

TASK_ALIASES: dict[str, str] = {
    "investigation": "evidence",
}
"""Accepted spellings that canonicalize to a taxonomy key.

"investigation" is the gate task type, the gate, and the
system's own word for establishing what happened; the taxonomy
key for that work is "evidence". Aliases are accepted anywhere
a category is expected and stored canonical, so downstream
TASK_TAXONOMY lookups keep working.
"""


def canonical_category(name: str) -> str:
    """The taxonomy key for a category or alias spelling."""
    if not isinstance(name, str):
        return name
    return TASK_ALIASES.get(name, name)

WORKFLOWS: dict[str, dict] = {
    "legal_research": {
        "label": "Legal research",
        "tools": ("plan_research", "search_corpus", "note_source", "resolve_query"),
    },
    "statute_case_analysis": {
        "label": "Statutory and case-law analysis",
        "tools": ("note_source", "analyze_element", "record_fact"),
    },
    "contract_interpretation": {
        "label": "Contract and document interpretation",
        "tools": ("analyze_document", "search_corpus", "analyze_element", "record_fact"),
    },
    "fact_evidence": {
        "label": "Fact development and evidence assessment",
        "tools": ("analyze_document", "record_fact", "record_facts", "search_corpus", "sweep_issues", "check_signatures"),
    },
    "element_analysis": {
        "label": "Element-by-element legal analysis",
        "tools": ("analyze_element", "answer_foundational"),
    },
    "procedure_deadlines": {
        "label": "Procedural and deadline analysis",
        "tools": ("note_context", "plan_research", "open_branch"),
    },
    "drafting_verification": {
        "label": "Drafting and document verification",
        "tools": ("review_draft", "submit_memo"),
    },
    "risk_assessment": {
        "label": "Risk assessment",
        "tools": ("answer_foundational", "analyze_element"),
    },
    "adversarial_review": {
        "label": "Adversarial review",
        "tools": ("adversarial_review",),
    },
    "strategy_comparison": {
        "label": "Strategic option comparison",
        "tools": ("answer_foundational", "open_branch", "resolve_branch"),
    },
    "quality_control": {
        "label": "Final quality control",
        "tools": ("review_draft", "submit_memo", "request_evidence"),
    },
}

WORK_PRODUCTS: dict[str, dict] = {
    "direct_answer": {
        "label": "Direct answer",
        "requires": ("conclusion", "key reasons", "limitations"),
    },
    "legal_memorandum": {
        "label": "Legal memorandum",
        "requires": ("issue", "rules", "application", "counterarguments", "conclusion", "limitations"),
    },
    "draft_agreement": {
        "label": "Draft agreement",
        "requires": ("operative terms", "defined terms", "conditions", "termination", "review note"),
    },
    "litigation_analysis": {
        "label": "Litigation analysis",
        "requires": ("claims", "elements", "defenses", "evidence", "procedure", "risks", "options"),
    },
    "risk_matrix": {
        "label": "Risk matrix",
        "requires": ("scenarios", "likelihood and severity", "mitigations", "residual risks"),
    },
    "research_report": {
        "label": "Research report",
        "requires": ("issue", "authorities", "contrary authority", "gaps"),
    },
    "negotiation_strategy": {
        "label": "Negotiation strategy",
        "requires": ("objectives", "options", "leverage", "fallback", "risks"),
    },
    "action_plan": {
        "label": "Action plan",
        "requires": ("steps", "owners", "deadlines", "dependencies"),
    },
    "other": {
        "label": "Other work product",
        "requires": ("stated deliverable",),
    },
}

TRIAGE_DECISIONS = ("investigate", "ask_user", "scenarios", "accept")

TRIAGE_GUIDE = {
    "investigate": "name the source to check (documents, tools, research)",
    "ask_user": "the smallest set of high-value clarifying questions",
    "scenarios": "the alternative assumptions to analyze explicitly",
    "accept": "why proceeding is safe and what is disclosed",
}

IDENTIFICATION_FIELDS = (
    "requested",
    "objective",
    "deliverable",
    "underlying_problem",
    "context_available",
    "missing_or_ambiguous",
    "constraints",
)

#: Threshold areas mapped to the question class that would cover
#: them, for the threshold advisory (guidance, never a failure).
THRESHOLD_CLASS_MAP = {
    "jurisdiction": "jurisdiction",
    "standing": "claim",
    "limitation periods": "deadlines",
    "appeal deadlines": "deadlines",
    "filing deadlines": "deadlines",
    "reporting deadlines": "deadlines",
    "deadlines": "deadlines",
    "procedural prerequisites": "deadlines",
    "procedural bars": "deadlines",
    "governing law": "governing_law",
    "governing regime": "governing_law",
    "current status of the law": "law_status",
    "formation and validity": "claim",
    "authority to bind": "claim",
    "authority": "claim",
    "issue framing": "objective",
    "objectives": "objective",
    "scope": "objective",
}


def plan_case(categories: list[str]) -> dict:
    """Guidance for identified categories: thresholds, workflows, products.

    Pure function of its inputs. Raises ValueError for an unknown
    category. Suggestion only: the agent determines what matters.
    """
    categories = [canonical_category(c) for c in categories]
    unknown = [c for c in categories if c not in TASK_TAXONOMY]
    if unknown:
        raise ValueError(
            f"unknown task categories: {', '.join(unknown)}; "
            f"known: {', '.join(sorted(TASK_TAXONOMY))}"
        )
    seen: set[str] = set()
    thresholds, workflows, products = [], [], []
    for category in categories:
        entry = TASK_TAXONOMY[category]
        for key, bucket in (
            ("thresholds", thresholds),
            ("workflows", workflows),
            ("products", products),
        ):
            for item in entry[key]:
                if item not in seen:
                    seen.add(item)
                    bucket.append(item)
    return {"thresholds": thresholds, "workflows": workflows, "products": products}


LAW_BASES = (
    "supplied-documents",
    "background-needed",
    "mixed",
)
"""Where the identification says the law comes from (Item 2, G9).

supplied-documents: every legal proposition traces to supplied
sources via registered authorities — files normally.
background-needed/mixed: the work needs law beyond the supplied
record, which this agent cannot verify (no live-law corpus) —
the gate refuses; narrow the scope or qualify at human sign-off.
"""


def _check_scope_coverage(identification: dict) -> tuple[list[str], list[str]]:
    """Scope gate: declared law basis + jurisdiction (Item 2, G9).

    Honors docs/supported-coverage.md. Structural only: the basis
    must be declared and in-scope, and a missing jurisdiction must
    be a conscious omission — never that the declaration is true.
    A model that declares supplied-documents while leaning on
    background law lies past this gate; only the claims judge and
    the human reviewer can catch that.
    """
    problems: list[str] = []
    advisory: list[str] = []
    basis = str(identification.get("law_basis") or "").strip()
    if basis in ("background-needed", "mixed"):
        problems.append(
            f"law basis is {basis}: the work needs law beyond the supplied "
            "record, which this agent cannot verify (no live-law corpus): "
            "narrow to supplied documents with registered authorities, "
            "or qualify at human sign-off"
        )
    elif not basis:
        advisory.append(
            "no law basis declared: say whether the law comes from supplied "
            f"documents or background knowledge (one of: {', '.join(LAW_BASES)})"
        )
    elif basis not in LAW_BASES:
        problems.append(
            f"unknown law basis {basis!r}: must be one of {', '.join(LAW_BASES)}"
        )
    if not str(identification.get("jurisdiction") or "").strip():
        advisory.append(
            "no jurisdiction recorded: confirm none is needed, or record the "
            "operator- or source-established jurisdiction (never infer one)"
        )
    return problems, advisory


def validate_identification(entry: dict) -> list[str]:
    """Deterministic problems with one task identification, else []."""
    if not isinstance(entry, dict):
        return ["the identification must be an object"]
    return [
        f"{field} is required: say {IDENTIFICATION_GUIDE[field]}"
        for field in _missing(entry, IDENTIFICATION_FIELDS)
    ]


IDENTIFICATION_GUIDE = {
    "requested": "what the user explicitly asked for",
    "objective": "the practical or legal objective behind the request",
    "deliverable": "what deliverable or decision would satisfy it",
    "underlying_problem": "the legal problem beneath the requested output",
    "context_available": "what legal and factual context is already available",
    "missing_or_ambiguous": "what information is missing or ambiguous",
    "constraints": "legal, procedural, temporal, confidentiality, or safety constraints",
}


def validate_classification(
    categories: list, dependencies: list, primary: str, gate_task_type: str
) -> list[str]:
    """Deterministic problems with a multi-label classification, else []."""
    problems = []
    if not isinstance(categories, list) or not categories:
        return ["at least one task category is required"]
    names = []
    for pos, entry in enumerate(categories):
        if not isinstance(entry, dict):
            problems.append(f"category at index {pos} must be an object")
            continue
        name = entry.get("category", "")
        if name not in TASK_TAXONOMY and name not in TASK_ALIASES:
            problems.append(
                f"unknown task category {name!r}; known: "
                f"{', '.join(sorted(TASK_TAXONOMY))}; "
                "use 'other' for emerging work"
            )
        elif canonical_category(name) in names:
            problems.append(f"duplicate task category {name!r}")
        else:
            names.append(canonical_category(name))
        if not str(entry.get("rationale", "") or "").strip():
            problems.append(f"category {name or pos} needs its rationale")
    if canonical_category(primary) not in names:
        problems.append("the primary category must be one of the classified categories")
    if gate_task_type not in TASK_TYPES:
        problems.append(
            f"gate task type {gate_task_type!r} must be one of: "
            + ", ".join(TASK_TYPES)
        )
    if isinstance(dependencies, list):
        dependencies = [
            {
                "from": canonical_category(e.get("from", "")),
                "to": canonical_category(e.get("to", "")),
            }
            if isinstance(e, dict)
            else e
            for e in dependencies
        ]
    problems.extend(_validate_edges(dependencies, names, "category"))
    return problems


def validate_workflow(components: list) -> list[str]:
    """Deterministic problems with a workflow plan, else []."""
    if not isinstance(components, list) or not components:
        return ["at least one workflow component is required"]
    problems = []
    names = []
    for pos, entry in enumerate(components):
        if not isinstance(entry, dict):
            problems.append(f"component at index {pos} must be an object")
            continue
        name = entry.get("workflow", "")
        if name not in WORKFLOWS:
            problems.append(f"unknown workflow {name!r}")
        elif name in names:
            problems.append(f"duplicate workflow {name!r}")
        else:
            names.append(name)
        if not str(entry.get("purpose", "") or "").strip():
            problems.append(f"workflow {name or pos} needs its purpose")
    edges = [
        {"from": entry.get("workflow", ""), "to": dep}
        for entry in components
        if isinstance(entry, dict)
        for dep in (entry.get("after") or [])
    ]
    problems.extend(_validate_edges(edges, names, "workflow"))
    return problems


def validate_triage(entry: dict) -> list[str]:
    """Deterministic problems with one clarification triage, else []."""
    if not isinstance(entry, dict):
        return ["the triage must be an object"]
    problems = []
    for field in _missing(entry, ("ref", "gap", "needed_for", "sources_checked")):
        problems.append(f"{field} is required")
    decision = entry.get("decision", "")
    if decision not in TRIAGE_DECISIONS:
        problems.append(
            f"decision {decision!r} must be one of: "
            + ", ".join(TRIAGE_DECISIONS)
        )
    elif not str(entry.get("detail", "") or "").strip():
        problems.append(f"detail is required: {TRIAGE_GUIDE[decision]}")
    return problems


def validate_work_product(entry: dict) -> list[str]:
    """Deterministic problems with a work-product declaration, else []."""
    if not isinstance(entry, dict):
        return ["the declaration must be an object"]
    problems = []
    if entry.get("product") not in WORK_PRODUCTS:
        problems.append(
            f"product {entry.get('product')!r} must be one of: "
            + ", ".join(sorted(WORK_PRODUCTS))
        )
    sections = entry.get("sections")
    if (
        not isinstance(sections, list)
        or not sections
        or any(not str(s or "").strip() for s in sections)
    ):
        problems.append("sections must be a non-empty list of named sections")
    if not str(entry.get("traceability", "") or "").strip():
        problems.append(
            "traceability is required: how conclusions trace to "
            "authorities, facts, and assumptions"
        )
    return problems


def validate_answer_edges(answers: list) -> list[str]:
    """Deterministic problems with question dependencies, else [].

    Every depends_on id must exist; the graph must be acyclic.
    """
    ids = [a.get("id") for a in answers if isinstance(a, dict)]
    edges = [
        {"from": a.get("id"), "to": dep}
        for a in answers
        if isinstance(a, dict)
        for dep in (a.get("depends_on") or [])
    ]
    return _validate_edges(edges, ids, "question")


def _validate_edges(edges: list, names: list, kind: str) -> list[str]:
    problems = []
    if not isinstance(edges, list):
        return [f"{kind} dependencies must be a list"]
    known = set(names)
    graph: dict[str, list] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            problems.append(f"a {kind} dependency must be an object")
            continue
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src not in known:
            problems.append(f"unknown {kind} {src!r} in dependencies")
            continue
        if dst not in known:
            problems.append(f"unknown {kind} {dst!r} in dependencies")
            continue
        if src == dst:
            problems.append(f"{kind} {src!r} cannot depend on itself")
            continue
        graph.setdefault(src, []).append(dst)
    problems.extend(
        f"cyclic {kind} dependencies: {' -> '.join(cycle)}"
        for cycle in _cycles(graph)
    )
    return problems


def _cycles(graph: dict[str, list]) -> list[list]:
    """Dependency cycles in a graph, each as a node path."""
    cycles, visited, stack = [], set(), []

    def visit(node: str) -> None:
        if node in stack:
            cycles.append(stack[stack.index(node):] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        stack.append(node)
        for nxt in graph.get(node, []):
            visit(nxt)
        stack.pop()

    for node in sorted(graph):
        visit(node)
    return cycles


def identified_mode(case_data: dict) -> bool:
    """Whether the identification chain engages (Stage 1 recorded)."""
    data = case_data if isinstance(case_data, dict) else {}
    return isinstance(data.get("identification"), dict)


def run_identified_checks(case_data: dict) -> tuple[list[str], list[str]]:
    """Identified-mode gate checks: (problems, advisory).

    Runs after the reasoning checks when identification is present.
    Structural only: the chain must exist, be valid, and cover the
    record's unknowns — never that it is correct.
    """
    data = case_data if isinstance(case_data, dict) else {}
    problems: list[str] = []
    advisory: list[str] = []
    identification = data.get("identification") or {}
    problems.extend(
        f"identification: {p}" for p in validate_identification(identification)
    )
    classification = data.get("classification") or {}
    categories = classification.get("categories") or []
    problems.extend(
        f"classification: {p}"
        for p in validate_classification(
            categories,
            classification.get("dependencies") or [],
            classification.get("primary", ""),
            classification.get("gate_task_type", ""),
        )
    )
    if (
        classification.get("gate_task_type")
        and classification.get("gate_task_type") != data.get("task_type")
    ):
        problems.append(
            "classification gate task type "
            f"{classification.get('gate_task_type')!r} does not match the run's "
            f"task type {data.get('task_type')!r}"
        )
    scope_problems, scope_advisory = _check_scope_coverage(identification)
    problems.extend(scope_problems)
    advisory.extend(scope_advisory)
    workflow = data.get("workflow") or {}
    if not workflow.get("components"):
        problems.append(
            "no workflow plan recorded: select the workflow components "
            "and their order first"
        )
    else:
        problems.extend(
            f"workflow: {p}" for p in validate_workflow(workflow["components"])
        )
    product = data.get("work_product") or {}
    if not product:
        problems.append(
            "no work product declared: name the deliverable and its sections first"
        )
    else:
        problems.extend(
            f"work product: {p}" for p in validate_work_product(product)
        )
    answers = [a for a in (data.get("foundational") or []) if isinstance(a, dict)]
    decompositions = [
        d for d in (data.get("decompositions") or []) if isinstance(d, dict)
    ]
    if not decompositions:
        problems.append(
            "no validated decomposition: validate the question set "
            "before filing"
        )
    elif decompositions[-1].get("verdict") == "stale":
        problems.append(
            "decomposition is stale after reclassification: "
            f"{decompositions[-1].get('stale_reason', 're-validate')}; "
            "re-validate the question set before filing"
        )
    elif decompositions[-1].get("verdict") != "pass":
        problems.append(
            "latest decomposition failed validation: "
            + "; ".join(
                str(p)
                for p in (decompositions[-1].get("problems") or ["re-validate"])
            )
        )
    else:
        uncovered, unplanned = decomposition_coverage(
            decompositions[-1].get("questions") or [], answers
        )
        problems.extend(uncovered)
        if unplanned:
            advisory.append(
                f"{len(unplanned)} recorded answer(s) outside the validated "
                "plan — re-validate if the plan is stale: "
                + "; ".join(unplanned[:3])
            )
    problems.extend(validate_answer_edges(answers))
    problems.extend(_check_triage_coverage(data, answers))
    advisory.extend(_advise_thresholds(data, answers))
    advisory.extend(_advise_dependency_order(answers))
    advisory.extend(_advise_product_content(product))
    for approval in pending_approvals(data):
        advisory.append(
            f"approval {approval.get('id', '?')} pending: "
            f"{approval.get('action', '')} — nothing external may proceed "
            "on it until granted"
        )
    return problems, advisory


def _check_triage_coverage(data: dict, answers: list) -> list[str]:
    refs = {
        str(t.get("ref", ""))
        for t in (data.get("triages") or [])
        if isinstance(t, dict)
    }
    problems = []
    for entry in answers:
        if entry.get("status") == "unresolved" and entry.get("id") not in refs:
            problems.append(
                f"unresolved {entry.get('id')} has no triage decision: "
                "investigate it, ask the user, analyze scenarios, or "
                "record why proceeding is safe"
            )
    fields = (data.get("matter", {}) or {}).get("fields", {})
    for name, field in fields.items():
        if (
            isinstance(field, dict)
            and field.get("status") == "unknown"
            and f"matter:{name}" not in refs
        ):
            problems.append(
                f"unknown matter field {name} has no triage decision"
            )
    return problems


def _advise_thresholds(data: dict, answers: list) -> list[str]:
    classification = data.get("classification") or {}
    names = [
        c.get("category")
        for c in (classification.get("categories") or [])
        if isinstance(c, dict) and c.get("category") in TASK_TAXONOMY
    ]
    if not names:
        return []
    covered = {a.get("class") for a in answers if isinstance(a, dict)}
    missing = []
    for name in names:
        for threshold in TASK_TAXONOMY[name]["thresholds"]:
            qclass = THRESHOLD_CLASS_MAP.get(threshold)
            if qclass and qclass not in covered and threshold not in missing:
                missing.append(threshold)
    if missing:
        return [
            "threshold areas with no recorded question — confirm none is "
            f"needed: {', '.join(missing)}"
        ]
    return []


def _seq(answer_id: str) -> int | None:
    try:
        return int(str(answer_id or "")[2:])
    except (TypeError, ValueError):
        return None


def _advise_dependency_order(answers: list) -> list[str]:
    seqs = {}
    for entry in answers:
        if isinstance(entry, dict) and entry.get("id"):
            seqs[entry["id"]] = _seq(entry["id"])
    notes = []
    for entry in answers:
        if not isinstance(entry, dict):
            continue
        mine = seqs.get(entry.get("id"))
        if mine is None:
            continue
        for dep in entry.get("depends_on") or []:
            theirs = seqs.get(dep)
            if theirs is not None and theirs > mine and len(notes) < 3:
                notes.append(
                    f"{entry.get('id')} was recorded before its dependency "
                    f"{dep} — confirm the dependency was resolved first"
                )
    return notes


def _advise_product_content(product: dict) -> list[str]:
    if not isinstance(product, dict) or product.get("product") not in WORK_PRODUCTS:
        return []
    requires = WORK_PRODUCTS[product["product"]]["requires"]
    return [
        f"human review: a {WORK_PRODUCTS[product['product']]['label'].lower()} "
        f"should address: {', '.join(requires)}"
    ]


# ---- decomposition validation -----------------------------------------------
#
# The decomposition validator checks the QUESTION SET before the work,
# not the answers after it: does the plan cover the required
# question classes, the taxonomy's threshold areas (or waive them
# with reasons), valid dependencies, non-redundant questions each
# with a resolution criterion? Sufficiency of judgment — the right
# alternative theories, the user's real problem — stays an explicit
# human advisory. Planned questions use caller-chosen temp ids;
# the gate later matches planned (class, issue) pairs against
# recorded answers.

def _normalize_question(text: str) -> str:
    return "".join(
        ch.lower() for ch in str(text or "") if ch.isalnum()
    )


def validate_decomposition(
    questions: list, waived: list, categories: list, gate_task_type: str
) -> tuple[list[str], list[str]]:
    """Validate a planned question set: (problems, advisory).

    Pure function of its inputs. Checks class coverage for the
    gate type, threshold coverage for the categories, dependency
    validity, redundancy, and resolution criteria.
    """
    problems: list[str] = []
    advisory: list[str] = [
        "human judgment: confirm alternative theories are represented "
        "and the plan solves the user's actual problem, not just its wording"
    ]
    if not isinstance(questions, list) or not questions:
        return ["at least one planned question is required"], advisory
    if gate_task_type not in REQUIRED_CLASSES:
        problems.append(f"unknown gate task type {gate_task_type!r}")
        return problems, advisory
    ids: list[str] = []
    for pos, entry in enumerate(questions):
        if not isinstance(entry, dict):
            problems.append(f"question at index {pos} must be an object")
            continue
        temp = str(entry.get("id", "") or "")
        if not temp:
            problems.append(f"question at index {pos} needs its id")
        elif temp in ids:
            problems.append(f"duplicate planned question id {temp!r}")
        else:
            ids.append(temp)
        if entry.get("class") not in QUESTION_CLASSES:
            problems.append(
                f"question {temp or pos} has unknown class "
                f"{entry.get('class')!r}"
            )
        if not str(entry.get("question", "") or "").strip():
            problems.append(f"question {temp or pos} needs its text")
        if not str(entry.get("criterion", "") or "").strip():
            problems.append(
                f"question {temp or pos} needs its resolution criterion: "
                "what would resolve it"
            )
    edges = [
        {"from": entry.get("id", ""), "to": dep}
        for entry in questions
        if isinstance(entry, dict)
        for dep in (entry.get("depends_on") or [])
    ]
    problems.extend(_validate_edges(edges, ids, "planned question"))
    seen: dict[str, str] = {}
    for entry in questions:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        fingerprint = _normalize_question(entry.get("question", ""))
        if fingerprint and fingerprint in seen:
            problems.append(
                f"questions {seen[fingerprint]} and {entry['id']} look "
                "redundant: merge them or sharpen the difference"
            )
        else:
            seen[fingerprint] = entry["id"]
    planned_classes = {
        entry.get("class") for entry in questions if isinstance(entry, dict)
    }
    for qclass in REQUIRED_CLASSES[gate_task_type]:
        if qclass not in planned_classes:
            problems.append(
                f"required question class {qclass!r} is not planned: "
                f"{QUESTION_PROMPTS[qclass]}"
            )
    problems.extend(
        _validate_threshold_coverage(questions, waived, categories)
    )
    return problems, advisory


def _validate_threshold_coverage(
    questions: list, waived: list, categories: list
) -> list[str]:
    names = [c for c in (categories or []) if c in TASK_TAXONOMY]
    if not names:
        return []
    if not isinstance(waived, list):
        return ["waived thresholds must be a list"]
    problems = []
    waived_areas: set[str] = set()
    for entry in waived:
        if not isinstance(entry, dict):
            problems.append("a waived threshold must be an object")
            continue
        area, reason = entry.get("area", ""), str(entry.get("reason", "") or "")
        known = any(
            area in TASK_TAXONOMY[name]["thresholds"] for name in names
        )
        if not known:
            problems.append(f"unknown threshold area {area!r} in waivers")
            continue
        if not reason.strip():
            problems.append(f"waived threshold {area!r} needs its reason")
            continue
        waived_areas.add(area)
    planned_classes = {
        entry.get("class") for entry in questions if isinstance(entry, dict)
    }
    for name in names:
        for threshold in TASK_TAXONOMY[name]["thresholds"]:
            qclass = THRESHOLD_CLASS_MAP.get(threshold)
            if (
                qclass
                and qclass not in planned_classes
                and threshold not in waived_areas
            ):
                problems.append(
                    f"threshold area {threshold!r} has no planned question: "
                    f"plan a {qclass} question or waive it with a reason"
                )
    return problems


# ---- proposition-level authority registry -------------------------------------
#
# Document provenance (which repository a text came from) is not
# enough: each legal proposition the agent relies on needs its
# authority level, effective date, verification status, and
# subsequent treatment recorded, or the limitation stated. Binding
# authority, persuasive authority, secondary commentary, current
# law, historical law, holdings, and inferences drawn from holdings
# must never silently collapse into one another.

AUTHORITY_LEVELS = ("binding", "persuasive", "secondary")

AUTHORITY_VERIFICATION = ("verified", "provisional", "unverified")

EFFECTIVE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_authority(entry: dict) -> list[str]:
    """Deterministic problems with one authority entry, else []."""
    if not isinstance(entry, dict):
        return ["the authority entry must be an object"]
    problems = []
    for field in _missing(
        entry, ("proposition", "document_id", "citation", "version")
    ):
        problems.append(f"{field} is required")
    if entry.get("level") not in AUTHORITY_LEVELS:
        problems.append(
            f"level {entry.get('level')!r} must be one of: "
            + ", ".join(AUTHORITY_LEVELS)
        )
    if not EFFECTIVE_DATE_PATTERN.match(str(entry.get("effective_date", "") or "")):
        problems.append(
            "effective_date is required as YYYY-MM-DD: the law's date, "
            "not the retrieval date"
        )
    verification = entry.get("verification", "")
    if verification not in AUTHORITY_VERIFICATION:
        problems.append(
            f"verification {verification!r} must be one of: "
            + ", ".join(AUTHORITY_VERIFICATION)
        )
    if not isinstance(entry.get("treatment_checked"), bool):
        problems.append("treatment_checked is required: true or false")
    if not str(entry.get("treatment", "") or "").strip():
        problems.append(
            "treatment is required: subsequent treatment, or state "
            "that it was not checked"
        )
    if verification == "verified" and not entry.get("treatment_checked"):
        problems.append(
            "a verified proposition needs its subsequent treatment "
            "checked; mark it provisional until then"
        )
    return problems


# ---- authorization ledger and action policy -----------------------------------
#
# Preparing analysis is not executing it. Every tool carries an
# action class: research (read-only investigation), draft
# (record writes reviewed before delivery), request (pauses for
# a person), decide (approval decisions), execute (external legal
# consequences), confirm (verifying an external outcome). No
# execute or confirm tools exist today — analysis only — so the
# ledger and policy below are the reusable core awaiting them:
# the classification-coverage test fails if a tool is added
# without an explicit class, forcing security review before any
# external action can slip in silently.

ACTION_CLASSES = ("research", "draft", "request", "decide", "execute", "confirm")

TOOL_ACTION_CLASSES = {
    "list_documents": "research",
    "analyze_document": "research",
    "search_corpus": "research",
    "sweep_issues": "research",
    "work_status": "research",
    "resolve_query": "research",
    "scan_document": "research",
    "resolve_branch": "research",
    "check_signatures": "research",
    "suggest_next_action": "research",
    "set_checklist": "draft",
    "update_plan": "draft",
    "record_fact": "draft",
    "record_facts": "draft",
    "note_context": "draft",
    "note_source": "draft",
    "register_authority": "draft",
    "assess_authority": "draft",
    "set_evidence_needs": "draft",
    "open_branch": "draft",
    "plan_research": "draft",
    "answer_foundational": "draft",
    "analyze_element": "draft",
    "calculate": "draft",
    "adversarial_review": "draft",
    "identify_task": "draft",
    "reclassify_task": "draft",
    "plan_workflow": "draft",
    "triage_gap": "draft",
    "declare_work_product": "draft",
    "validate_decomposition": "draft",
    "review_draft": "draft",
    "flag_privileged": "draft",
    "scan_redactions": "draft",
    "submit_memo": "draft",
    "request_evidence": "request",
    "request_approval": "request",
    "decide_approval": "decide",
}

#: Tools with external legal consequences. Empty: the system cannot
#: act externally today. Adding one requires the approval mechanics
#: (request → grant → execute → confirm) and updates this tuple
#: plus its coverage test deliberately.
EXECUTE_TOOLS: tuple[str, ...] = ()

APPROVAL_DECISIONS = ("grant", "deny")


def requires_approval(tool: str) -> bool:
    """Whether a tool needs a granted approval to run."""
    return tool in EXECUTE_TOOLS


def validate_approval_request(entry: dict) -> list[str]:
    """Deterministic problems with one approval request, else []."""
    if not isinstance(entry, dict):
        return ["the request must be an object"]
    return [f"{field} is required" for field in _missing(
        entry, ("action", "scope", "reason")
    )]


def validate_approval_decision(entry: dict) -> list[str]:
    """Deterministic problems with one approval decision, else []."""
    if not isinstance(entry, dict):
        return ["the decision must be an object"]
    problems = []
    if entry.get("decision") not in APPROVAL_DECISIONS:
        problems.append(
            f"decision {entry.get('decision')!r} must be one of: "
            + ", ".join(APPROVAL_DECISIONS)
        )
    for field in _missing(entry, ("decided_by",)):
        problems.append(f"{field} is required: who decided")
    return problems


def approval_status(case_data: dict, action: str) -> str:
    """Latest approval state for an action: granted, stale, denied, pending, none.

    A grant staled by reclassification reports stale, not
    granted: the old policy no longer authorizes (closeout §8).
    """
    data = case_data if isinstance(case_data, dict) else {}
    approvals = [a for a in (data.get("approvals") or []) if isinstance(a, dict)]
    for entry in reversed(approvals):
        if str(entry.get("action", "")) == str(action or ""):
            decision = entry.get("decision", "")
            if decision in APPROVAL_DECISIONS:
                if decision == "grant" and entry.get("stale"):
                    return "stale"
                return "granted" if decision == "grant" else "denied"
            return "pending"
    return "none"


def pending_approvals(case_data: dict) -> list[dict]:
    """Approval requests still awaiting a decision."""
    data = case_data if isinstance(case_data, dict) else {}
    return [
        a for a in (data.get("approvals") or [])
        if isinstance(a, dict) and not a.get("decision")
    ]


# ---- untrusted-document scanning ----------------------------------------------
#
# Inbox documents are untrusted data: they may contain prompt
# injection, instruction overrides, or exfiltration probes alongside
# legitimate text. Legal prose is full of imperatives ("shall",
# "must"), so patterns stay narrow — override and role-play
# markers with AI-domain context words — trading recall for
# precision. The scan reports; it never blocks. Recall is measured
# by the scan benchmark (eval/scan_run.py): mechanical
# obfuscations (newlines, leetspeak) must hit, stretch limits
# (homoglyphs, spaced letters, paraphrase) are reported.
#
# Windows exclude periods but tolerate newlines: a line break must
# not split a marker, while a period still bounds it. Normalized
# passes de-obfuscate mechanically (leetspeak, Cyrillic/Greek
# confusables, single-character spacing) and mark their exclusive
# hits normalized:true, since the span text differs from the
# source. Paraphrases with plausible benign collisions ("act as",
# "show ... instructions") stay out: those need semantics.

INJECTION_PATTERNS = (
    re.compile(
        r"\b(ignore|disregard|forget|override|set\s+aside)\b[^.]{0,50}"
        r"\b(previous|prior|all\s+previous|above|earlier|original)\b[^.]{0,30}"
        r"\b(instructions?|prompts?|rules?|guidelines?|constraints?|directives?)\b",
        re.I,
    ),
    re.compile(
        r"\b(do\s+not\s+follow|stop\s+following|disobey|"
        r"pay\s+no\s+attention\s+to)\b[^.]{0,40}"
        r"\b(instructions?|system|prompt|rules?|guidelines?)\b",
        re.I,
    ),
    re.compile(
        r"\b(system\s*prompt|new\s+instructions|you\s+are\s+now|"
        r"y\s*o\s*u\s*a\s*r\s*e\s*n\s*o\s*w|"
        r"jailbreak|prompt\s+injection|bypass\s+verification|"
        r"skip\s+verification|developer\s+mode|sudo\s+mode|"
        r"forget\s+your\s+training)\b",
        re.I,
    ),
    re.compile(
        r"\b(reveal|repeat|repeat\s+back|print|output|disclose)\b[^.]{0,40}"
        r"\b(system\s+prompt|instructions|api\s*key|secret|password|credentials?)\b",
        re.I,
    ),
)

#: Single-character spoof map for the normalized passes: leetspeak
#: plus Cyrillic/Greek lookalikes of the marker alphabet.
#: Length-preserving: offsets into the mapped text are valid for
#: the source text.
SPOOF_MAP = str.maketrans(
    {
        "0": "o", "1": "l", "3": "e", "4": "a", "5": "s",
        "7": "t", "8": "b", "@": "a", "$": "s",
        # Cyrillic lowercase lookalikes.
        "\u0430": "a", "\u0441": "c", "\u0435": "e", "\u0456": "i", "\u0458": "j",
        "\u043a": "k", "\u043c": "m", "\u043e": "o", "\u0440": "p", "\u0455": "s",
        "\u0445": "x", "\u0443": "y",
        # Cyrillic uppercase lookalikes.
        "\u0410": "a", "\u0412": "b", "\u0421": "c", "\u0415": "e", "\u0406": "i",
        "\u041a": "k", "\u041c": "m", "\u041d": "h", "\u041e": "o", "\u0420": "p",
        "\u0422": "t", "\u0425": "x",
        # Greek lookalikes.
        "\u03bf": "o", "\u03c1": "p", "\u03b5": "e",
    }
)


def _despace(text: str) -> tuple[str, list[int]]:
    """Remove spaces inside single-character runs ("i g n" -> "ign").

    Returns the collapsed text plus an index map: map[i] is the
    source offset of collapsed offset i, so hits keep honest spans.
    A space collapses only when both neighbors are single alnum
    characters (multi-letter words are never joined).
    """
    out: list[str] = []
    mapping: list[int] = []
    size = len(text)
    for pos, char in enumerate(text):
        if (
            char == " "
            and out
            and pos + 1 < size
            and out[-1].isalnum()
            and text[pos + 1].isalnum()
            and (pos < 2 or text[pos - 2] == " ")
            and (pos + 2 >= size or text[pos + 2] == " ")
        ):
            continue
        out.append(char)
        mapping.append(pos)
    return "".join(out), mapping


def _scan_compiled(text: str) -> list[dict]:
    found = []
    for pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            found.append(
                {
                    "span": " ".join(match.group(0).split()),
                    "at": match.start(),
                }
            )
    return found


def scan_text(text: str) -> list[dict]:
    """Injection/override markers in one text with match spans."""
    source = str(text or "")
    found = _scan_compiled(source)
    seen = {hit["at"] for hit in found}
    variants: list[tuple[str, list[int] | None]] = [
        (source.translate(SPOOF_MAP), None),
    ]
    despaced, backmap = _despace(source)
    if despaced != source:
        variants.append((despaced.translate(SPOOF_MAP), backmap))
    for candidate, back in variants:
        for hit in _scan_compiled(candidate):
            at = back[hit["at"]] if back is not None else hit["at"]
            if at not in seen:
                seen.add(at)
                found.append(
                    {"span": hit["span"], "at": at, "normalized": True}
                )
    return found


#: Minimum normalized quote length for withdrawn-quote matching.
#: Shorter quotes match innocent prose too often to gate on.
WITHDRAWN_QUOTE_MIN = 40


def _check_withdrawn_quotes(data: dict, memo: dict) -> list[str]:
    problems = []
    texts = " ".join(text for _, text in _memo_texts(memo))
    normalized = " ".join(str(texts or "").lower().split())
    for fact in data.get("withdrawn_facts") or []:
        if not isinstance(fact, dict):
            continue
        quote = " ".join(str(fact.get("quote", "") or "").lower().split())
        if len(quote) < WITHDRAWN_QUOTE_MIN:
            continue
        if quote in normalized:
            problems.append(
                f"the memo repeats withdrawn text from "
                f"{fact.get('document_id', '?')} (flagged privilege): "
                "remove it — withdrawn material must not reach the memo, "
                "cited or not"
            )
    return problems


# ---- live-loop decision selection ---------------------------------------------
#
# The orchestrator's engine ordering drives scripted engines; the
# live turn loop needs its own small decision aid over the ledger
# the model itself maintains. suggest_next_actions ranks the next
# useful steps from recorded state only: stale orchestration
# questions first, then the identification chain, threshold
# questions, uncovered plans, untriaged gaps, adversarial review,
# and finally filing. Pure function; the tool reports, never acts.

def suggest_next_actions(case_data: dict, limit: int = 5) -> list[dict]:
    """Ranked next actions from recorded state, most urgent first."""
    data = case_data if isinstance(case_data, dict) else {}
    suggestions: list[dict] = []

    def add(tool: str, target: str, reason: str) -> None:
        suggestions.append({"tool": tool, "target": target, "reason": reason})

    orch = data.get("orchestration") or {}
    questions = orch.get("questions") or {}
    for qid in sorted(questions):
        question = questions[qid]
        if not isinstance(question, dict):
            continue
        if question.get("status") == "stale" and question.get("issue"):
            add(
                "analyze_document",
                str(question["issue"]),
                f"orchestrated question {qid} went stale after "
                "invalidation: re-run the affected analysis, then "
                "re-validate the decomposition",
            )
    contradictions = [
        c
        for c in (orch.get("contradictions") or [])
        if isinstance(c, dict) and c.get("status") == "open"
    ]
    for contradiction in sorted(
        contradictions, key=lambda c: str(c.get("id", ""))
    ):
        about = str(contradiction.get("about", "") or "")
        question = questions.get(about, {})
        issue = (
            str(question.get("issue", "") or "")
            if isinstance(question, dict)
            else ""
        )
        cid = str(contradiction.get("id", "") or "?")
        add(
            "adversarial_review",
            cid,
            f"open contradiction {cid} touches {issue or about or 'an issue'}: "
            "investigate the conflict, then integrate or disclose it",
        )
    if not identified_mode(data):
        add(
            "identify_task",
            "",
            "no task identification recorded: identify the work first",
        )
        return suggestions[:limit]
    decompositions = [
        d for d in (data.get("decompositions") or []) if isinstance(d, dict)
    ]
    if not decompositions or decompositions[-1].get("verdict") != "pass":
        add(
            "validate_decomposition",
            "",
            "no passing decomposition: validate the question set "
            "before investigating",
        )
    answers = [a for a in (data.get("foundational") or []) if isinstance(a, dict)]
    planned = (
        decompositions[-1].get("questions", [])
        if decompositions and decompositions[-1].get("verdict") == "pass"
        else []
    )
    if planned:
        uncovered, _ = decomposition_coverage(planned, answers)
        for item in uncovered[:2]:
            add("answer_foundational", item, "planned but unanswered")
    for entry in answers:
        if entry.get("threshold") and entry.get("status") == "unresolved":
            add(
                "answer_foundational",
                str(entry.get("id", "")),
                "threshold question unresolved: it may dispose of the matter",
            )
    refs = {
        str(t.get("ref", ""))
        for t in (data.get("triages") or [])
        if isinstance(t, dict)
    }
    for entry in answers:
        if entry.get("status") == "unresolved" and entry.get("id") not in refs:
            add(
                "triage_gap",
                str(entry.get("id", "")),
                "unresolved without a triage decision",
            )
    fields = (data.get("matter", {}) or {}).get("fields", {})
    for name, field in fields.items():
        if (
            isinstance(field, dict)
            and field.get("status") == "unknown"
            and f"matter:{name}" not in refs
        ):
            add(
                "triage_gap",
                f"matter:{name}",
                "unknown matter field without a triage decision",
            )
    triage_decisions = {}
    for triage in (data.get("triages") or []):
        if isinstance(triage, dict) and triage.get("ref"):
            triage_decisions[str(triage["ref"])] = str(
                triage.get("decision", "") or ""
            )
    for entry in answers:
        ref = str(entry.get("id", "") or "")
        if (
            entry.get("status") == "unresolved"
            and ref
            and triage_decisions.get(ref) == "ask_user"
        ):
            add(
                "request_evidence",
                ref,
                "triage asked the user and the gap is still open: surface "
                "the question so the reviewer can reply or release evidence",
            )
    authorities = data.get("authorities") or {}
    if isinstance(authorities, dict):
        relied = {
            str(fact.get("authority", "") or "")
            for fact in (data.get("facts") or [])
            if isinstance(fact, dict)
        }
        for aid in sorted(authorities, key=str):
            entry = authorities[aid]
            if not isinstance(entry, dict):
                continue
            citation = str(entry.get("citation", "") or "")
            if entry.get("verification") in ("unverified", "provisional"):
                add(
                    "search_corpus",
                    str(aid),
                    f"authority {aid} is {entry.get('verification')}: verify "
                    f"{citation or 'the citation'} supports the relied-upon "
                    "proposition",
                )
            elif entry.get("treatment_checked") is not True:
                add(
                    "search_corpus",
                    str(aid),
                    f"authority {aid} has no subsequent-treatment check: "
                    f"confirm {citation or 'the citation'} remains good law",
                )
            elif str(aid) not in relied:
                add(
                    "analyze_document",
                    str(entry.get("document_id", "") or ""),
                    f"authority {aid} is registered but no fact relies on "
                    "it: inspect the source or stop relying on it",
                )
    reviews = [r for r in (data.get("adversarial") or []) if isinstance(r, dict)]
    if not reviews:
        add(
            "adversarial_review",
            "",
            "no adversarial challenge recorded before filing",
        )
    else:
        latest = str(reviews[-1].get("at", "") or "")
        later = any(
            isinstance(f, dict) and str(f.get("at", "") or "") > latest
            for f in (data.get("facts") or [])
        )
        if latest and later:
            add(
                "adversarial_review",
                "",
                "evidence arrived after the latest challenge: re-review",
            )
    workflow = (data.get("workflow") or {}).get("components") or []
    if not workflow:
        add("plan_workflow", "", "no workflow plan recorded")
    if not data.get("work_product"):
        add("declare_work_product", "", "no work product declared")
    if len(suggestions) < 2:
        add(
            "submit_memo",
            "",
            "chain complete: file the memo or review a draft first",
        )
    return suggestions[:limit]


# ---- orchestration control-plane seam -----------------------------------------
#
# The deterministic control plane (agent_kit/orchestration/) owns
# execution ordering, premise versioning, and invalidation; this
# module owns the filing record. The seam runs both ways without
# either side importing the other:
#
# - orchestration_seeds() renders recorded foundational answers as
#   control-plane seed dicts ({text, class, deps, premises, issue}),
#   so a run can seed the plane from the validated ledger instead
#   of re-deriving questions. FQ numeric order is dependency-safe
#   (record-time validation only allows backward references).
# - the filing gate reads orchestration state defensively: asserted
#   findings on issues with stale or unsettled control-plane
#   questions fail until the plane re-runs or discloses them.
# Malformed orchestration state never fails filing on its own
# (that plane validates itself when it runs); it draws an advisory.

ORCHESTRATION_CLASS_MAP = {
    "facts": "evidence",
    "evidence": "evidence",
    "adverse": "review",
}

ORCHESTRATION_UNSETTLED = (
    "proposed",
    "ready",
    "assigned",
    "in_progress",
    "blocked",
)


def orchestration_seeds(case_data: dict) -> list[dict]:
    """Recorded answers as control-plane seed dicts, in FQ order."""
    data = case_data if isinstance(case_data, dict) else {}
    answers = [a for a in (data.get("foundational") or []) if isinstance(a, dict)]
    positions = {
        entry.get("id"): num + 1
        for num, entry in enumerate(answers)
        if entry.get("id")
    }
    seeds = []
    for entry in answers:
        deps = [
            f"Q{positions[dep]}"
            for dep in (entry.get("depends_on") or [])
            if dep in positions
        ]
        issue = str(entry.get("issue", "") or "")
        seeds.append(
            {
                "text": f"[{entry.get('class', '')}] "
                + str(entry.get("question", ""))
                + (f" (issue {issue})" if issue else ""),
                "class": ORCHESTRATION_CLASS_MAP.get(
                    str(entry.get("class", "")), "analysis"
                ),
                "deps": deps,
                "premises": [],
                "issue": issue,
            }
        )
    return seeds


def orchestration_staleness(
    case_data: dict, memo: dict
) -> tuple[list[str], list[str]]:
    """Filing problems/advisories from control-plane state.

    Asserted findings on issues whose control-plane questions are
    stale or unsettled fail; disclosed or escalated questions and
    open contradictions touching asserted issues draw advisories.
    """
    data = case_data if isinstance(case_data, dict) else {}
    memo = memo if isinstance(memo, dict) else {}
    problems, advisory = [], []
    orch = data.get("orchestration")
    if orch is None:
        return problems, advisory
    if not isinstance(orch, dict) or not isinstance(
        orch.get("questions"), dict
    ):
        return problems, [
            "orchestration state is present but unreadable — confirm "
            "no stale questions before relying on this filing"
        ]
    questions = orch["questions"]
    by_issue: dict[str, list] = {}
    for qid, question in questions.items():
        if not isinstance(question, dict):
            continue
        issue = str(question.get("issue", "") or "")
        if issue:
            by_issue.setdefault(issue, []).append((qid, question))
    if not by_issue:
        return problems, advisory
    disclosures = " ".join(str(d) for d in (orch.get("disclosures") or []))
    invalidations = [
        inv for inv in (orch.get("invalidations") or []) if isinstance(inv, dict)
    ]

    def latest_reason(qid: str) -> str:
        for inv in reversed(invalidations):
            if qid in (inv.get("staled") or []):
                reason = str(inv.get("reason", "") or "")
                return f" ({reason})" if reason else ""
        return ""

    for finding in memo.get("findings") or []:
        if not isinstance(finding, dict) or finding.get("status") != "asserted":
            continue
        item = str(finding.get("item", ""))
        for qid, question in by_issue.get(item, []):
            status = question.get("status", "")
            if status == "stale":
                problems.append(
                    f"orchestrated question {qid} for {item} is stale"
                    f"{latest_reason(qid)}: re-run the affected analysis "
                    "and re-validate the decomposition first"
                )
            elif status in ORCHESTRATION_UNSETTLED and qid not in disclosures:
                problems.append(
                    f"orchestrated question {qid} for {item} is {status}: "
                    "settle or disclose it in the orchestration plane first"
                )
            elif status in ("disclosed", "escalated"):
                advisory.append(
                    f"orchestrated question {qid} for {item} was {status} — "
                    "confirm the asserted finding accounts for it"
                )
    open_contradictions = [
        c
        for c in (orch.get("contradictions") or [])
        if isinstance(c, dict) and c.get("status") == "open"
    ]
    asserted_items = {
        str(f.get("item", ""))
        for f in (memo.get("findings") or [])
        if isinstance(f, dict) and f.get("status") == "asserted"
    }
    for contradiction in open_contradictions:
        about = str(contradiction.get("about", ""))
        question = questions.get(about, {})
        issue = str(question.get("issue", "") or "") if isinstance(question, dict) else ""
        if issue and issue in asserted_items:
            advisory.append(
                f"open orchestration contradiction "
                f"{contradiction.get('id', '?')} touches {issue} — "
                "confirm it is integrated or disclosed"
            )
    return problems, advisory


def decomposition_coverage(
    planned: list, answers: list
) -> tuple[list[str], list[str]]:
    """Match a validated plan against recorded answers.

    Returns (uncovered planned pairs, unplanned recorded pairs).
    Pairs are (class, issue); a planned pair is covered by a
    recorded answer with the same class and issue, where a blank
    planned issue is covered by any answer of that class.
    """
    uncovered = []
    for entry in planned:
        if not isinstance(entry, dict):
            continue
        qclass, issue = entry.get("class"), str(entry.get("issue", "") or "")
        covered = any(
            isinstance(a, dict)
            and a.get("class") == qclass
            and (not issue or str(a.get("issue", "") or "") == issue)
            for a in answers
        )
        if not covered:
            uncovered.append(
                f"planned {qclass} question"
                + (f" for {issue}" if issue else "")
                + " has no recorded answer"
            )
    planned_pairs = {
        (entry.get("class"), str(entry.get("issue", "") or ""))
        for entry in planned
        if isinstance(entry, dict)
    }
    unplanned = []
    for entry in answers:
        if not isinstance(entry, dict):
            continue
        pair = (entry.get("class"), str(entry.get("issue", "") or ""))
        blank = (entry.get("class"), "")
        if pair not in planned_pairs and blank not in planned_pairs:
            unplanned.append(
                f"{entry.get('class')} answer {entry.get('id', '?')} is "
                "outside the validated plan"
            )
    return uncovered, unplanned
