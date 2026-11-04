"""Seeded invariant fuzzing for the AI-governance slice.

Mirrors CollabInvariantFuzzTest: seeded random mutation
sequences across the governance/intake lifecycle, then
structural invariants plus two-phase replay determinism.
Governance ops are pure dict mutations, so replay reruns
recorded ops on a fresh demo matter and compares
normalized state. Fully deterministic; no model calls.
"""

from __future__ import annotations

import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from agent_kit import artifacts
from agent_kit.case_record import CaseRecord

_SEEDS = (11, 12, 13)
_OPS = 200

_NEXT = {
    "proposed": ("assessed",),
    "assessed": ("approved", "rejected", "proposed"),
    "approved": ("proposed",),
    "rejected": ("proposed",),
}
_RISKS = ("unassessed", "low", "medium", "high")
_IN_NEXT = {
    "new": ("triaged", "routed", "declined"),
    "triaged": ("routed", "declined"),
    "routed": ("closed",),
    "declined": ("new",),
    "closed": ("routed",),
}


class _World:
    """One demo matter plus fuzz-local bookkeeping."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.run_id = artifacts.build_demo(root)
        self.refresh()

    def refresh(self) -> None:
        self.case = CaseRecord(self.root, self.run_id)
        self.inv = self.case.artifact_get("A11")
        self.data = self.case.data
        self.source = self.case.artifact_get(
            self.inv["source_instance"]
        )
        self.in_pool = [
            r.get("in_id") for r in self.source.get("intakes") or []
        ]

    def uses(self) -> list:
        return self.inv.get("uses") or []

    def uc_ids(self) -> list:
        return [u.get("uc_id") for u in self.uses()]


def _attempt(func, *args, **kwargs) -> bool:
    try:
        func(*args, **kwargs)
    except Exception:
        return False
    return True


_OP_NAMES = (
    "add_valid", "add_bad", "set_field", "set_bad", "decide",
    "decide_bad", "triage", "route", "decide_in",
    "save_inv", "save_src", "reload",
)


def _do(world: _World, rng: random.Random, record: list | None,
         forced: tuple | None = None) -> None:
    name, arg = forced if forced else (
        rng.choice(_OP_NAMES),
        rng.randrange(10_000),
    )
    if record is not None:
        record.append((name, arg))
    pick = random.Random(arg)
    inv, data, src = world.inv, world.data, world.source
    have_uc = world.uc_ids()

    def bad_id() -> str:
        return f"UC{9000 + pick.randrange(900)}"

    if name == "add_valid":
        link = pick.choice(world.in_pool + [""])
        _attempt(
            artifacts.add_uc, inv, data,
            f"use {arg}", f"purpose {arg}", f"records {arg}",
            system="desk", owner="r", in_id=link,
        )
    elif name == "add_bad":
        mode = pick.randrange(4)
        kw: dict = {
            "title": f"use {arg}", "description": f"p {arg}",
            "data_types": f"d {arg}",
        }
        if mode == 0:
            kw["title"] = " "
        elif mode == 1:
            kw["description"] = ""
        elif mode == 2:
            kw["data_types"] = "  "
        else:
            kw["in_id"] = "IN99"
        _attempt(artifacts.add_uc, inv, data, **kw)
    elif name in ("set_field", "set_bad"):
        if not have_uc:
            return
        uc = bad_id() if name == "set_bad" and pick.random() < 0.3 else pick.choice(have_uc)
        field = pick.choice(
            ["title", "description", "data_types", "system",
             "risk", "owner", "conditions", "in_id"]
        )
        if name == "set_bad" and pick.random() < 0.5:
            value = pick.choice([" ", "", "bogus-risk"])
            if field not in ("title", "description", "data_types", "risk"):
                field = pick.choice(["title", "risk"])
        elif field == "risk":
            value = pick.choice(_RISKS)
        elif field == "in_id":
            value = pick.choice(world.in_pool + [""])
        else:
            value = f"{field} {arg}"
        _attempt(artifacts.set_uc, inv, data, uc, **{field: value})
    elif name in ("decide", "decide_bad"):
        if not have_uc:
            return
        uc = bad_id() if name == "decide_bad" and pick.random() < 0.3 else pick.choice(have_uc)
        target = pick.choice(
            ["proposed", "assessed", "approved", "rejected"]
        )
        reason = "" if name == "decide_bad" and pick.random() < 0.4 else f"r {arg}"
        _attempt(artifacts.decide_uc, inv, uc, target, reason)
    elif name == "triage":
        if not world.in_pool:
            return
        _attempt(
            artifacts.triage_in, src,
            pick.choice(world.in_pool), f"owner {arg}",
        )
    elif name == "route":
        if not world.in_pool:
            return
        _attempt(
            artifacts.route_in, src,
            pick.choice(world.in_pool), f"desk {arg}", f"why {arg}",
        )
    elif name == "decide_in":
        if not world.in_pool:
            return
        target = pick.choice(
            ["new", "declined", "closed", "routed"]
        )
        _attempt(
            artifacts.decide_in, src,
            pick.choice(world.in_pool), target, f"why {arg}",
        )
    elif name == "save_inv":
        world.case.artifact_save(world.inv, "artifact_governance", "")
        world.refresh()
    elif name == "save_src":
        world.case.artifact_save(world.source, "artifact_intake", "")
        world.refresh()
    elif name == "reload":
        world.case.artifact_save(world.inv, "artifact_governance", "")
        world.case.artifact_save(world.source, "artifact_intake", "")
        world.refresh()


def _normalize(obj):
    if isinstance(obj, dict):
        return {
            k: _normalize(v)
            for k, v in obj.items()
            if k not in ("at", "created_at", "updated_at")
        }
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


class GovernanceFuzzTest(unittest.TestCase):
    def _check_walk(self, use: dict, seed: int, i: int) -> None:
        status = "proposed"
        self.assertEqual("logged", use["history"][0]["change"],
                         f"seed {seed} op {i}")
        for entry in use["history"][1:]:
            change = entry["change"]
            if change == "revised":
                continue
            self.assertIn(
                change, _NEXT[status],
                f"seed {seed} op {i}: {status} -> {change}",
            )
            status = change
        self.assertEqual(status, use.get("status"))

    def _invariants(self, world: _World, seed: int, i: int) -> None:
        where = f"seed {seed} op {i}"
        uses = world.uses()
        ids = world.uc_ids()
        self.assertEqual(len(ids), len(set(ids)), where)
        expect = {f"UC{n}" for n in range(1, len(ids) + 1)}
        self.assertEqual(set(ids), expect, where)
        for item in uses:
            self.assertIn(item.get("status"), _NEXT, where)
            self.assertIn(item.get("risk"), _RISKS, where)
            self._check_walk(item, seed, i)
            first = artifacts.uc_status(world.inv, world.data, item)
            again = artifacts.uc_status(world.inv, world.data, item)
            self.assertEqual(first, again, where)
            if first["state"] != "current":
                self.assertTrue(first["reasons"], where)
        summary = artifacts.gv_summary(world.inv, world.data)
        counts = summary["counts"]
        self.assertEqual(sum(counts.values()), len(uses), where)
        queue_ids = {q["uc_id"] for q in summary["queue"]}
        for q in summary["queue"]:
            self.assertIn(q["status"], ("proposed", "assessed"), where)
            found = next(u for u in uses if u.get("uc_id") == q["uc_id"])
            live = artifacts.uc_status(world.inv, world.data, found)
            self.assertEqual(q["link_state"], live["state"], where)
        self.assertEqual(
            queue_ids,
            {u.get("uc_id") for u in uses
             if u.get("status") in ("proposed", "assessed")},
            where,
        )
        self.assertEqual(
            set(summary["unassessed"]),
            {u.get("uc_id") for u in uses
             if u.get("risk") == "unassessed"},
            where,
        )
        self.assertEqual(
            {s["uc_id"] for s in summary["stale"]},
            {u.get("uc_id") for u in uses
             if artifacts.uc_status(
                 world.inv, world.data, u)["state"] != "current"},
            where,
        )
        versions = world.inv.get("versions") or []
        self.assertEqual(
            [v.get("v") for v in versions],
            list(range(1, len(versions) + 1)), where,
        )
        if versions:
            self.assertEqual(
                _normalize(versions[-1].get("uses")),
                _normalize(uses), where,
            )
        json.dumps(world.inv)
        json.dumps(summary)

    def test_seeded_sequences(self) -> None:
        for seed in _SEEDS:
            with tempfile.TemporaryDirectory() as tmp:
                world = _World(Path(tmp))
                rng = random.Random(seed)
                record: list = []
                # Fresh-demo baseline must also satisfy invariants.
                self._invariants(world, seed, -1)
                for i in range(_OPS):
                    _do(world, rng, record)
                    self._invariants(world, seed, i)
                # Replay: recorded ops on a second fresh demo.
                with tempfile.TemporaryDirectory() as tmp2:
                    twin = _World(Path(tmp2))
                    for name, arg in record:
                        _do(twin, rng, None, forced=(name, arg))
                    self.assertEqual(
                        _normalize(twin.inv), _normalize(world.inv),
                        f"seed {seed}: replay diverged",
                    )
                    self.assertEqual(
                        _normalize(twin.source),
                        _normalize(world.source),
                        f"seed {seed}: source replay diverged",
                    )
                # Proof the generator exercised the lifecycle.
                decided = [
                    u for u in world.uses()
                    if any(e["change"] != "logged"
                           and e["change"] != "revised"
                           for e in u["history"])
                ]
                self.assertGreaterEqual(
                    len(decided), 3,
                    f"seed {seed}: lifecycle never exercised",
                )
