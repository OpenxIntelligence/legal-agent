"""Versioned source registry and query routing for legal research.

The registry describes where law lives and how to characterize what
comes back: repositories, authority types, citation conventions,
language and calendar needs, historical-version handling, and access
limits. It never contains "the law" as static text, and nothing here
fetches: every repository's access is operator-supplied — supplied
pages enter through the evidence channel (documents), where quotes
are verified like any other evidence.

Routing is deterministic and table-driven: query class, scope, regime,
and context map to a plan with repositories, ordered steps, blocking
needs, and warnings. Location never implies governing law: ambiguous
scopes refuse to route until clarified.
"""

from __future__ import annotations

import re

REGISTRY_VERSION = 1

QUERY_CLASSES = (
    "exact_lookup",
    "local_factual",
    "corpus_pattern",
    "chronology",
    "applicable_law",
    "rule_exception",
    "counterevidence",
    "missing_information",
    "verification",
)

LOCAL_CLASSES = (
    "exact_lookup",
    "local_factual",
    "corpus_pattern",
    "chronology",
    "counterevidence",
)

REPOS = {
    "ew-legislation": {
        "label": "legislation.gov.uk",
        "region": "ew",
        "authority": "official statute source",
        "scope_note": (
            "UK-wide and devolved legislation in one archive: always check "
            "the Extent field. An England-and-Wales extent is not Scotland "
            "or Northern Ireland."
        ),
        "languages": ["English"],
        "calendar_note": "",
        "historical": "point-in-time versions available; retrieve the version in force at the event date",
        "citation_format": "Short Title Year, s X (e.g. Consumer Rights Act 2015, s 11)",
        "access": "operator-supplied",
    },
    "ew-judiciary": {
        "label": "judiciary.uk",
        "region": "ew",
        "authority": "official courts source",
        "scope_note": "Judgments and guidance from England and Wales courts and tribunals.",
        "languages": ["English"],
        "calendar_note": "",
        "historical": "decisions carry their handed-down date; later treatment must be checked separately",
        "citation_format": "neutral citation (e.g. [2024] EWCA Civ 1)",
        "access": "operator-supplied",
    },
    "bailii": {
        "label": "BAILII",
        "region": "ew",
        "authority": "free archive, not the court itself",
        "scope_note": "Case law archive; verify consequential passages against an official source.",
        "languages": ["English"],
        "calendar_note": "",
        "historical": "decisions carry their date; later treatment must be checked separately",
        "citation_format": "neutral citation (e.g. [2024] EWHC 1 (Comm))",
        "access": "operator-supplied",
    },
    "ca-leginfo": {
        "label": "leginfo.legislature.ca.gov",
        "region": "ca",
        "authority": "official statute source",
        "scope_note": "California codes; a California statute is not federal law and not another state's law.",
        "languages": ["English"],
        "calendar_note": "",
        "historical": "session history available; retrieve the version in force at the event date",
        "citation_format": "Cal. [Code] § X (e.g. Cal. Civ. Code § 1542)",
        "access": "operator-supplied",
    },
    "ca-opinions": {
        "label": "courts.ca.gov opinions",
        "region": "ca",
        "authority": "official courts source",
        "scope_note": "Published California appellate opinions.",
        "languages": ["English"],
        "calendar_note": "",
        "historical": "opinions carry their filing date; later treatment must be checked separately",
        "citation_format": "official report citation (e.g. 1 Cal.5th 1)",
        "access": "operator-supplied",
    },
    "uscode": {
        "label": "uscode.house.gov",
        "region": "us-federal",
        "authority": "official statute source",
        "scope_note": (
            "Federal statutes only. A federal forum does not make every "
            "substantive issue federal: 28 USC §1652 recognizes state "
            "rules of decision where applicable."
        ),
        "languages": ["English"],
        "calendar_note": "",
        "historical": "currency stated per title; retrieve the version in force at the event date",
        "citation_format": "X USC § Y (e.g. 28 USC §1652)",
        "access": "operator-supplied",
    },
}

REGIONS = {
    "ew": {
        "label": "England and Wales",
        "repos": ["ew-legislation", "ew-judiciary", "bailii"],
        "supported": True,
    },
    "ca": {
        "label": "California",
        "repos": ["ca-leginfo", "ca-opinions"],
        "supported": True,
    },
    "us-federal": {
        "label": "US federal",
        "repos": ["uscode"],
        "supported": True,
        "note": "applicability analysis and federal statutes; substantive state issues route to their state",
    },
    "us-ny": {
        "label": "New York",
        "repos": [],
        "supported": False,
        "note": "no registered repository yet: request the text; live substantive conclusions unsupported",
    },
    "us-fl": {
        "label": "Florida",
        "repos": [],
        "supported": False,
        "note": "no registered repository yet: request the text; live substantive conclusions unsupported",
    },
    "fr": {
        "label": "France",
        "repos": [],
        "supported": False,
        "note": (
            "no registered repository yet: request the text; live substantive "
            "conclusions unsupported. French is the original language of its "
            "codes; historical versions matter (cf. ChronoLégi)."
        ),
    },
    "sa": {
        "label": "Saudi Arabia",
        "repos": [],
        "supported": False,
        "note": (
            "no registered repository yet: request the text; live substantive "
            "conclusions unsupported. Arabic governs official translations; "
            "the Hijri calendar applies to periods provided for in laws that "
            "say so (e.g. the Civil Transactions Law, art. 2), never assumed "
            "for every deadline."
        ),
    },
}

SCOPE_ALIASES = {
    "england and wales": "ew",
    "england": "ew",
    "wales": "ew",
    "ew": "ew",
    "california": "ca",
    "ca": "ca",
    "us federal": "us-federal",
    "federal": "us-federal",
    "united states federal": "us-federal",
    "new york": "us-ny",
    "ny": "us-ny",
    "florida": "us-fl",
    "fl": "us-fl",
    "france": "fr",
    "french law": "fr",
    "saudi arabia": "sa",
    "saudi": "sa",
    "ksa": "sa",
}

#: Scopes that name several legal systems at once: routing stops until
#: the caller picks one. Never default one for them.
SCOPE_CLARIFICATIONS = {
    "uk": ["England and Wales", "Scotland", "Northern Ireland"],
    "united kingdom": ["England and Wales", "Scotland", "Northern Ireland"],
    "britain": ["England and Wales", "Scotland"],
    "great britain": ["England and Wales", "Scotland"],
    "united states": ["the federal regime", "the relevant state regime"],
    "us": ["the federal regime", "the relevant state regime"],
    "usa": ["the federal regime", "the relevant state regime"],
    "america": ["the federal regime", "the relevant state regime"],
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_scope(scope: str) -> dict:
    """Map scope text to a region, a clarification, or unknown.

    Returns {"region": key} | {"clarify": [...]} | {"unknown": scope}.
    """
    key = re.sub(r"^the\s+", "", str(scope or "").strip().lower())
    if not key:
        return {"unknown": str(scope or "")}
    if key in SCOPE_CLARIFICATIONS:
        return {"clarify": list(SCOPE_CLARIFICATIONS[key])}
    if key in SCOPE_ALIASES:
        return {"region": SCOPE_ALIASES[key]}
    return {"unknown": str(scope or "")}


def route(query_class: str, context: dict | None = None) -> dict:
    """Route one research query to a plan. Pure function of its inputs.

    context: {scope?, regime?, transaction?, events_date?, connecting?,
    clause?}. Returns {query_class, route, repos, steps, needs,
    warnings, supported, blocked}. Needs are blocking; warnings
    qualify a plan that may proceed.
    """
    if query_class not in QUERY_CLASSES:
        raise ValueError(f"unknown query class {query_class!r}")
    context = dict(context or {})
    events_date = str(context.get("events_date", "") or "")
    if events_date and not DATE_RE.match(events_date):
        raise ValueError("events_date must be YYYY-MM-DD")
    plan: dict = {
        "query_class": query_class,
        "route": "local-corpus",
        "repos": [],
        "steps": [],
        "needs": [],
        "warnings": [],
        "supported": True,
        "blocked": False,
    }
    if query_class in LOCAL_CLASSES:
        plan["steps"] = list(_LOCAL_STEPS[query_class])
        return plan
    if query_class == "verification":
        plan["route"] = "verification"
        plan["steps"] = [
            "check a draft (review_draft) before filing",
            "filing runs memo rules then the claims check",
        ]
        return plan
    if query_class == "missing_information":
        plan["route"] = "evidence-request"
        plan["steps"] = [
            "target the missing source with request_evidence",
            "record what the request could resolve",
        ]
        return plan
    scope = str(context.get("scope", "") or "").strip()
    if scope:
        normalized = normalize_scope(scope)
        if "clarify" in normalized:
            plan["needs"].append(
                "which legal system: "
                + ", ".join(normalized["clarify"])
                + f" (scope {scope!r} names several)"
            )
        elif "unknown" in normalized:
            plan["needs"].append(f"an intelligible scope: {scope!r} is not registered")
    if query_class == "applicable_law":
        _route_applicable_law(plan, context)
    else:
        _route_rule_exception(plan, context)
    plan["blocked"] = bool(plan["needs"])
    return plan


_LOCAL_STEPS = {
    "exact_lookup": [
        "look up the identifier, quotation, or citation in the corpus",
        "verify the passage on its page before recording",
    ],
    "local_factual": [
        "retrieve the passage with its surrounding context",
        "record it with record_fact or record_facts",
    ],
    "corpus_pattern": [
        "sweep the corpus with coverage tracking (sweep_issues)",
        "synthesize per issue; keep opposing passages",
    ],
    "chronology": [
        "collect dated events with source references",
        "order them; check document versions for changes",
        "keep unresolved ordering explicit",
    ],
    "counterevidence": [
        "search for competing explanations and adverse passages",
        "record them with stance opposes; address them in the wording",
    ],
}


def _route_applicable_law(plan: dict, context: dict) -> None:
    plan["route"] = "registry"
    if not context.get("connecting"):
        plan["needs"].append(
            "connecting facts (residence, performance, conduct, injury, "
            "property): applicability cannot be decided without them"
        )
    plan["steps"] = [
        "separate forum, substantive law, procedure, and evidence per issue",
        "record governing-law, forum, and arbitration clauses with their signed versions",
        "open one branch per candidate regime; keep alternatives until resolved",
    ]
    if context.get("clause"):
        plan["warnings"].append(
            "a governing-law clause is recorded, not decided: assess its "
            "validity and scope before treating it as decisive"
        )
    if str(context.get("scope", "")).strip().lower() in (
        "us federal",
        "federal",
        "united states federal",
    ):
        plan["warnings"].append(
            "a federal forum does not make every substantive issue federal "
            "(cf. 28 USC §1652)"
        )
    _transaction_warnings(plan, context)


def _route_rule_exception(plan: dict, context: dict) -> None:
    plan["route"] = "registry"
    regime = str(context.get("regime", "") or "")
    if not regime:
        plan["needs"].append("the governing regime: open a branch first")
        return
    if regime not in REGIONS:
        plan["needs"].append(f"a registered regime: {regime!r} is unknown")
        return
    region = REGIONS[regime]
    plan["repos"] = list(region["repos"])
    if not region["supported"]:
        plan["supported"] = False
        plan["steps"] = [
            f"no registered repository for {region['label']}: "
            "request the text through request_evidence",
            "live substantive conclusions under this regime are unsupported",
        ]
        plan["warnings"].append(region.get("note", ""))
        return
    plan["steps"] = [
        f"consult {', '.join(plan['repos'])} ({region['label']})",
        "confirm issuing body, legal status, and subsequent treatment",
    ]
    events_date = str(context.get("events_date", "") or "")
    if events_date:
        plan["steps"].append(
            f"retrieve the version in force at {events_date}: today's text "
            "is not automatically the relevant text"
        )
    else:
        plan["steps"].append(
            "confirm the version in force at the event date before relying on it"
        )
    for repo_id in plan["repos"]:
        repo = REPOS[repo_id]
        if repo.get("scope_note"):
            plan["steps"].append(f"{repo_id}: {repo['scope_note']}")
        if repo["languages"] != ["English"]:
            plan["warnings"].append(
                f"{repo_id}: verify against the {', '.join(repo['languages'])} original"
            )
        if repo.get("calendar_note"):
            plan["warnings"].append(f"{repo_id}: {repo['calendar_note']}")
    if context.get("clause"):
        plan["warnings"].append(
            "a governing-law clause is recorded, not decided: assess its "
            "validity and scope before treating it as decisive"
        )
    _transaction_warnings(plan, context)


def _transaction_warnings(plan: dict, context: dict) -> None:
    transaction = str(context.get("transaction", "") or "").strip().lower()
    if transaction == "consumer":
        plan["warnings"].append(
            "consumer transaction: check consumer protections and limits on "
            "choice-of-law and forum clauses"
        )


def validate_provenance(repo_id: str, citation: str, version: str, language: str) -> list:
    """Deterministic problems with supplied-authority provenance, else [].

    The repository must be registered, the citation and version
    non-empty (today's text is not automatically the relevant text),
    and non-English-original sources must record their language.
    """
    problems = []
    repo = REPOS.get(str(repo_id or ""))
    if repo is None:
        return [f"repository {repo_id!r} is not registered"]
    if not str(citation or "").strip():
        problems.append("a citation is required (format: " + repo["citation_format"] + ")")
    if not str(version or "").strip():
        problems.append(
            "the source version is required: retrieve the version in force "
            "at the event date"
        )
    if repo["languages"] != ["English"] and not str(language or "").strip():
        problems.append(
            f"record the language: {repo['label']} originals are "
            f"{', '.join(repo['languages'])}"
        )
    return problems
