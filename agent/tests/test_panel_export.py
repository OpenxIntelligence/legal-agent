"""Panel export contract: ledger + coverage views for the kernel."""

from __future__ import annotations

import unittest

from agent_kit import panel_export
from agent_kit.case_record import ADVERSARIAL_LENSES


def _review(id="AR1", lens="", **kw):
    entry = {"id": id, "lens": lens, "at": "t"}
    entry.update(kw)
    return entry


def _finding(id="OBJ1", status="open", **kw):
    entry = {
        "id": id,
        "status": status,
        "objection": "o",
        "counter_evidence": "c",
        "reporters": ["AR1"],
        "at": "t",
    }
    entry.update(kw)
    return entry


class PanelExportTest(unittest.TestCase):
    def test_empty_record_exports_none(self) -> None:
        self.assertIsNone(panel_export.panel_export({}))
        self.assertIsNone(panel_export.panel_export({"adversarial": []}))
        self.assertIsNone(panel_export.panel_export("not a dict"))

    def test_reviews_only_cover_lenses_without_objections(self) -> None:
        view = panel_export.panel_export(
            {"adversarial": [_review("AR1", "law"), _review("AR2", "law")]}
        )
        self.assertEqual(
            (2, "AR2", ["law"], ["evidence", "procedure", "strategy"], False),
            (
                view["reviews"], view["latest"], view["lenses"],
                view["missing"], view["complete"],
            ),
        )
        self.assertEqual(
            {"open": 0, "disproven": 0, "total": 0},
            view["objections"]["counts"],
        )

    def test_full_panel_reports_complete(self) -> None:
        view = panel_export.panel_export(
            {
                "adversarial": [
                    _review(f"AR{i}", lens)
                    for i, lens in enumerate(
                        ("evidence", "law", "procedure", "strategy"), 1
                    )
                ]
            }
        )
        self.assertEqual([], view["missing"])
        self.assertTrue(view["complete"])

    def test_objections_split_open_and_disproven(self) -> None:
        view = panel_export.panel_export(
            {
                "adversarial": [_review()],
                "objections": [
                    _finding("OBJ1", "disproven"),
                    _finding("OBJ2", "open"),
                ],
            }
        )
        opened = view["objections"]["open"]
        dropped = view["objections"]["disproven"]
        self.assertEqual(["OBJ2"], [o["id"] for o in opened])
        self.assertEqual(["OBJ1"], [o["id"] for o in dropped])
        self.assertNotIn("counter_evidence", opened[0])
        self.assertEqual("c", dropped[0]["counter_evidence"])
        self.assertEqual(
            {"open": 1, "disproven": 1, "total": 2},
            view["objections"]["counts"],
        )

    def test_malformed_entries_skipped_never_raised(self) -> None:
        view = panel_export.panel_export(
            {
                "adversarial": ["x", None, 42],
                "objections": ["y", {"id": "OBJ1", "status": "open"}],
            }
        )
        self.assertEqual(0, view["reviews"])
        self.assertIsNone(view["latest"])
        self.assertEqual("OBJ1", view["objections"]["open"][0]["id"])
        self.assertEqual("", view["objections"]["open"][0]["objection"])

    def test_lens_seats_pinned_to_case_record(self) -> None:
        self.assertEqual(
            list(ADVERSARIAL_LENSES), list(panel_export.REQUIRED_LENSES)
        )

    def test_export_is_a_snapshot_without_internals(self) -> None:
        view = panel_export.panel_export(
            {
                "objections": [
                    _finding(key="k", history=[{"status": "open"}])
                ]
            }
        )
        self.assertNotIn("key", view["objections"]["open"][0])
        self.assertNotIn("history", view["objections"]["open"][0])
        self.assertEqual(
            {"reviews", "latest", "lenses", "required", "missing",
             "complete", "objections"},
            set(view),
        )


if __name__ == "__main__":
    unittest.main()
