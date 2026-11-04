"""PageRank authority scoring: known values and graph behavior.

Pins the iteration against hand-solved systems (not the
implementation against itself) plus the legal shape that
matters: a much-cited landmark must outrank obscure opinions.
"""

from __future__ import annotations

import unittest

from agent_kit import pagerank


class KnownValuesTest(unittest.TestCase):
    # Hand-solved A -> B -> C with C dangling, d = 0.85:
    #   x = 0.05 + 0.28333z; y = 0.0925 + 0.52417z;
    #   z = 0.06977 + 1.18605y  =>  z = 0.47441,
    #   y = 0.34118, x = 0.18441 (sums to 1).
    def test_chain_with_dangling_end(self) -> None:
        scores = pagerank.rank({"A": ["B"], "B": ["C"], "C": []})
        self.assertAlmostEqual(0.18441, scores["A"], places=4)
        self.assertAlmostEqual(0.34118, scores["B"], places=4)
        self.assertAlmostEqual(0.47441, scores["C"], places=4)
        self.assertAlmostEqual(
            1.0, sum(scores.values()), places=9
        )

    def test_mutual_pair_splits_evenly(self) -> None:
        scores = pagerank.rank({"A": ["B"], "B": ["A"]})
        self.assertAlmostEqual(0.5, scores["A"], places=9)
        self.assertAlmostEqual(0.5, scores["B"], places=9)


class GraphBehaviorTest(unittest.TestCase):
    def test_landmark_outranks_obscure(self) -> None:
        edges = {
            f"opinion-{n}": ["landmark", f"obscure-{n}"]
            for n in range(5)
        }
        edges["landmark"] = []
        scores = pagerank.rank(edges)
        top = max(scores, key=scores.get)
        self.assertEqual("landmark", top)
        self.assertGreater(
            scores["landmark"], scores["obscure-0"] * 2
        )

    def test_target_only_nodes_join(self) -> None:
        scores = pagerank.rank({"A": ["B"]})
        self.assertIn("B", scores)
        self.assertGreater(scores["B"], scores["A"])

    def test_empty_graph_empty_scores(self) -> None:
        self.assertEqual({}, pagerank.rank({}))

    def test_bad_adjacency_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pagerank.rank({"A": "B"})

    def test_deterministic(self) -> None:
        edges = {"A": ["B", "C"], "B": ["C"], "C": ["A"]}
        self.assertEqual(pagerank.rank(edges), pagerank.rank(edges))


class VariantValuesTest(unittest.TestCase):
    # Hand-solved PPR on A <-> B seeded [A], d = 0.85:
    #   rA = 0.15 + 0.85 rB; rB = 0.85 rA  =>  rA = 0.54054,
    #   rB = 0.45946.
    def test_personalized_seed_bias(self) -> None:
        scores = pagerank.personalized({"A": ["B"], "B": ["A"]}, ["A"])
        self.assertAlmostEqual(0.54054, scores["A"], places=4)
        self.assertAlmostEqual(0.45946, scores["B"], places=4)

    def test_personalized_no_usable_seeds_zero(self) -> None:
        scores = pagerank.personalized({"A": ["B"]}, ["ZZZ"])
        self.assertEqual({"A": 0.0, "B": 0.0}, scores)

    def test_personalized_dangling_returns_to_seeds(self) -> None:
        # A -> B with B dangling, seeded [A]: B's mass returns
        # to the seed distribution, which here equals B -> A —
        # the same system as the mutual pair above.
        scores = pagerank.personalized({"A": ["B"], "B": []}, ["A"])
        self.assertAlmostEqual(0.54054, scores["A"], places=4)
        self.assertAlmostEqual(0.45946, scores["B"], places=4)

    def test_personalized_no_leak_into_disconnected(self) -> None:
        # Off-topic cluster T -> BIG shares no path with seed A:
        # seed-biased dangling keeps its mass at ~0 instead of
        # siphoning uniform rank (graph-experiment finding).
        scores = pagerank.personalized(
            {"A": ["B"], "B": [], "T": ["BIG"], "BIG": []}, ["A"]
        )
        self.assertGreater(scores["A"], scores["B"])
        self.assertAlmostEqual(0.0, scores["BIG"], places=6)
        self.assertAlmostEqual(0.0, scores["T"], places=6)

    def test_hits_authority_star(self) -> None:
        scores = pagerank.hits_authority(
            {"X": ["L"], "Y": ["L"], "Z": ["L"], "L": []}
        )
        self.assertAlmostEqual(1.0, scores["L"], places=9)
        for node in ("X", "Y", "Z"):
            self.assertAlmostEqual(0.0, scores[node], places=9)

    def test_hits_authority_symmetric(self) -> None:
        scores = pagerank.hits_authority({"A": ["B", "C"]})
        self.assertAlmostEqual(0.5, scores["B"], places=9)
        self.assertAlmostEqual(0.5, scores["C"], places=9)
        self.assertAlmostEqual(0.0, scores["A"], places=9)


if __name__ == "__main__":
    unittest.main()
