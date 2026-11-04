"""Retrieved passages carry machine-usable provenance (§5/RET-4).

Fixed (Slice 1): every passage from search_corpus carries offsets
into the cached page text, the inbox file fingerprint, and
jurisdiction/effective from caller-supplied per-document meta
(None when unknown — never fabricated). Restricted docs stay
excluded; offsets/fingerprints are ints/hex, never source text.
"""

from __future__ import annotations

import unittest

from agent_kit import document_text
from eval import retrieval2_cases as cases
from eval import retrieval2_run as runner


class PassageProvenanceTest(unittest.TestCase):
    def test_passages_carry_provenance(self) -> None:
        import shutil
        import tempfile
        from pathlib import Path

        parent = Path(tempfile.mkdtemp(prefix="r2prov-"))
        try:
            root, _ = runner.build_matter_root("harbor-bridge", parent)
            matter = cases.CORPORA["harbor-bridge"]
            result = document_text.search_corpus(
                root, "liquidated damages", flagged={"HB-PRIV"},
                meta=matter["meta"])
            (group,) = [d for d in result["documents"]
                        if d["document_id"] == "HB-CONTRACT"]
            passage = group["passages"][0]
            for key in ("offsets", "fingerprint", "jurisdiction",
                        "effective"):
                self.assertIn(
                    key, passage,
                    f"passage lacks {key}: {sorted(passage)}",
                )
            # Offsets resolve into the cached page text at the match.
            page = document_text.page_text(
                root, "HB-CONTRACT", passage["page"])
            start, end = passage["offsets"]
            self.assertIn("liquidated damages", page[start:end].lower())
            # Fingerprint is the inbox file's digest, not a label.
            doc = document_text.get_document(root, "HB-CONTRACT")
            self.assertEqual(doc["sha256"], passage["fingerprint"])
            # Caller-supplied meta flows through verbatim.
            self.assertEqual("Port Authority", passage["jurisdiction"])
            self.assertEqual("2025-09-01", passage["effective"])
            # Unknown meta stays None; restricted docs stay excluded.
            bare = document_text.search_corpus(
                root, "liquidated damages", flagged={"HB-PRIV"})
            bare_passage = bare["documents"][0]["passages"][0]
            self.assertIsNone(bare_passage["jurisdiction"])
            self.assertIsNone(bare_passage["effective"])
            self.assertEqual(["HB-PRIV"], bare["restricted"])
            blob = repr([
                (p.get("offsets"), p.get("fingerprint"))
                for d in result["documents"] for p in d["passages"]
            ])
            self.assertNotIn("PRIVILEGED", blob)
            self.assertNotIn("45,000", blob)
        finally:
            shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
