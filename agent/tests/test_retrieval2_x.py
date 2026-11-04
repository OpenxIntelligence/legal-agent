"""Experimental-arm smoke coverage (RET-3 record integrity).

Every arm must run the full frozen set without crashing and
return the documented search shape. The base arm must also
reproduce the real search_corpus ranks exactly: it is a
replication, and any drift means the experiment record no
longer describes the baseline it measured. If that check
fails after a search_corpus change, update this module's
header note with the divergence — do not silently re-tune
the experiment to match new behavior.

DIVERGENCE (coverage completion, Phase 3, + Porter-lite
stemming, experiment #4): the base arm replicates the
pre-completion, pre-stemming ranker (its term pipeline is
frozen in retrieval2_x._terms). 26 queries diverge:
completion appends are strictly additive; stemming reorders
by conflated terms. DIVERGENCE pins both sides per query;
any other drift still fails. Known costs: j-plan-year ranks
a prohibited V1 page first (required V2,2 also newly
present); w-insurer (phase-3 set) over-fires at retriever
level. Recall/precision tradeoffs, owned by the conclusion
layer, which is unchanged.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent_kit import document_text
from eval import retrieval2_cases as cases
from eval import retrieval2_run as runner
from eval import retrieval2_x as arms

# Pinned ranker divergence: {qid: (frozen-base ranks, current ranks)}.
DIVERGENCE = {
    'p-delay-cost': (
        [('HB-CONTRACT', 2)],
        [('HB-ENGINEER', 1), ('HB-CONTRACT', 2), ('HB-CO-03', 2)],
    ),
    'p-new-deadline': (
        [('HB-CO-03', 1)],
        [('HB-CO-03', 1), ('HB-ENGINEER', 1)],
    ),
    'p-steel': (
        [('HB-ENGINEER', 1)],
        [('HB-ENGINEER', 1), ('HB-ENGINEER', 2)],
    ),
    'p-payout-job': (
        [('MB-PLAN-V2', 2), ('MB-EMAIL', 1), ('MB-HRNOTE', 1)],
        [('MB-HRNOTE', 1), ('MB-PLAN-V1', 1)],
    ),
    'p-rating-num': (
        [('MB-REVIEW', 1)],
        [('MB-REVIEW', 1), ('MB-EMAIL', 1)],
    ),
    'p-easement': (
        [('QT-AGREEMENT', 2), ('QT-AGREEMENT', 1), ('QT-TITLE', 1)],
        [('QT-TITLE', 1)],
    ),
    'm-completion': (
        [('HB-MINUTES', 2)],
        [('HB-CO-03', 2), ('HB-MINUTES', 2)],
    ),
    'm-rain': (
        [('HB-NOTICE', 2)],
        [('HB-NOTICE', 2), ('HB-NOTICE', 1), ('HB-ENGINEER', 1)],
    ),
    'm-bonus-rules': (
        [('MB-PLAN-V1', 1), ('MB-PLAN-V1', 2), ('MB-PLAN-V2', 1), ('MB-PLAN-V2', 2), ('MB-HRNOTE', 1)],
        [('MB-PLAN-V2', 2), ('MB-PLAN-V1', 1)],
    ),
    'm-reyes-case': (
        [('MB-HRNOTE', 1)],
        [('MB-HRNOTE', 1), ('MB-REVIEW', 1), ('MB-TERM', 1)],
    ),
    'm-title': (
        [('QT-TITLE', 1)],
        [('QT-TITLE', 1), ('QT-TITLE', 2)],
    ),
    'm-closing': (
        [('QT-STATUTE', 2), ('QT-AGREEMENT', 2), ('QT-CLOSING', 1)],
        [('QT-AGREEMENT', 2), ('QT-STATUTE', 2)],
    ),
    'a-weather-exc': (
        [('HB-NOTICE', 2)],
        [('HB-NOTICE', 2), ('HB-MINUTES', 2)],
    ),
    'a-promise': (
        [('MB-REVIEW', 2), ('MB-EMAIL', 1), ('MB-OFFER', 2)],
        [('MB-REVIEW', 2), ('MB-EMAIL', 1), ('MB-OFFER', 2), ('MB-PLAN-V1', 1)],
    ),
    'j-caldera-law': (
        [('QT-AGREEMENT', 3)],
        [('QT-AGREEMENT', 3), ('QT-AGREEMENT', 1)],
    ),
    'j-plan-year': (
        [('MB-PLAN-V2', 1)],
        [('MB-PLAN-V1', 2), ('MB-PLAN-V2', 2), ('MB-PLAN-V2', 1)],
    ),
    'j-start-date': (
        [('MB-OFFER', 2)],
        [('MB-OFFER', 2), ('MB-EMAIL', 2)],
    ),
    'j-notice-date': (
        [('HB-NOTICE', 1)],
        [('HB-NOTICE', 1), ('HB-ENGINEER', 2)],
    ),
    'c-slip-size': (
        [('HB-MINUTES', 2)],
        [('HB-MINUTES', 2), ('HB-NOTICE', 1)],
    ),
    'x-ld-amount': (
        [('HB-CONTRACT', 2)],
        [('HB-CONTRACT', 2), ('HB-CO-03', 1)],
    ),
    'r-privileged': (
        [('HB-ENGINEER', 2)],
        [('HB-ENGINEER', 2), ('HB-CONTRACT', 1)],
    ),
    't-crossing': (
        [('QT-TITLE', 1), ('QT-SURVEY', 1)],
        [('QT-SURVEY', 1), ('QT-TITLE', 1)],
    ),
    'v-steel-days': (
        [('HB-ENGINEER', 1), ('HB-MINUTES', 2)],
        [('HB-ENGINEER', 1), ('HB-MINUTES', 2), ('HB-NOTICE', 1)],
    ),
    's-rain-both': (
        [('HB-NOTICE', 2), ('HB-NOTICE', 1)],
        [('HB-NOTICE', 1), ('HB-NOTICE', 2)],
    ),
    's-against-ext': (
        [('HB-CO-03', 1), ('HB-CONTRACT', 2), ('HB-NOTICE', 2), ('HB-MINUTES', 2)],
        [('HB-CO-03', 1), ('HB-NOTICE', 1)],
    ),
    's-no-liens': (
        [('QT-TITLE', 2)],
        [('QT-TITLE', 2), ('QT-TITLE', 1)],
    ),
}



class ExperimentSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parent = Path(tempfile.mkdtemp(prefix="r2x-smoke-"))
        cls.roots = {}
        for matter in cases.CORPORA:
            root, _ = runner.build_matter_root(matter, cls.parent)
            cls.roots[matter] = root
        cls.addClassCleanup(shutil.rmtree, cls.parent,
                            ignore_errors=True)

    def test_all_arms_return_documented_shape(self) -> None:
        for name, fn in arms.ARMS.items():
            for spec in cases.QUERIES:
                result = fn(
                    self.roots[spec[1]], spec[2],
                    flagged=set(
                        cases.CORPORA[spec[1]]["restricted"]))
                self.assertEqual(
                    {"query", "documents", "passage_count",
                     "unindexed", "restricted", "truncated"},
                    set(result), f"{name} {spec[0]}")
                for document in result["documents"]:
                    for passage in document["passages"]:
                        self.assertIn("page", passage)
                        self.assertIn("passage", passage)

    def test_base_arm_reproduces_real_search(self) -> None:
        for spec in cases.QUERIES:
            root = self.roots[spec[1]]
            flagged = set(cases.CORPORA[spec[1]]["restricted"])
            real = runner.ranks_of(
                document_text.search_corpus(root, spec[2],
                                            flagged=flagged))
            replica = runner.ranks_of(
                arms.search_base(root, spec[2], flagged=flagged))
            if spec[0] in DIVERGENCE:
                base, new = DIVERGENCE[spec[0]]
                self.assertEqual(
                    base, replica,
                    f"base arm moved on {spec[0]}: the experiment "
                    "record must keep measuring the old baseline",
                )
                self.assertEqual(
                    new, real,
                    f"pinned divergence stale on {spec[0]}: re-verify "
                    "the additive completion ranks by hand",
                )
                continue
            self.assertEqual(
                real, replica,
                f"experiment drift on {spec[0]}: base arm no longer "
                "reproduces search_corpus; annotate, do not re-tune",
            )


if __name__ == "__main__":
    unittest.main()
