"""Cross-matter isolation probe (Item 6, G12).

Two matters share one root (one inbox, one transcript/analysis
cache — the deployment shape) and run interleaved tool calls.
Each matter uses its own documents and marker-tagged claims.
The probe asserts both directions:

- prompt payloads: a matter's model calls carry its own document
  text and markers, never the other matter's;
- records: a matter's run directory carries its own markers,
  never the other's; the shared cache holds transcripts only;
- function: each matter's analysis quotes come from its own docs
  (the content-addressed cache keys return the right text).

Isolation here is structural (per-run records, per-call prompts,
content-keyed caches), and this probe turns it from assumed to
demonstrated. Out of scope: same-process env sharing under
concurrent runs, and provider-side file handling.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from verification import verify_claims


MATTERS = {
    "A": {
        "marker": "ALPHAMARKER",
        "doc": "DOC-REPORT",
        "quote": "Open Severity 1 defects: 21 at period end.",
        "item": "alpha",
    },
    "B": {
        "marker": "BETAMARKER",
        "doc": "DOC-MINUTES",
        "quote": "Mr. Lindgren confirmed the system is ready for go-live.",
        "item": "beta",
    },
}


class Matter:
    def __init__(self, root: Path, key: str) -> None:
        spec = MATTERS[key]
        self.key = key
        self.marker = spec["marker"]
        self.doc = spec["doc"]
        self.quote = spec["quote"]
        self.item = spec["item"]
        self.fake = support.scripted(root, [])
        self.run_id = new_run(root, instructions="i", objective="o")
        self.case = CaseRecord(root, self.run_id)
        self.case.set_checklist([support.checklist_item(self.item)])
        self.ctx = tools.Context(
            self.fake.client, root, self.run_id, self.case, True
        )
        self.memo = {
            "conclusion": f"{self.marker} conclusion.",
            "findings": [
                {
                    "item": self.item,
                    "status": "asserted",
                    "summary": f"{self.marker} summary.",
                    "citations": [
                        {
                            "document_id": self.doc,
                            "page": 1,
                            "quote": self.quote,
                        }
                    ],
                }
            ],
            "gaps": "none",
        }

    def analyze(self) -> dict:
        return analyze(self.fake.client, self.ctx.root, [self.doc], "terms")

    def record(self) -> dict:
        return self.case.record_fact(
            self.item, self.doc, 1, self.quote,
            f"{self.marker} claim about the passage.",
        )

    def judge(self) -> dict:
        return verify_claims.run_verification(self.ctx, self.memo)

    def submit(self) -> dict:
        from agent_kit.tools import submit_memo

        return submit_memo.run(self.ctx, dict(self.memo))

    def prompt_text(self) -> str:
        return json.dumps(self.fake.calls, default=str)

    def record_text(self) -> str:
        # The matter's own persisted state: its run directory plus its
        # case record (per-run files; the directories are shared).
        run_dir = self.ctx.root / "workspace/runs" / self.run_id
        record = (
            self.ctx.root / "workspace/case_records" / f"{self.run_id}.json"
        )
        paths = [p for p in sorted(run_dir.rglob("*")) if p.is_file()]
        if record.is_file():
            paths.append(record)
        return "\n".join(path.read_text() for path in paths)


class IsolationProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def _other(self, matter: Matter) -> Matter:
        key = "B" if matter.key == "A" else "A"
        return self.matters[key]

    def _assert_isolated(self) -> None:
        for matter in self.matters.values():
            other = self._other(matter)
            prompts = matter.prompt_text()
            self.assertIn(
                matter.marker, prompts,
                f"matter {matter.key}: own marker missing from its "
                "prompts — the cross-assertion below would be vacuous",
            )
            self.assertNotIn(
                other.marker, prompts,
                f"matter {other.key} leaked into matter {matter.key} prompts",
            )
            self.assertNotIn(
                other.quote, prompts,
                f"matter {other.key} document text leaked into matter "
                f"{matter.key} prompts",
            )
            records = matter.record_text()
            self.assertIn(matter.marker, records)
            self.assertNotIn(
                other.marker, records,
                f"matter {other.key} leaked into matter {matter.key} records",
            )

    def test_sequential_matters_isolated(self) -> None:
        self.matters = {key: Matter(self.root, key) for key in ("A", "B")}
        for matter in self.matters.values():
            matter.analyze()
            matter.record()
            matter.judge()
            matter.submit()
        self._assert_isolated()

    def test_interleaved_matters_isolated(self) -> None:
        self.matters = {key: Matter(self.root, key) for key in ("A", "B")}
        first, second = self.matters["A"], self.matters["B"]
        first.analyze()
        second.analyze()
        first.record()
        second.record()
        first.judge()
        second.judge()
        first.submit()
        second.submit()
        self._assert_isolated()

    def test_analysis_quotes_own_documents(self) -> None:
        self.matters = {key: Matter(self.root, key) for key in ("A", "B")}
        for matter in self.matters.values():
            result = matter.analyze()
            quotes = [
                item.get("quote", "")
                for doc in result.get("documents", [])
                for item in doc.get("evidence", [])
            ]
            self.assertTrue(quotes, f"matter {matter.key} analyzed nothing")
            other = self._other(matter)
            for quote in quotes:
                self.assertNotIn(other.marker, quote)
            pages = support.INITIAL[matter.doc]
            self.assertTrue(
                any(quote in "\n".join(pages) for quote in quotes),
                f"matter {matter.key} quotes match none of its own pages",
            )

    def test_shared_cache_holds_no_matter_markers(self) -> None:
        self.matters = {key: Matter(self.root, key) for key in ("A", "B")}
        for matter in self.matters.values():
            matter.analyze()
            matter.record()
        cache = self.root / "workspace/cache"
        cached = "\n".join(
            path.read_text()
            for path in sorted(cache.rglob("*"))
            if path.is_file()
        )
        for matter in self.matters.values():
            self.assertNotIn(matter.marker, cached)


if __name__ == "__main__":
    unittest.main()
