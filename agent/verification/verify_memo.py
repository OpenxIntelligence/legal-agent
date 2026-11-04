"""The memo rules.

Called when the agent calls submit_memo with VERIFICATION=on, and by
./helper verify RUN --memo.

    run_verification(ctx, memo) -> {"passed": bool, "feedback": [problem, ...]}

memo = {"conclusion": str, "findings": [{"item", "status", "summary", "citations": [
{"document_id", "page", "quote"}]}], "gaps": str}. ctx.case is the run's CaseRecord:
case.data["checklist"], case.data["facts"], case.data["signature_checks"] (each with
document_id, page, verdict), case.flagged(document_id), case.open_requests(). Compare
quotes with agent_kit.document_text.normalize.

Rules (one problem string per break, which the agent reads and fixes; passed = no problems):
- every checklist item has a finding (an item may be open: filing reports
  the investigation, it does not claim every question answered);
- a finding marked "asserted" cites at least one recorded fact for its item
  (any stance: a supported negative answer cites opposing evidence;
  whether the citations support the wording is the claims check's job)
  (opposing and context quotes may be cited);
- every citation matches a recorded fact (same document, page, and quote), and no
  citation rests only on stale facts whose quote left the page;
- no finding relies on a superseded fact unless every successor is
  cited in the SAME finding: corrections must reach the memo
  claim-locally (a correction cited by an unrelated finding
  excuses nothing), while the version history stays visible in
  the record (contested items are deliberately not blocked here:
  weighing opposition needs judgment, so one-sided contested
  assertions surface as reasoning-gate advisories for the
  reviewer instead);
- no citation is from a document flagged confidential;
- an item with needs_signature_check that is asserted has a signature check on a
  document it cites, and every such check's verdict is "consistent" (otherwise
  the item cannot be asserted: say so, and name it as a gap);
- findings say what kind of claim they make (fact, legal_rule,
  applicability, inference); an asserted legal-rule finding cites a
  legal-rule fact, an asserted fact finding cites a fact-kind fact,
  and an asserted applicability finding names its branch;
- finding branches exist, belong to the finding's item, and their
  issues are still on the checklist; the conclusion keeps every
  branch its findings use (depends_on) and names no unknown branch;
- no evidence request is still open.
"""

from __future__ import annotations

from agent_kit.case_record import CLAIM_KINDS
from agent_kit.document_text import (
    DocumentError,
    get_document,
    normalize,
    page_text,
    quote_on_page,
)
# Pinned by test (closeout §5): citation matching below is built
# against this normalizer version; a textnorm bump must review it.
from agent_kit.textnorm import (  # noqa: F401
    NORMALIZER_VERSION as QUOTE_NORMALIZER,
)


def coverage_problems(checklist: dict, findings: list) -> list[dict]:
    """Checklist items with no finding, and findings for unknown items.

    Pure and reusable: memo rules format these into feedback, and
    the audit defect ledger reads them directly. Either direction
    is a gap a reviewer must see, never a quiet pass.
    """
    by_item = {}
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        item = finding.get("item")
        if isinstance(item, str) and item.strip() and item not in by_item:
            by_item[item] = finding
    problems = []
    for item_id in (checklist or {}):
        if item_id not in by_item:
            problems.append(
                {
                    "kind": "uncovered_issue",
                    "item": item_id,
                    "detail": (
                        f"no finding for {item_id}: add a finding with status "
                        "asserted, unresolved, or escalated"
                    ),
                }
            )
    for item_id in by_item:
        if item_id not in (checklist or {}):
            problems.append(
                {
                    "kind": "unknown_item",
                    "item": item_id,
                    "detail": (
                        f"finding for unknown checklist item {item_id}: "
                        "remove it or add the item to the checklist first"
                    ),
                }
            )
    return problems


def run_verification(ctx, memo: dict) -> dict:
    case = ctx.case
    checklist = case.data.get("checklist") or {}
    facts = case.data.get("facts") or []
    signature_checks = case.data.get("signature_checks") or []
    findings = memo.get("findings") or [] if isinstance(memo, dict) else []

    feedback: list[str] = []
    by_item: dict = {}
    all_findings: list = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            feedback.append(
                f"malformed finding at index {index}: "
                "must be an object with item, status, summary, citations"
            )
            continue
        item = finding.get("item")
        if not isinstance(item, str) or not item.strip():
            feedback.append(
                f"malformed finding at index {index}: missing or invalid item id"
            )
            continue
        if finding.get("status") not in ("asserted", "unresolved", "escalated"):
            feedback.append(
                f"malformed finding for {item}: "
                f"status {finding.get('status')!r} must be asserted, unresolved, or escalated"
            )
        if not isinstance(finding.get("citations"), list):
            feedback.append(
                f"malformed finding for {item}: citations must be a list"
            )
        if item in by_item:
            feedback.append(
                f"duplicate finding for {item}: only one finding per checklist item"
            )
        else:
            by_item[item] = finding
        all_findings.append((item, finding))

    for problem in coverage_problems(checklist, findings):
        feedback.append(problem["detail"])

    branches = case.data.get("branches") or {}
    if not isinstance(branches, dict):
        branches = {}
    for item_id, finding in all_findings:
        kind = finding.get("kind", "fact")
        if kind not in CLAIM_KINDS:
            feedback.append(
                f"malformed finding for {item_id}: "
                f"kind {kind!r} must be one of: {', '.join(CLAIM_KINDS)}"
            )
            continue
        branch_id = finding.get("branch") or ""
        if branch_id:
            if not isinstance(branch_id, str):
                feedback.append(
                    f"malformed finding for {item_id}: "
                    "branch must be a branch id"
                )
            else:
                branch = branches.get(branch_id)
                if not isinstance(branch, dict):
                    feedback.append(
                        f"finding for {item_id} cites unknown branch {branch_id}: "
                        "open it first"
                    )
                elif branch.get("issue") != item_id:
                    feedback.append(
                        f"finding for {item_id} cites branch {branch_id}, "
                        f"which is for {branch.get('issue')}: "
                        "a finding cites its own issue's branch"
                    )
                elif branch.get("issue") not in checklist:
                    feedback.append(
                        f"finding for {item_id} cites branch {branch_id}, "
                        "whose issue left the checklist: remove the citation"
                    )
        if finding.get("status") != "asserted":
            continue
        if kind == "applicability" and not branch_id:
            feedback.append(
                f"{item_id} asserts applicability but names no branch: "
                "open one first"
            )
        if kind in ("fact", "legal_rule"):
            citations = finding.get("citations")
            if not isinstance(citations, list):
                citations = []
            matched = [
                fact
                for citation in citations
                if isinstance(citation, dict)
                for fact in facts
                if isinstance(fact, dict) and _matches_citation(citation, fact)
            ]
            if matched and kind not in {
                fact.get("kind", "fact") for fact in matched
            }:
                need = (
                    "a legal-rule fact: record the authority first"
                    if kind == "legal_rule"
                    else "a fact-kind fact"
                )
                feedback.append(
                    f"{item_id} asserts a {kind.replace('_', ' ')} "
                    f"but cites no {kind} fact: cite {need}"
                )

    for item_id, finding in by_item.items():
        if finding.get("status") != "asserted":
            continue
        facts_for_item = [
            f for f in facts if isinstance(f, dict) and f.get("item") == item_id
        ]
        if not facts_for_item:
            feedback.append(
                f"{item_id} is asserted but has no recorded fact: "
                f"record a fact for {item_id} first"
            )
            continue
        citations = finding.get("citations")
        if not isinstance(citations, list):
            citations = []
        if not citations:
            feedback.append(
                f"{item_id} is asserted but cites no facts: "
                f"cite at least one recorded fact for {item_id}"
            )
        elif not any(
            _matches_item(cit, facts_for_item)
            for cit in citations
            if isinstance(cit, dict)
        ):
            feedback.append(
                f"{item_id} is asserted but cites no fact recorded for {item_id}: "
                f"cite a recorded fact for {item_id}"
            )

    successors: dict[str, set] = {}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        old = str(fact.get("supersedes") or "").strip()
        if old and fact.get("id"):
            successors.setdefault(old, set()).add(fact.get("id"))
    for item_id, finding in all_findings:
        citations = finding.get("citations")
        if not isinstance(citations, list):
            continue
        # Claim-local: a finding citing a superseded fact must cite
        # every successor in the SAME finding. A correction cited by
        # an unrelated finding excuses nothing.
        cited_here = {
            f.get("id")
            for citation in citations
            if isinstance(citation, dict)
            for f in facts
            if isinstance(f, dict) and _matches_citation(citation, f)
        }
        reported: set = set()
        for pos, citation in enumerate(citations):
            if not isinstance(citation, dict):
                feedback.append(
                    f"malformed citation for {item_id}: "
                    f"citation at index {pos} must be an object with document_id, page, and quote"
                )
                continue
            document_id = citation.get("document_id")
            page = citation.get("page")
            quote = citation.get("quote")
            if (
                not isinstance(document_id, str)
                or not document_id.strip()
                or not _valid_page(page)
                or not isinstance(quote, str)
                or not quote.strip()
            ):
                feedback.append(
                    f"malformed citation for {item_id}: "
                    f"citation at index {pos} must have document_id, page, and quote"
                )
                continue
            if case.flagged(document_id):
                feedback.append(
                    f"{item_id} cites {document_id} which is flagged confidential: "
                    "remove the citation and do not rely on it"
                )
            matched = [
                fact
                for fact in facts
                if isinstance(fact, dict) and _matches_citation(citation, fact)
            ]
            if not matched:
                feedback.append(
                    f"{item_id} cites {document_id} p{page} with no matching recorded fact: "
                    "record the quote as a fact first or fix the citation"
                )
                continue
            stale = _stale_problem(case.root, item_id, citation, matched)
            if stale:
                feedback.append(stale)
            for fact in matched:
                missing = sorted(
                    successors.get(str(fact.get("id") or ""), set())
                    - cited_here
                )
                key = (str(fact.get("id") or ""), tuple(missing))
                if missing and key not in reported:
                    reported.add(key)
                    names = ", ".join(missing)
                    plural = len(missing) > 1
                    feedback.append(
                        f"{item_id} cites {fact.get('id')}, superseded by "
                        f"{names}: cite the current "
                        f"{'versions' if plural else 'version'} alongside "
                        f"{'them' if plural else 'it'} so the "
                        f"{'corrections reach' if plural else 'correction reaches'}"
                        " the memo"
                    )

    for item_id, finding in by_item.items():
        if finding.get("status") != "asserted":
            continue
        entry = checklist.get(item_id) or {}
        if not entry.get("needs_signature_check"):
            continue
        citations = finding.get("citations")
        if not isinstance(citations, list):
            citations = []
        cited_docs = {
            c.get("document_id")
            for c in citations
            if isinstance(c, dict)
            and isinstance(c.get("document_id"), str)
            and c.get("document_id").strip()
        }
        relevant = [
            c
            for c in signature_checks
            if isinstance(c, dict) and c.get("document_id") in cited_docs
        ]
        if not relevant:
            feedback.append(
                f"{item_id} needs a signature check but no cited document was checked: "
                "run check_signatures on a cited document"
            )
        else:
            for check in relevant:
                if check.get("verdict") != "consistent":
                    feedback.append(
                        f"{item_id} cannot be asserted: "
                        f"{check.get('document_id')} p{check.get('page')} "
                        f"is {check.get('verdict')}, name {item_id} as a gap"
                    )

    depends = memo.get("depends_on") if isinstance(memo, dict) else None
    if depends is None:
        depends = []
    if not isinstance(depends, list) or any(
        not isinstance(dep, str) for dep in depends
    ):
        feedback.append(
            "malformed conclusion dependencies: "
            "depends_on must be a list of branch ids"
        )
        depends = []
    else:
        for branch_id in depends:
            if branch_id not in branches:
                feedback.append(
                    f"the conclusion depends on unknown branch {branch_id}: "
                    "open it first"
                )
    cited = {
        finding.get("branch")
        for _, finding in all_findings
        if isinstance(finding.get("branch"), str) and finding.get("branch")
    }
    for branch_id in sorted(cited):
        if branch_id in branches and branch_id not in depends:
            feedback.append(
                f"the conclusion drops {branch_id}, which its findings depend on: "
                "keep every finding branch in the conclusion's depends_on"
            )

    for request in case.open_requests():
        feedback.append(
            f"request is still open: {request.get('id')} for {request.get('item')}: "
            "wait for the reply before filing the memo"
        )

    for document_id in sorted(case.source_drift()):
        feedback.append(
            f"{document_id} changed since its provenance was noted: "
            "re-note the source before filing"
        )

    return {"passed": not feedback, "feedback": feedback}


def _stale_problem(root, item_id: str, citation: dict, matched: list) -> str | None:
    """A problem if every matched fact is stale (quote gone from the page), else None.

    A citation stays valid when any matched fact is still on its page, or when
    staleness cannot be assessed (no saved transcript): absence of a check is
    never reported as staleness.
    """
    document_id, page = citation.get("document_id"), citation.get("page")
    try:
        current = get_document(root, document_id)
    except DocumentError:
        return (
            f"{item_id} cites {document_id} p{page}, which is no longer "
            "in the inbox: remove the citation"
        )
    changed = False
    for fact in matched:
        try:
            text = page_text(root, fact.get("document_id"), fact.get("page"))
        except DocumentError:
            continue
        if text is None:
            return None
        if quote_on_page(str(fact.get("quote", "")), text):
            return None
        if fact.get("sha256") and fact.get("sha256") != current.get("sha256"):
            changed = True
    problem = (
        f"{item_id} cites a stale fact ({document_id} p{page}): "
        "the quote is no longer on that page"
    )
    if changed:
        problem += "; the document changed since the fact was recorded"
    return problem + ": re-record the fact from the current text"


def _same_page(first, second) -> bool:
    try:
        return int(first) == int(second)
    except (TypeError, ValueError):
        return first == second


def _valid_page(page) -> bool:
    try:
        int(page)
        return True
    except (TypeError, ValueError):
        return False


def _matches_citation(citation: dict, fact: dict) -> bool:
    if not isinstance(citation.get("document_id"), str):
        return False
    if not _valid_page(citation.get("page")):
        return False
    citation_quote = citation.get("quote")
    fact_quote = fact.get("quote")
    if not isinstance(citation_quote, str) or not isinstance(fact_quote, str):
        return False
    return (
        fact.get("document_id") == citation.get("document_id")
        and _same_page(fact.get("page"), citation.get("page"))
        and normalize(fact_quote) == normalize(citation_quote)
    )


def _matches_item(citation: dict, facts_for_item: list) -> bool:
    return any(_matches_citation(citation, fact) for fact in facts_for_item)
