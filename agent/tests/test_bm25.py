"""BM25 fallback ranking: formula, folding, and silence guards.

Pins the Okapi formula against hand-computed values (not the
implementation against itself), the number-word folding, and
the near-miss guard that keeps contradictions silent while
paraphrases pass through.
"""

from __future__ import annotations

import math
import unittest

from agent_kit import bm25


class TokenizeTest(unittest.TestCase):
    def test_lower_punctuation_stops(self) -> None:
        self.assertEqual(
            ["lindgren", "said", "platform"],
            bm25.tokenize("Lindgren said: the platform!"),
        )

    def test_empty_query_no_terms(self) -> None:
        self.assertEqual([], bm25.tokenize("is the, and!"))
        self.assertEqual([], bm25.tokenize(""))


class NumberFoldingTest(unittest.TestCase):
    def test_hyphen_and_case_variants(self) -> None:
        self.assertEqual(["21"], bm25.query_terms("twenty-one"))
        self.assertEqual(["21"], bm25.query_terms("Twenty One"))

    def test_multipliers(self) -> None:
        self.assertEqual(["105"], bm25.query_terms("one hundred five"))
        self.assertEqual(["2000"], bm25.query_terms("two thousand"))

    def test_digits_canonicalize(self) -> None:
        self.assertEqual(["21"], bm25.query_terms("021"))

    def test_non_numbers_pass_through(self) -> None:
        self.assertEqual(
            ["defect", "21"], bm25.query_terms("defects: twenty-one")
        )


class StemTest(unittest.TestCase):
    def test_conflations(self) -> None:
        pairs = [
            ("slipped", "slip"),
            ("employment", "employed"),
            ("recorded", "record"),
            ("disputed", "disputes"),
            ("completed", "completion"),
            ("rated", "rating"),
            ("warns", "warning"),
            ("extends", "extend"),
            ("passages", "passage"),
        ]
        for left, right in pairs:
            self.assertEqual(
                bm25.stem(left), bm25.stem(right), (left, right))

    def test_guards(self) -> None:
        self.assertEqual("21", bm25.stem("21"))
        self.assertEqual("", bm25.stem(""))
        # short words and digits survive; stops never reach stem
        self.assertEqual("bus", bm25.stem("bus"))
        self.assertEqual("due", bm25.stem("due"))
        self.assertEqual(["defect", "21"],
                         bm25.query_terms("defects: twenty-one"))


class FormulaTest(unittest.TestCase):
    # Hand-computed Okapi BM25 (k1=1.5, b=0.75):
    #   D1=[cat,sat] D2=[dog,sat] D3=[fish,swim,deep], avgdl=7/3.
    # idf(cat) = ln(1 + 2.5/1.5) = ln(2.6667) = 0.98083.
    # D1 length norm = 0.25 + 0.75*2/2.33333 = 0.89286.
    # D1 "cat" = 0.98083 * 2.5/(1 + 1.5*0.89286) = 1.04823.
    # idf(sat) = ln(1 + 1.5/2.5) = ln(1.6) = 0.47000.
    # D1 "sat" = 0.47000 * 1.06872 = 0.50230.
    DOCS = [
        ("d1", ["cat", "sat"]),
        ("d2", ["dog", "sat"]),
        ("d3", ["fish", "swim", "deep"]),
    ]

    def test_idf_values(self) -> None:
        self.assertAlmostEqual(
            math.log(1 + 2.5 / 1.5), bm25.idf(1, 3), places=6
        )
        self.assertAlmostEqual(
            math.log(1 + 1.5 / 2.5), bm25.idf(2, 3), places=6
        )

    def test_single_term_score(self) -> None:
        frequencies = {"cat": 1, "sat": 2}
        got = bm25.score(["cat"], ["cat", "sat"], 7 / 3, frequencies, 3)
        self.assertAlmostEqual(1.04823, got, places=4)

    def test_multi_term_ranking(self) -> None:
        ranked = bm25.rank(["cat", "sat"], self.DOCS)
        self.assertEqual(["d1", "d2", "d3"], [key for key, _ in ranked])
        self.assertAlmostEqual(1.04823 + 0.50230, ranked[0][1], places=4)
        self.assertAlmostEqual(0.50230, ranked[1][1], places=4)
        self.assertEqual(0.0, ranked[2][1])

    def test_empty_sides_score_zero(self) -> None:
        self.assertEqual(
            0.0, bm25.score([], ["cat"], 1.0, {"cat": 1}, 1)
        )
        self.assertEqual(
            0.0, bm25.score(["cat"], [], 1.0, {"cat": 1}, 1)
        )
        ranked = bm25.rank([], self.DOCS)
        self.assertTrue(all(score == 0.0 for _, score in ranked))


class NearMissTest(unittest.TestCase):
    REPORT = "Weekly status report. Overall status is green."

    def test_ratio_values(self) -> None:
        # "overall status is " (18) over a 21-char needle.
        self.assertAlmostEqual(
            18 / 21, bm25.longest_match_ratio("overall status is red", self.REPORT)
        )
        self.assertTrue(bm25.near_miss("overall status is red", self.REPORT))

    def test_exact_is_not_near_miss(self) -> None:
        self.assertFalse(bm25.near_miss("overall status is green", self.REPORT))

    def test_scattered_words_pass_through(self) -> None:
        ratio = bm25.longest_match_ratio(
            "weekly report shows green overall", self.REPORT
        )
        self.assertLess(ratio, bm25.NEAR_MISS_RATIO)
        self.assertFalse(
            bm25.near_miss("weekly report shows green overall", self.REPORT)
        )

    def test_empty_needle_never_near_miss(self) -> None:
        self.assertEqual(0.0, bm25.longest_match_ratio("", self.REPORT))
        self.assertFalse(bm25.near_miss("", self.REPORT))


if __name__ == "__main__":
    unittest.main()
