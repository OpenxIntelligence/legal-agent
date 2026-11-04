"""Labeling packet + judge slice plumbing (calibration prep, offline).

The packet generator assembles stratified labeling items from
the benchmark suites; the slice runner judges them. Both run
offline here (scripted judge); the live judge run needs
$MODEL_API_KEY and an operator budget, and the human side
needs a person — neither is substitutable (see
docs/judge-calibration.md).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eval import judge_slice, label_packet
from tests import support


class LabelPacketTest(unittest.TestCase):
    def test_packet_stratified_stable_unique(self) -> None:
        packet = label_packet.build_packet(24)
        self.assertEqual(24, len(packet))
        ids = [item["id"] for item in packet]
        self.assertEqual(24, len(set(ids)))
        self.assertEqual(12, sum(1 for i in packet if i["sound"]))
        self.assertEqual(12, sum(1 for i in packet if not i["sound"]))
        suites = {item["source"].split(":")[0] for item in packet}
        self.assertTrue({"reasoning", "quality", "identification"} <= suites)
        for item in packet:
            self.assertTrue(item["unit"]["id"])
            self.assertTrue(item["memo"].get("findings"))
            # Facts may be legitimately empty (a no-evidence memo
            # the judge must fail); the key must exist.
            self.assertIsInstance(item["facts"], list)
        again = label_packet.build_packet(24)
        self.assertEqual(ids, [item["id"] for item in again])

    def test_packet_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            label_packet.build_packet(0)


class JudgeSliceTest(unittest.TestCase):
    def test_scripted_slice_labels_every_item(self) -> None:
        packet = label_packet.build_packet(4)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "case").mkdir()
            client = support.scripted(root, []).client
            labels = judge_slice.judge_packet(packet, client)
        self.assertEqual(
            [item["id"] for item in packet],
            [entry["id"] for entry in labels],
        )
        for entry in labels:
            self.assertIn(entry["label"], ("pass", "fail", "unknown"))


if __name__ == "__main__":
    unittest.main()
