"""CourtListener resolve-only spike tests (G3, offline).

The transport seam is scripted: these tests pin OUR logic —
token handling, two-step resolution, cluster-id URL tolerance,
and explicit unavailable reasons — not CourtListener's live
schema (marked ASSUMPTION in agent_kit/caselaw.py until the
smoke test runs with a real token). No test touches the
network.
"""

from __future__ import annotations

import unittest

from agent_kit import caselaw


def _transport(script):
    calls = []

    def fetch(method, url, headers):
        calls.append((method, url, headers))
        key = "clusters/" if "/clusters/" in url else "search"
        status, payload = script[key]
        if isinstance(payload, Exception):
            raise payload
        return status, dict(payload)

    fetch.calls = calls
    return fetch


def _search(cluster_ref):
    return {
        "results": [
            {"cluster_id": cluster_ref, "case_name": "Bush v. Gore"}
        ]
    }


_CLUSTER = {
    "case_name": "Bush v. Gore",
    "court": "scotus",
    "date_filed": "2000-12-12",
}


class ResolveSpikeTest(unittest.TestCase):
    def test_found_returns_cluster(self) -> None:
        fetch = _transport({
            "search": (200, _search("111")),
            "clusters/": (200, dict(_CLUSTER)),
        })
        result = caselaw.lookup_citation(
            "531 U.S. 98", token="t", transport=fetch
        )
        self.assertTrue(result["available"])
        self.assertTrue(result["found"])
        self.assertEqual(
            {"cluster_id": "111", **_CLUSTER}, result["cluster"]
        )
        self.assertEqual("courtlistener", result["backend"])
        self.assertTrue(result["fetched_at"])
        methods = [c[0] for c in fetch.calls]
        self.assertEqual(["GET", "GET"], methods)
        self.assertIn("Token t", fetch.calls[0][2]["Authorization"])

    def test_cluster_url_tolerated(self) -> None:
        fetch = _transport({
            "search": (
                200, _search("https://www.courtlistener.com/api/rest/v3/clusters/111/")
            ),
            "clusters/": (200, dict(_CLUSTER)),
        })
        result = caselaw.lookup_citation(
            "531 U.S. 98", token="t", transport=fetch
        )
        self.assertTrue(result["found"])
        self.assertEqual("111", result["cluster"]["cluster_id"])
        self.assertIn("/clusters/111/", fetch.calls[1][1])

    def test_no_match_is_found_false(self) -> None:
        fetch = _transport({"search": (200, {"results": []})})
        result = caselaw.lookup_citation(
            "999 U.S. 999", token="t", transport=fetch
        )
        self.assertTrue(result["available"])
        self.assertFalse(result["found"])
        self.assertIsNone(result["cluster"])
        self.assertEqual(1, len(fetch.calls))

    def test_auth_rejection_is_unavailable(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                fetch = _transport({"search": (status, {})})
                result = caselaw.lookup_citation(
                    "531 U.S. 98", token="bad", transport=fetch
                )
                self.assertFalse(result["available"])
                self.assertFalse(result["found"])
                self.assertIn("auth", result["reason"])

    def test_network_failure_is_unavailable(self) -> None:
        fetch = _transport({"search": (200, TimeoutError("slow"))})
        result = caselaw.lookup_citation(
            "531 U.S. 98", token="t", transport=fetch
        )
        self.assertFalse(result["available"])
        self.assertIn("timeout", result["reason"])

    def test_missing_token_names_provisioning(self) -> None:
        result = caselaw.lookup_citation("531 U.S. 98", token=None)
        # Token resolves from env when arg is None; in this offline
        # environment no token exists, so provisioning is named.
        # (If an operator token is ever present, this test fails
        # loudly and must be re-scoped — that is the point.)
        import os

        if os.environ.get("COURTLISTENER_TOKEN") or os.environ.get(
            "COURTLISTENER_TOKEN_FILE"
        ):
            self.skipTest("operator token present")
        self.assertFalse(result["available"])
        self.assertIn("COURTLISTENER_TOKEN", result["reason"])

    def test_empty_cite_rejected(self) -> None:
        with self.assertRaises(caselaw.CaselawError):
            caselaw.lookup_citation("  ", token="t", transport=_transport({}))


class CollectionSpikeTest(unittest.TestCase):
    """RECAP, judges, and disclosures share the spike envelope."""

    def _one(self, payload):
        def fetch(method, url, headers):
            fetch.calls.append((method, url, headers))
            return 200, dict(payload)

        fetch.calls = []
        return fetch

    def test_docket_found_and_missed(self) -> None:
        fetch = self._one({"results": [{
            "id": 42, "case_name": "A v. B",
            "docket_number": "1:26-cv-00001", "court": "cand",
        }]})
        result = caselaw.lookup_docket(
            "1:26-cv-00001", "cand", token="t", transport=fetch
        )
        self.assertTrue(result["available"])
        self.assertTrue(result["found"])
        self.assertEqual(
            {"docket_id": "42", "case_name": "A v. B",
             "docket_number": "1:26-cv-00001", "court": "cand"},
            result["docket"],
        )
        self.assertIn("/dockets/?", fetch.calls[0][1])
        missed = caselaw.lookup_docket(
            "9:99-cv-99999", "cand", token="t",
            transport=self._one({"results": []}),
        )
        self.assertTrue(missed["available"])
        self.assertFalse(missed["found"])
        with self.assertRaises(caselaw.CaselawError):
            caselaw.lookup_docket("", "cand", token="t",
                                 transport=self._one({}))

    def test_judge_found_and_missed(self) -> None:
        fetch = self._one({"results": [{"id": 7, "name": "Learned Hand"}]})
        result = caselaw.lookup_judge(
            "Learned Hand", token="t", transport=fetch
        )
        self.assertTrue(result["found"])
        self.assertEqual(
            {"judge_id": "7", "name": "Learned Hand"}, result["judge"]
        )
        self.assertIn("/judges/?", fetch.calls[0][1])
        missed = caselaw.lookup_judge(
            "Nobody Null", token="t", transport=self._one({"results": []})
        )
        self.assertTrue(missed["available"])
        self.assertFalse(missed["found"])

    def test_disclosures_list_and_empty(self) -> None:
        fetch = self._one({"results": [
            {"id": 1, "year": 2024}, {"id": 2, "year": 2023},
        ]})
        result = caselaw.lookup_disclosures(
            "7", token="t", transport=fetch
        )
        self.assertTrue(result["found"])
        self.assertEqual(
            [{"record_id": "1", "year": "2024"},
             {"record_id": "2", "year": "2023"}],
            result["disclosures"],
        )
        self.assertIn("/financial-disclosures/?", fetch.calls[0][1])
        empty = caselaw.lookup_disclosures(
            "7", token="t", transport=self._one({"results": []})
        )
        self.assertTrue(empty["available"])
        self.assertFalse(empty["found"])
        self.assertEqual([], empty["disclosures"])
        malformed = caselaw.lookup_disclosures(
            "7", token="t", transport=self._one({"count": 0})
        )
        self.assertFalse(malformed["available"])
        self.assertIn("no results list", malformed["reason"])

    def test_auth_failure_shared(self) -> None:
        def fetch(method, url, headers):
            return 401, {}

        for call in (
            lambda: caselaw.lookup_docket("1", "c", token="t", transport=fetch),
            lambda: caselaw.lookup_judge("n", token="t", transport=fetch),
            lambda: caselaw.lookup_disclosures("1", token="t", transport=fetch),
        ):
            with self.subTest():
                result = call()
                self.assertFalse(result["available"])
                self.assertIn("auth", result["reason"])


def _opinion_transport(cluster_payload, opinions):
    """Cluster payload once, then opinions in listed order."""
    calls = []
    queue = list(opinions)

    def fetch(method, url, headers):
        calls.append((method, url, headers))
        if "/opinions/" in url:
            status, payload = queue.pop(0)
            if isinstance(payload, Exception):
                raise payload
            return status, dict(payload)
        return 200, dict(cluster_payload)

    fetch.calls = calls
    return fetch


_OPINION = {
    "id": 999,
    "type": "010combined",
    "plain_text": "PER CURIAM. The petitioner George W. Bush sought review.",
}


class OpinionTextTest(unittest.TestCase):
    def test_fetch_returns_text_and_metadata(self) -> None:
        fetch = _opinion_transport(
            {"sub_opinions": [999]}, [(200, dict(_OPINION))],
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertTrue(result["available"])
        self.assertTrue(result["found"])
        opinion = result["opinion"]
        self.assertEqual("999", opinion["opinion_id"])
        self.assertEqual("010combined", opinion["type"])
        self.assertIn("PER CURIAM", opinion["text"])
        self.assertFalse(opinion["truncated"])
        self.assertEqual(len(opinion["text"]), opinion["char_count"])
        self.assertIn("/clusters/111/", fetch.calls[0][1])
        self.assertIn("/opinions/999/", fetch.calls[1][1])

    def test_html_with_citations_beats_plain_text(self) -> None:
        opinion = dict(
            _OPINION,
            html_with_citations="<p>Held: <b>affirmed</b>.</p>",
            plain_text="stale fallback words",
        )
        fetch = _opinion_transport(
            {"sub_opinions": [999]}, [(200, opinion)],
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertEqual("Held: affirmed.", result["opinion"]["text"])
        self.assertEqual(
            "html_with_citations", result["opinion"]["text_source"]
        )

    def test_plain_text_last_resort_names_itself(self) -> None:
        fetch = _opinion_transport(
            {"sub_opinions": [999]}, [(200, dict(_OPINION))],
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertEqual("plain_text", result["opinion"]["text_source"])

    def test_combined_preferred_over_dissent(self) -> None:
        dissent = {
            "id": 1, "type": "040dissent", "plain_text": "I dissent.",
        }
        fetch = _opinion_transport(
            {"sub_opinions": [
                "https://www.courtlistener.com/api/rest/v3/opinions/1/",
                999,
            ]},
            [(200, dissent), (200, dict(_OPINION))],
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertTrue(result["found"])
        self.assertEqual("999", result["opinion"]["opinion_id"])
        self.assertEqual(3, len(fetch.calls))

    def test_dissent_only_falls_back_flagged(self) -> None:
        dissent = {
            "id": 1, "type": "040dissent", "plain_text": "I dissent.",
        }
        fetch = _opinion_transport(
            {"sub_opinions": [1]}, [(200, dissent)]
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertTrue(result["found"])
        self.assertEqual("1", result["opinion"]["opinion_id"])
        self.assertEqual("040dissent", result["opinion"]["type"])

    def test_html_fallback_stripped(self) -> None:
        fetch = _opinion_transport(
            {"sub_opinions": [5]},
            [(200, {
                "id": 5, "type": "010combined",
                "plain_text": "",
                "html": "<p>Filing &amp; <b>review</b>.</p>",
            })],
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertEqual("Filing & review.", result["opinion"]["text"])

    def test_long_text_truncates_with_count(self) -> None:
        text = ("word " * (caselaw.MAX_OPINION_CHARS // 5 + 10)).strip()
        fetch = _opinion_transport(
            {"sub_opinions": [9]},
            [(200, {
                "id": 9, "type": "010combined", "plain_text": text,
            })],
        )
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        opinion = result["opinion"]
        self.assertTrue(opinion["truncated"])
        self.assertEqual(len(text), opinion["char_count"])
        self.assertEqual(caselaw.MAX_OPINION_CHARS, len(opinion["text"]))

    def test_no_sub_opinions_found_false(self) -> None:
        fetch = _opinion_transport({"sub_opinions": []}, [])
        result = caselaw.fetch_opinion_text(
            "111", token="t", transport=fetch
        )
        self.assertTrue(result["available"])
        self.assertFalse(result["found"])
        self.assertIn("no opinions", result["reason"])

    def test_auth_failure_unavailable(self) -> None:
        def fetch(method, url, headers):
            return 403, {}

        result = caselaw.fetch_opinion_text(
            "111", token="bad", transport=fetch
        )
        self.assertFalse(result["available"])
        self.assertIn("auth", result["reason"])

    def test_missing_token_no_network(self) -> None:
        import os

        if os.environ.get("COURTLISTENER_TOKEN") or os.environ.get(
            "COURTLISTENER_TOKEN_FILE"
        ):
            self.skipTest("operator token present")
        calls = []

        def fetch(method, url, headers):
            calls.append(url)
            raise AssertionError("no network without a token")

        result = caselaw.fetch_opinion_text("111", transport=fetch)
        self.assertFalse(result["available"])
        self.assertIn("COURTLISTENER_TOKEN", result["reason"])
        self.assertEqual([], calls)

    def test_bad_args_rejected(self) -> None:
        with self.assertRaises(caselaw.CaselawError):
            caselaw.fetch_opinion_text("  ", token="t")
        with self.assertRaises(caselaw.CaselawError):
            caselaw.fetch_opinion_text("111", token="t", transport="nope")


class ExcerptTest(unittest.TestCase):
    TEXT = (
        "PER CURIAM. The petitioner sought review of the recount. "
        "The equal protection clause demands uniform standards for "
        "ballot review across counties. The judgment is reversed."
    )

    def test_terms_cluster_and_rank_first(self) -> None:
        excerpts = caselaw.find_excerpts(
            self.TEXT, "equal protection ballot standards"
        )
        self.assertTrue(excerpts)
        top = excerpts[0]
        self.assertIn("equal protection", top["text"])
        self.assertEqual(
            ["ballot", "equal", "protection", "standards"], top["terms"]
        )

    def test_limit_respected(self) -> None:
        text = "Alpha one. " + "filler. " * 200 + "Alpha two."
        excerpts = caselaw.find_excerpts(text, "alpha", limit=1)
        self.assertEqual(1, len(excerpts))

    def test_empty_query_and_no_match_silent(self) -> None:
        self.assertEqual([], caselaw.find_excerpts(self.TEXT, "is the"))
        self.assertEqual([], caselaw.find_excerpts(self.TEXT, "bankruptcy"))
        self.assertEqual([], caselaw.find_excerpts("", "equal protection"))


if __name__ == "__main__":
    unittest.main()
