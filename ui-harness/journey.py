"""Synthetic UI journey: real HTTP + real services + isolated storage.

Runs the gallery -> audit -> source -> revise -> recheck -> review ->
export -> reopen journey against the actual ui.py request handler over
in-memory socket pairs (no ports bound, so it runs even where the
sandbox forbids listeners), with a fictional demo matter in a temp
root. Mutations go through the same ./helper artifact path the
server uses. Restart persistence is proven by a FRESH SUBPROCESS
reading the same root.

Usage (from the repo root):
    agent/.venv/bin/python ui-harness/journey.py

Exit 0 + "journey green" on success; first failure aborts non-zero.
Covers handlers, services, and persistence. DOM/keyboard/focus
behavior is covered by ui-harness/browser_run.sh (real
headless-shell over CDP); screen-reader behavior remains
unverified (see ui-harness/README.md).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
RUN = "run-demo-audit"

failures = 0


def ok(name: str, cond: bool, detail: str = "") -> bool:
    global failures
    if cond:
        print(f"ok - {name}")
    else:
        failures += 1
        print(f"FAIL - {name}" + (f": {detail}"[:300] if detail else ""))
    return cond


def load_ui() -> ModuleType:
    spec = importlib.util.spec_from_file_location("harness_ui", ROOT / "ui" / "ui.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["harness_ui"] = module
    spec.loader.exec_module(module)
    return module


class FakeServer:
    server_name = "harness"
    server_version = "harness"


def http(handler_cls, method: str, path: str, body: dict | None = None):
    """One real HTTP request through the real handler. Returns (status, body)."""
    ours, theirs = socket.socketpair()
    server = FakeServer()
    thread = threading.Thread(
        target=handler_cls, args=(theirs, ("127.0.0.1", 0), server), daemon=True
    )
    thread.start()
    try:
        payload = json.dumps(body).encode() if body is not None else b""
        head = (
            f"{method} {path} HTTP/1.0\r\n"
            "Host: harness\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        ours.sendall(head + payload)
        ours.shutdown(socket.SHUT_WR)
        # Framed read (headers + Content-Length body): never wait for
        # EOF, since no serve loop owns this socket to close it.
        ours.settimeout(30)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = ours.recv(65536)
            if not chunk:
                raise RuntimeError("handler closed before responding")
            raw += chunk
        header, _, rest = raw.partition(b"\r\n\r\n")
        length = 0
        for line in header.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body_bytes = rest
        while len(body_bytes) < length:
            chunk = ours.recv(65536)
            if not chunk:
                break
            body_bytes += chunk
        raw = header + b"\r\n\r\n" + body_bytes
    finally:
        ours.close()
        thread.join(timeout=10)
        theirs.close()
    header, _, content = raw.partition(b"\r\n\r\n")
    status = int(header.split(b" ", 2)[1])
    try:
        parsed = json.loads(content) if content else None
    except ValueError:
        parsed = None
    return status, parsed, content


def _inst(show: dict | None) -> dict:
    """Show payloads nest the instance; tolerate either shape."""
    show = show or {}
    inner = show.get("instance")
    return inner if isinstance(inner, dict) else show


def rows_of(show: dict | None) -> list:
    return _inst(show).get("rows") or []


def versions_of(show: dict | None) -> list:
    return _inst(show).get("versions") or []


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="ui-harness-")
    root = Path(tmp.name)
    os.environ["LEGAL_AGENT_ROOT"] = str(root)
    # Fake model transport for every ./helper artifact subprocess the
    # handler spawns: semantic checks record unavailable, never live.
    os.environ["LEGAL_AGENT_FAKE_MODEL"] = "1"
    sys.path.insert(0, str(AGENT))
    from agent_kit.artifacts.demo import build_demo

    build_demo(root)
    ok("demo matter builds", (root / "workspace/runs" / RUN).is_dir())
    ui = load_ui()
    handler = ui.make_handler(ui.Console(ROOT))

    def get(path):
        return http(handler, "GET", path)

    def post(path, body):
        return http(handler, "POST", path, body)

    # Quarantine surfaces on the documents feed (no covered text).
    (root / "workspace/inbox").mkdir(parents=True, exist_ok=True)
    (root / "workspace/inbox/DEMO-SEALED.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "workspace/quarantine.json").write_text(json.dumps({
        "DEMO-SEALED": {
            "reason": "harness seal", "findings": 2,
            "at": "2026-10-30T00:00:00+00:00",
        },
        "DEMO-OLD": {
            "reason": "harness seal", "findings": 1,
            "at": "2026-10-29T00:00:00+00:00",
            "released": {"at": "2026-10-29T01:00:00+00:00",
                        "note": "harness release"},
        },
    }))
    status, docs, _ = get("/api/documents")
    seen = {d.get("id"): d for d in (docs or {}).get("visible", [])}
    ok("sealed document flagged on the feed",
       status == 200 and seen.get("DEMO-SEALED", {}).get("quarantined") is True
       and seen["DEMO-SEALED"].get("quarantine_reason") == "harness seal"
       and (docs or {}).get("quarantined") == ["DEMO-SEALED"],
       f"status={status} body={json.dumps(docs)[:200]}")

    # Human adds a document: validated, transcribed, listed.
    status, added, _ = post("/api/documents", {
        "filename": "HARNESS-NOTE.txt",
        "text": "Harness note. The attachment arrived on May 7.",
    })
    ok("text upload transcribes",
       status == 200 and (added or {}).get("state") == "transcribed"
       and (added or {}).get("document_id") == "HARNESS-NOTE",
       f"status={status} body={json.dumps(added)[:200]}")
    status, docs2, _ = get("/api/documents")
    seen2 = {d.get("id") for d in (docs2 or {}).get("visible", [])}
    ok("upload appears on the feed", "HARNESS-NOTE" in seen2)
    status, _, _ = post("/api/documents", {
        "filename": "../ESCAPE.txt", "text": "x"})
    ok("traversal upload refused", status == 409, f"status={status}")
    status, _, _ = post("/api/documents", {
        "filename": "HARNESS-NOTE.txt", "text": "again"})
    ok("duplicate upload refused", status == 409, f"status={status}")
    status, _, _ = post("/api/documents", {
        "filename": "FAKE.pdf",
        "content_b64": base64.b64encode(b"not a pdf").decode()})
    ok("fake pdf refused", status == 409, f"status={status}")

    # Gallery.
    status, gal, _ = get("/api/gallery")
    cards = (gal or {}).get("cards") or (gal or {}).get("templates") or []
    if not ok("gallery lists cards", status == 200 and len(cards) > 0):
        return 1
    ok(
        "gallery marks a synthetic demo",
        any("demo" in json.dumps(c).lower() for c in cards),
    )

    # Open the audit instance.
    status, listed, _ = get(f"/api/runs/{RUN}/artifacts")
    instances = (
        listed if isinstance(listed, list)
        else (listed or {}).get("instances") or (listed or {}).get("artifacts", [])
    )
    audit = next(
        (i for i in instances
         if "audit" in str(i.get("template") or i.get("template_id") or "").lower()),
        None,
    )
    if not ok("audit instance listed", audit is not None):
        return 1
    aid = audit.get("id") or audit.get("instance_id")
    base = f"/api/runs/{RUN}/artifacts/{aid}"
    status, show0, _ = get(base)
    rows0 = rows_of(show0)
    v0 = len(versions_of(show0))
    row = next((r for r in rows0 if r.get("kind") == "finding"), rows0[0] if rows0 else None)
    if not ok("audit shows rows", bool(row and row.get("row_id"))):
        return 1
    cite = (row.get("citations") or [{}])[0]
    if not ok("row carries a citation", bool(cite.get("document_id"))):
        return 1

    # Source inspection is real.
    status, src, _ = get(
        f"{base}/source?doc={cite['document_id']}&page={cite.get('page', 1)}"
    )
    ok(
        "source text served with provenance",
        status == 200
        and isinstance((src or {}).get("text"), str) and len(src["text"]) > 0
        and isinstance((src or {}).get("sha12"), str) and len(src["sha12"]) == 12
        and isinstance((src or {}).get("fidelity_note"), str),
        f"status={status}",
    )

    # Revise -> new version stored.
    new_text = "Harness revised assertion (synthetic)."
    status, _, _ = post(f"{base}/revise", {"row": row["row_id"], "assertion": new_text})
    if not ok("revise accepted", status == 200, f"status={status}"):
        return 1
    status, show1, _ = get(base)
    rows1 = rows_of(show1)
    v1 = len(versions_of(show1))
    row1 = next((r for r in rows1 if r.get("row_id") == row["row_id"]), {})
    ok("revision stored as new version", v1 == v0 + 1 and row1.get("assertion") == new_text,
       f"v{v0}->v{v1}")

    # Recheck runs (fake transport: semantic stays unavailable).
    status, chk, _ = post(f"{base}/check", {"rows": row["row_id"]})
    ok("recheck runs", status == 200 and isinstance((chk or {}).get("changed"), list),
       f"status={status}")
    _, show_chk, _ = get(base)
    row_chk = next((r for r in rows_of(show_chk)
                    if r.get("row_id") == row["row_id"]), {})
    sem = ((row_chk.get("checks") or {}).get("semantic") or {}).get("outcome")
    ok("recheck leaves semantic honestly unavailable",
       sem in ("unavailable", "not_run"), f"semantic={sem}")

    # Structured wrong-marking: annotation, persisted, exported.
    status, marked, _ = post(
        f"{base}/mark",
        {"row": row["row_id"], "index": 0, "category": "wrong",
         "note": "harness: quote not on page"})
    ok("mark accepted", status == 200 and (marked or {}).get("row") == row["row_id"],
       f"status={status}")
    _, show_m, _ = get(base)
    row_m = next((r for r in rows_of(show_m) if r.get("row_id") == row["row_id"]), {})
    marks = row_m.get("marks") or []
    ok("mark persisted on row",
       len(marks) == 1 and marks[0].get("category") == "wrong"
       and "quote not on page" in str(marks[0].get("note")),
       f"marks={marks!r}"[:160])
    ok("mark adds no revision", len(versions_of(show_m)) == len(versions_of(show_chk)),
       "mark must be annotation-only")
    status, _, _ = post(
        f"{base}/mark",
        {"row": row["row_id"], "index": 0, "category": "bogus"})
    ok("bad category refused", status in (400, 404, 409), f"status={status}")
    status, _, _ = post(
        f"{base}/mark",
        {"row": row["row_id"], "index": 99, "category": "wrong"})
    ok("bad citation index refused", status in (400, 404, 409), f"status={status}")
    st = (show_m or {}).get("status") or {}
    ok("show carries contradiction data",
       "adversarial" in st and "panel" in st, f"keys={sorted(st)}")
    ok("show carries quality panels",
       "quality" in st and "eval_version" in st
       and (st.get("quality") or {}).get("rows") == 9,
       f"keys={sorted(st)}")

    # Blind review: stripped assignment, one judgment, token burns.
    status, assign, _ = post(f"{base}/assign", {"row": row["row_id"]})
    blob = json.dumps(assign or {})
    forbidden = [k for k in ("checks", "review_labels", "adversarial",
                             "panel", "verdict", "semantic")
                 if f'"{k}"' in blob]
    ok("assignment is blinded",
       status == 200 and not forbidden and (assign or {}).get("token"),
       f"status={status} leaked={forbidden}")
    token = (assign or {}).get("token") or ""
    status, labeled, _ = post(f"{base}/label", {
        "token": token, "verdict": "insufficient",
        "rationale": "harness: timing not shown",
        "actor": "sim:journey", "origin": "simulated",
        "missing_evidence": True})
    ok("blinded label accepted",
       status == 200 and (labeled or {}).get("verdict") == "insufficient",
       f"status={status}")
    status, _, _ = post(f"{base}/label", {
        "token": token, "verdict": "supported",
        "rationale": "again", "actor": "sim:journey",
        "origin": "simulated"})
    ok("assignment token is single-use",
       status in (400, 404, 409), f"status={status}")
    _, show_l, _ = get(base)
    states = (((show_l or {}).get("status") or {}).get("quality") or {}
              ).get("label_states") or {}
    ok("label state surfaces in show",
       states.get(row["row_id"]) == "submitted",
       f"states={states}")
    status, assign2, _ = post(f"{base}/assign", {"row": row["row_id"]})
    status, _, _ = post(f"{base}/label", {
        "token": (assign2 or {}).get("token") or "",
        "verdict": "supported",
        "rationale": "harness: second reviewer disagrees",
        "actor": "sim:journey2", "origin": "simulated"})
    ok("second label accepted", status == 200, f"status={status}")
    _, show_u, _ = get(base)
    states_u = (((show_u or {}).get("status") or {}).get("quality")
                or {}).get("label_states") or {}
    ok("conflict stays unresolved",
       states_u.get(row["row_id"]) == "unresolved",
       f"states={states_u}")
    status, adj, _ = post(f"{base}/adjudicate", {
        "row": row["row_id"], "decision": "corrected",
        "rationale": "harness: first reviewer was right",
        "actor": "sim:arbiter"})
    ok("adjudication recorded",
       status == 200 and (adj or {}).get("decision") == "corrected",
       f"status={status}")
    _, show_a, _ = get(base)
    states_a = (((show_a or {}).get("status") or {}).get("quality")
                or {}).get("label_states") or {}
    ok("row adjudicated after decision",
       states_a.get(row["row_id"]) == "adjudicated",
       f"states={states_a}")

    # Regression examples: list, honest promote refusal on the
    # adversarial demo (unpinned DOC-VANISHED), sealed refusal,
    # unknown replay refusal. Success path is CLI-tested on a
    # promotable review_cases fixture (test_promote_cli).
    status, examples, _ = get(f"{base}/examples")
    ok("examples list starts empty",
       status == 200 and (examples or {}).get("examples") == [],
       f"status={status} body={json.dumps(examples)[:160]}")
    _, show_c, _ = get(base)
    rows_c = ((show_c or {}).get("instance") or {}).get("rows") or []
    status, refused, _ = post(f"{base}/promote", {
        "example_id": "harness-timing", "role": "regression",
        "actor": "sim:curator", "failure": "harness: timing overreach",
        "behavior": "harness: preserve current outcomes",
        "classes": {r["row_id"]: "clean" for r in rows_c}})
    ok("adversarial demo refused with reason",
       status in (400, 404, 409)
       and "pinned source version" in json.dumps(refused),
       f"status={status} body={json.dumps(refused)[:200]}")
    status, _, _ = post(f"{base}/promote", {
        "example_id": "harness-sealed", "role": "sealed",
        "actor": "sim:curator", "failure": "x", "behavior": "y",
        "classes": {r["row_id"]: "clean" for r in rows_c}})
    ok("sealed promotion refused", status in (400, 404, 409),
       f"status={status}")
    status, _, _ = post(f"{base}/replay", {"example": "no-such-id"})
    ok("unknown example replay refused", status in (400, 404, 409),
       f"status={status}")
    status, _, _ = post(f"{base}/replay", {"example": "../escape"})
    ok("traversal example refused", status in (400, 404, 409),
       f"status={status}")

    # Hand notes: saved, listed, version-neutral.
    _, show_v, _ = get(base)
    v_before = len(versions_of(show_v))
    status, noted, _ = post(f"{base}/note", {
        "text": "harness: call the clerk about sealing",
        "author": "harness"})
    ok("note accepted",
       status == 200 and (noted or {}).get("note") == "N1",
       f"status={status}")
    _, show_n, _ = get(base)
    notes = ((show_n or {}).get("instance") or {}).get("notes") or []
    ok("note persisted",
       len(notes) == 1 and "sealing" in notes[0].get("text", ""),
       f"notes={notes!r}"[:160])
    ok("note adds no revision",
       len(versions_of(show_n)) == v_before,
       "note must be annotation-only")
    status, _, _ = post(f"{base}/note", {"text": "   "})
    ok("blank note refused", status in (400, 404, 409),
       f"status={status}")

    # Objections: raise, disprove, restore through the run route.
    status, raised, _ = post(f"/api/runs/{RUN}/objections", {
        "op": "raise", "text": "harness: totals look rounded",
        "reporter": "harness"})
    ok("objection raised",
       status == 200 and (raised or {}).get("status") == "open",
       f"status={status}")
    _, show_o, _ = get(base)
    counts = ((((show_o or {}).get("status") or {}).get("panel")
               or {}).get("objections") or {}).get("counts") or {}
    ok("panel counts the open objection",
       counts.get("open") == 1, f"counts={counts}")
    ref = (raised or {}).get("id") or "OBJ1"
    status, _, _ = post(f"/api/runs/{RUN}/objections", {
        "op": "disprove", "ref": ref,
        "counter": "harness: ledger shows exact cents"})
    ok("objection disproved", status == 200, f"status={status}")
    status, _, _ = post(f"/api/runs/{RUN}/objections", {
        "op": "restore", "ref": ref, "reason": "harness: recheck"})
    ok("objection restored", status == 200, f"status={status}")
    status, _, _ = post(f"/api/runs/{RUN}/objections", {
        "op": "raise", "text": "   "})
    ok("blank objection refused", status in (400, 404, 409),
       f"status={status}")

    # Explicit clear stores ""; then restore.
    status, _, _ = post(f"{base}/revise", {"row": row["row_id"], "assertion": ""})
    _, show_c, _ = get(base)
    row_c = next((r for r in rows_of(show_c) if r.get("row_id") == row["row_id"]), {})
    ok("explicit empty clears the stored value",
       status == 200 and row_c.get("assertion") == "", f"status={status}")
    restored = "Harness restored assertion (synthetic)."
    post(f"{base}/revise", {"row": row["row_id"], "assertion": restored})

    # Recheck everything so the approval below binds fresh checks.
    status, _, _ = post(f"{base}/check", {"rows": ""})
    ok("pre-review recheck runs", status == 200, f"status={status}")

    # Review exact revision; stale approval rejected.
    _, show2, _ = get(base)
    v_cur = len(versions_of(show2))
    snap_cur = (show2 or {}).get("snapshot") or {}
    snap_old = (show0 or {}).get("snapshot") or {}
    status, _, _ = post(
        f"{base}/review",
        {"decision": "approved", "notes": "harness review",
         "qualifications": ["synthetic harness approval"],
         "snapshot": snap_cur},
    )
    ok("review of exact revision recorded", status == 200, f"status={status}")
    status, stale, _ = post(
        f"{base}/review",
        {"decision": "approved", "notes": "stale attempt",
         "qualifications": [], "snapshot": snap_old or {"version": -1}},
    )
    ok(
        "stale approval rejected",
        status == 409 or "stale" in json.dumps(stale or {}).lower()
        or "version" in json.dumps(stale or {}).lower(),
        f"status={status} body={json.dumps(stale)[:120]}",
    )
    _, show_cur, _ = get(base)
    ok("fresh approval reads current",
       ((show_cur or {}).get("status") or {}).get("review_current")
       is True,
       f"status={json.dumps((show_cur or {}).get('status'))[:160]}")
    cited = root / "workspace/inbox/DEMO-STATUS.pdf"
    cited.write_bytes(cited.read_bytes() + b"\nappendix: nothing new.")
    _, show_st, _ = get(base)
    st = (show_st or {}).get("status") or {}
    ok("changed source stales the approval",
       st.get("review_current") is False
       and (st.get("counts") or {}).get("stale", 0) > 0,
       f"status={json.dumps(st)[:200]}")
    status, exp_st, _ = get(f"{base}/export")
    ok("export marks the approval stale",
       status == 200
       and (exp_st or {}).get("review", {}).get("current") is False
       and (exp_st or {}).get("review", {}).get("decision")
       == "approved",
       f"status={status}")

    # Unknown row refused; revoked source blocked; traversal blocked.
    status, _, _ = post(f"{base}/revise", {"row": "NOPE-999", "assertion": "x"})
    ok("unknown row refused", status in (404, 409), f"status={status}")
    status_m, _, _ = post(f"{base}/revise", {"assertion": "x"})
    status_n, _, _ = post(f"{base}/revise", {"row": None, "assertion": "x"})
    ok("missing/null row refused like unknown row",
       status_m in (404, 409) and status_n in (404, 409),
       f"missing={status_m} null={status_n}")
    _, show_pre, _ = get(base)
    v_pre = len(versions_of(show_pre))
    row_pre = next((r for r in rows_of(show_pre)
                    if r.get("row_id") == row["row_id"]), {})
    status, _, _ = post(f"{base}/revise",
                        {"row": row["row_id"], "assertion": None})
    _, show_post, _ = get(base)
    row_post = next((r for r in rows_of(show_post)
                     if r.get("row_id") == row["row_id"]), {})
    ok("null assertion is an accepted no-op",
       status == 200 and len(versions_of(show_post)) == v_pre
       and row_post.get("assertion") == row_pre.get("assertion"),
       f"status={status} v{v_pre}->{len(versions_of(show_post))}")
    status, rev, _ = get(f"{base}/source?doc=DEMO-DRAFT&page=1")
    ok("revoked source blocked",
       status == 409 and "revok" in json.dumps(rev or {}).lower(), f"status={status}")
    status, _, _ = get(f"{base}/source?doc=..")
    ok("path traversal blocked", status in (404, 409), f"status={status}")

    # Export carries ids, revisions, qualifications, demo marking.
    status, exp_j, _ = get(f"{base}/export?format=json&version={v_cur}")
    exp_text = json.dumps(exp_j or {})
    ok("json export versioned and identified",
       status == 200 and RUN in exp_text and aid in exp_text, f"status={status}")
    status, _, exp_m = get(f"{base}/export?format=md&version={v_cur}")
    md = exp_m.decode("utf8", "replace")
    ok("markdown export qualified and marked synthetic",
       status == 200 and "synthetic harness approval" in md
       and ("demo" in md.lower() or "synthetic" in md.lower())
       and "## Quality" in md and "## Review panel" in md,
       f"status={status}")

    # Board over the same matter (light regression).
    board = next(
        (i for i in instances
         if "board" in str(i.get("template") or i.get("template_id") or "").lower()),
        None,
    )
    if board:
        bid = board.get("id") or board.get("instance_id")
        status, _, _ = get(f"/api/runs/{RUN}/artifacts/{bid}")
        ok("board instance opens", status == 200, f"status={status}")
    else:
        print("ok - board instance opens (SKIP: no board in demo matter)")

    # Restart persistence via a FRESH SUBPROCESS on the same root.
    probe = (
        "import json,sys; sys.path.insert(0, %r); "
        "from agent_kit.case_record import CaseRecord; "
        "c = CaseRecord(__import__('pathlib').Path(%r), %r); "
        "a = c.data['artifacts']['%s']; "
        "rows = {r['row_id']: r.get('assertion') for r in a['rows']}; "
        "print(json.dumps({'v': len(a.get('versions', [])), 'rows': rows}))"
        % (str(AGENT), str(root), RUN, aid)
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    persisted = json.loads(out.stdout or "{}")
    ok(
        "fresh process reopens same state",
        persisted.get("rows", {}).get(row["row_id"]) == restored
        and persisted.get("v", 0) >= v_cur,
        out.stdout[:200] + out.stderr[:200],
    )

    # Pilot key review: list, approve, stale refused, changes tracked.
    status, pilot, _ = get("/api/pilot")
    matters = (pilot or {}).get("matters", [])
    ok("pilot keys list with draft status",
       status == 200 and len(matters) == 6
       and "DRAFT" in str((pilot or {}).get("key_status", "")),
       f"status={status} matters={len(matters)}")
    version = (pilot or {}).get("pilot_version", "")
    pending = [i for m in matters for i in m.get("issues", [])
               if i.get("state") == "pending"]
    ok("pilot issues start pending", len(pending) == 9,
       f"pending={len(pending)}")
    status, signed, _ = post("/api/pilot/review", {
        "matter": "pm-clean-breach", "issue": "formality",
        "decision": "approved", "reviewer": "Harness",
        "notes": "key matches the corpus text", "pilot_version": version,
    })
    ok("pilot approval recorded",
       status == 200 and signed.get("decision") == "approved",
       f"status={status} {str(signed)[:160]}")
    status, _, _ = post("/api/pilot/review", {
        "matter": "pm-clean-breach", "issue": "formality",
        "decision": "approved", "reviewer": "Harness",
        "notes": "", "pilot_version": "stale-version",
    })
    ok("pilot stale version refused", status == 409, f"status={status}")
    status, changed, _ = post("/api/pilot/review", {
        "matter": "pm-multi-bonus", "issue": "entitlement",
        "decision": "changes_requested", "reviewer": "Harness",
        "notes": "rating passage is wrong", "pilot_version": version,
    })
    ok("pilot changes requested",
       status == 200 and changed.get("decision") == "changes_requested",
       f"status={status}")
    status, pilot2, _ = get("/api/pilot")
    states = {f"{m['id']}/{i['id']}": (i["state"], len(i["history"]))
              for m in (pilot2 or {}).get("matters", [])
              for i in m.get("issues", [])}
    ok("pilot states persist with history",
       states.get("pm-clean-breach/formality") == ("approved", 1)
       and states.get("pm-multi-bonus/entitlement")
       == ("changes_requested", 1),
       str(states)[:200])
    status, _, _ = post("/api/pilot/review", {
        "matter": "pm-clean-breach", "issue": "formality",
        "decision": "approved", "reviewer": "Second",
        "notes": "", "pilot_version": version,
    })
    status, agree, _ = get("/api/pilot/agreement")
    pairs = (agree or {}).get("pairs", [])
    ok("pilot agreement reports shared kappa",
       status == 200 and len(pairs) == 1
       and pairs[0]["shared"] == 1
       and pairs[0]["kappa"] == 1.0,
       f"status={status} {str(pairs)[:160]}")

    # Evidence needs surface per issue (undeclared until frozen).
    status, needs, _ = get(f"/api/runs/{RUN}/needs")
    items = (needs or {}).get("items", [])
    ok("needs endpoint lists run issues",
       status == 200 and len(items) == 4
       and all(i.get("state") == "undeclared" for i in items),
       f"status={status} items={len(items)}")
    case_path = root / "workspace/case_records" / f"{RUN}.json"
    record = json.loads(case_path.read_text())
    record["evidence_needs"] = {
        "readiness": {
            "item": "readiness",
            "required": [
                {"label": "status", "passages": [["DEMO-STATUS", 1]]},
                {"label": "signoff", "passages": [["DEMO-PLAN", 9]]},
            ],
            "adverse": [],
            "superseded": [],
        }
    }
    case_path.write_text(json.dumps(record))
    status, needs2, _ = get(f"/api/runs/{RUN}/needs")
    by_item = {i["item"]: i for i in (needs2 or {}).get("items", [])}
    ready = by_item.get("readiness", {})
    ok("needs status reflects frozen obligations",
       status == 200 and ready.get("state") == "insufficient"
       and [m["label"] for m in ready.get("missing", [])] == ["signoff"],
       f"status={status} {str(ready)[:200]}")

    # A lawyer freezes another item's needs through the route.
    other = next(i["item"] for i in items if i["item"] != "readiness")
    status, frozen, _ = post(f"/api/runs/{RUN}/needs", {
        "item": other,
        "required": ["standing @ DEMO-STATUS:1", "notice"]})
    ok("needs freeze accepted",
       status == 200 and (frozen or {}).get("item") == other
       and len((frozen or {}).get("required", [])) == 2,
       f"status={status}")
    status, needs3, _ = get(f"/api/runs/{RUN}/needs")
    by_item3 = {i["item"]: i for i in (needs3 or {}).get("items", [])}
    ok("frozen needs listed",
       by_item3.get(other, {}).get("state") != "undeclared",
       f"state={by_item3.get(other, {}).get('state')}")
    # An item whose obligations are all known-absent reads abstain.
    third = next(i["item"] for i in items
                 if i["item"] not in ("readiness", other))
    status, _, _ = post(f"/api/runs/{RUN}/needs", {
        "item": third, "required": ["vanished-notice"]})
    status, needs4, _ = get(f"/api/runs/{RUN}/needs")
    row4 = {i["item"]: i for i in (needs4 or {}).get("items", [])}
    ok("known-absent item reads abstain",
       row4.get(third, {}).get("state") == "abstain"
       and row4.get(third, {}).get("abstain") is True,
       f"status={status} state={row4.get(third, {}).get('state')}")
    status, _, _ = post(f"/api/runs/{RUN}/needs", {
        "item": "no-such-item", "required": ["x @ D:1"]})
    ok("unknown item refused", status in (400, 404, 409),
       f"status={status}")
    status, _, _ = post(f"/api/runs/{RUN}/needs", {
        "item": other, "required": ["bad-pair @ DOC"]})
    ok("malformed pair refused", status in (400, 404, 409),
       f"status={status}")

    # C-0 collaboration: draft -> thread -> directive -> context, via HTTP.
    collab = f"/api/runs/{RUN}/collab"
    status, empty, _ = get(collab)
    ok("collab summary starts empty",
       status == 200 and (empty or {}).get("drafts") == []
       and (empty or {}).get("edges") == 0,
       f"status={status}")
    status, saved, _ = post(collab, {
        "op": "draft-save", "title": "Clause 8", "body": "first words",
        "author": "Harness"})
    ok("draft saved",
       status == 200 and (saved or {}).get("id") == "DR1"
       and (saved or {}).get("version") == 1,
       f"status={status} {str(saved)[:160]}")
    status, saved2, _ = post(collab, {
        "op": "draft-save", "draft": "DR1", "body": "revised words",
        "base": "1", "author": "Harness"})
    ok("draft versioned on fresh base",
       status == 200 and (saved2 or {}).get("version") == 2,
       f"status={status}")
    status, _, _ = post(collab, {
        "op": "draft-save", "draft": "DR1", "body": "stale write",
        "base": "1", "author": "Harness"})
    ok("stale draft base refused", status == 409, f"status={status}")
    status, sent, _ = post(collab, {
        "op": "draft-send", "draft": "DR1", "author": "Harness"})
    ok("draft sent",
       status == 200 and (sent or {}).get("state") == "sent",
       f"status={status}")
    status, thread, _ = post(collab, {
        "op": "thread-open", "object": "draft:DR1",
        "body": "Does the letter change this?", "challenge": True,
        "author": "Harness"})
    ok("challenge thread opened",
       status == 200 and (thread or {}).get("id") == "TH1",
       f"status={status}")
    status, replied, _ = post(collab, {
        "op": "thread-reply", "thread": "TH1",
        "body": "Checking the correspondence.", "author": "Harness"})
    ok("thread reply appended",
       status == 200 and (replied or {}).get("messages") == 2,
       f"status={status}")
    status, directive, _ = post(collab, {
        "op": "directive-submit", "kind": "redirect",
        "body": "Reassess clause 8 against the letter.",
        "base_refs": ["draft:DR1"], "from_thread": "TH1",
        "author": "Harness"})
    ok("directive submitted from thread",
       status == 200 and (directive or {}).get("id") == "DIR1",
       f"status={status}")
    status, mid, _ = get(collab)
    ok("edges link challenge and instruction",
       status == 200 and (mid or {}).get("edges") == 2
       and len((mid or {}).get("threads", [{}])[0].get("messages", [])) == 2,
       f"status={status} {str(mid)[:200]}")
    status, acked, _ = post(collab, {
        "op": "directive-ack", "directive": "DIR1",
        "agent": "harness-agent", "role": "research"})
    ok("directive acknowledged",
       status == 200 and (acked or {}).get("state") == "acknowledged",
       f"status={status}")
    status, applied, _ = post(collab, {
        "op": "directive-apply", "directive": "DIR1",
        "agent": "harness-agent", "role": "research"})
    ok("directive applied",
       status == 200 and (applied or {}).get("state") == "applied",
       f"status={status}")
    status, staged, _ = post(collab, {
        "op": "context-add", "source": "composer",
        "body": "The March letter.", "author": "Harness"})
    ok("context staged quarantined",
       status == 200 and (staged or {}).get("state") == "quarantined",
       f"status={status}")
    status, admitted, _ = post(collab, {
        "op": "context-admit", "context": "CI1", "author": "Harness",
        "user_role": "attorney"})
    ok("context admitted by counsel",
       status == 200 and (admitted or {}).get("state") == "admitted",
       f"status={status}")
    status, _, _ = post(collab, {"op": "no-such-op"})
    ok("unknown collab op refused", status == 409, f"status={status}")

    # C-2 threads mature: anchored open, agent reply, stamped edit.
    status, anchored, _ = post(collab, {
        "op": "thread-open", "object": "draft:DR1",
        "body": "About this span?", "author": "Harness",
        "version": "v2", "anchor_start": "0", "anchor_end": "5",
        "quoted": "revis"})
    ok("anchored thread opened",
       status == 200 and (anchored or {}).get("id") == "TH2",
       f"status={status}")
    status, agent_reply, _ = post(collab, {
        "op": "thread-reply", "thread": "TH2",
        "body": "Agent confirming the span.",
        "agent": "harness-agent", "role": "research"})
    ok("agent reply recorded",
       status == 200 and (agent_reply or {}).get("messages") == 2,
       f"status={status}")
    status, edited, _ = post(collab, {
        "op": "thread-edit", "thread": "TH2", "message": "M1",
        "body": "About this span (corrected)?", "author": "Harness"})
    ok("message edit accepted",
       status == 200 and (edited or {}).get("messages") == 2,
       f"status={status}")
    status, matured, _ = get(collab)
    th2 = next((t for t in (matured or {}).get("threads", [])
                if t.get("id") == "TH2"), {})
    ok("anchor, agent role, and edit stamp surface",
       status == 200 and th2.get("version_id") == "v2"
       and (th2.get("anchor") or {}).get("quoted") == "revis"
       and (th2.get("messages") or [{}, {}])[1].get("author", {}).get("type") == "agent"
       and (th2.get("messages") or [{}, {}])[1].get("author", {}).get("agent_role") == "research"
       and bool((th2.get("messages") or [{}])[0].get("edited_at"))
       and (th2.get("messages") or [{}])[0].get("body_md") == "About this span (corrected)?",
       f"status={status} {str(th2)[:240]}")
    status, _, _ = post(collab, {
        "op": "thread-edit", "thread": "TH2", "message": "M9",
        "body": "x", "author": "Harness"})
    ok("unknown message edit refused", status == 409, f"status={status}")

    # Verification: challenge/invalidate flip status, verdicts clear it.
    def draft_status():
        s, summ, _ = get(collab)
        ds = (summ or {}).get("drafts", [{}])
        return s, (ds[0].get("verification_status") if ds else None)
    status, v0 = draft_status()
    ok("earlier challenge left its mark",
       status == 200 and v0 == "challenged", f"status={status} v={v0}")
    status, _, _ = post(collab, {
        "op": "thread-open", "object": "draft:DR1",
        "body": "Harness challenge.", "challenge": True,
        "author": "Harness"})
    _, v1 = draft_status()
    ok("challenge marks challenged",
       status == 200 and v1 == "challenged",
       f"status={status} v={v1}")
    status, d2, _ = post(collab, {
        "op": "directive-submit", "kind": "redirect",
        "body": "Harness redo.", "author": "Harness"})
    post(collab, {"op": "directive-ack", "directive": "DIR2",
                  "agent": "harness-agent", "role": "research"})
    status, _, _ = post(collab, {
        "op": "directive-apply", "directive": "DIR2",
        "agent": "harness-agent", "role": "research",
        "invalidate": ["draft:DR1"]})
    _, v2 = draft_status()
    ok("invalidation marks stale",
       status == 200 and v2 == "stale"
       and (d2 or {}).get("id") == "DIR2",
       f"status={status} v={v2}")
    status, _, _ = post(collab, {
        "op": "draft-verify", "draft": "DR1", "verdict": "challenged",
        "author": "Harness"})
    ok("challenged verdict needs rationale", status == 409,
       f"status={status}")
    status, verdict, _ = post(collab, {
        "op": "draft-verify", "draft": "DR1", "verdict": "passed",
        "author": "Harness"})
    _, v3 = draft_status()
    ok("re-verification clears stale",
       status == 200 and v3 == "passed"
       and (verdict or {}).get("verification_status") == "passed",
       f"status={status} v={v3}")

    # Mid-run steering: an API-submitted directive is delivered once
    # by the real slice-start helper in a fresh process.
    status, d3, _ = post(collab, {
        "op": "directive-submit", "kind": "question",
        "body": "Harness steerage.", "author": "Harness"})
    ok("steering directive submitted",
       status == 200 and (d3 or {}).get("id") == "DIR3",
       f"status={status}")
    probe = (
        "import json,sys; sys.path.insert(0, %r); "
        "from legal_agent.loop import steering_messages; "
        "a = steering_messages(root=%r, run_id=%r); "
        "b = steering_messages(root=%r, run_id=%r); "
        "print(json.dumps({'first': [m['content'] for m in a], "
        "'second': len(b)}))"
        % (str(AGENT), str(root), RUN, str(root), RUN)
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    steered = json.loads(out.stdout or "{}")
    first = steered.get("first", [])
    ok(
        "fresh process steers once, then silent",
        len(first) == 1 and "DIR3" in first[0]
        and "Harness steerage." in first[0]
        and steered.get("second") == 0,
        (out.stdout or out.stderr)[:200],
    )

    # Governance: ack is agent-only, admit is counsel-only.
    status, _, _ = post(collab, {
        "op": "directive-ack", "directive": "DIR3", "author": "Harness"})
    ok("human ack refused", status == 409, f"status={status}")
    status, staged2, _ = post(collab, {
        "op": "context-add", "source": "composer",
        "body": "Second letter.", "author": "Harness"})
    ok("second context staged",
       status == 200 and (staged2 or {}).get("state") == "quarantined",
       f"status={status}")
    status, _, _ = post(collab, {
        "op": "context-admit", "context": "CI2", "author": "Harness"})
    ok("associate admit refused", status == 409, f"status={status}")
    status, admitted2, _ = post(collab, {
        "op": "context-admit", "context": "CI2", "author": "Harness",
        "user_role": "attorney"})
    ok("attorney admit accepted",
       status == 200 and (admitted2 or {}).get("state") == "admitted",
       f"status={status}")

    # Version-aware threads: bindings verify, drift surfaces.
    status, dr2, _ = post(collab, {
        "op": "draft-save", "title": "Drift", "body": "Harness anchor body",
        "author": "Harness"})
    ok("second draft saved",
       status == 200 and (dr2 or {}).get("id") == "DR2",
       f"status={status}")
    status, bound, _ = post(collab, {
        "op": "thread-open", "object": "draft:DR2",
        "body": "About v1?", "author": "Harness",
        "version": "v1", "anchor_start": "0", "anchor_end": "7",
        "quoted": "Harness"})
    bound_id = (bound or {}).get("id", "")
    ok("version-bound thread accepted",
       status == 200 and bound_id.startswith("TH"),
       f"status={status}")
    status, _, _ = post(collab, {
        "op": "thread-open", "object": "draft:DR2",
        "body": "Bad bind?", "author": "Harness", "version": "v9"})
    ok("unknown version refused", status == 409, f"status={status}")
    status, _, _ = post(collab, {
        "op": "thread-open", "object": "draft:DR2",
        "body": "Bad quote?", "author": "Harness",
        "anchor_start": "0", "anchor_end": "7", "quoted": "WRONG"})
    ok("mismatched quote refused", status == 409, f"status={status}")
    status, _, _ = post(collab, {
        "op": "draft-save", "draft": "DR2", "body": "Rewritten entirely",
        "base": "1", "author": "Harness"})
    status, drifted, _ = get(collab)
    th = next((t for t in (drifted or {}).get("threads", [])
               if t.get("id") == bound_id), {})
    ok("drift surfaces on old bindings",
       status == 200 and th.get("version_current") is False
       and th.get("anchor_match") is False,
       f"status={status} {str(th)[:200]}")

    # Exports carry the deliberation record with the work product.
    status, exp_c, _ = get(f"{base}/export?format=json")
    col = (exp_c or {}).get("collaboration") or {}
    ok("json export carries deliberation",
       status == 200 and len(col.get("drafts") or []) == 2
       and len(col.get("threads") or []) == 4
       and len(col.get("directives") or []) == 3
       and len(col.get("context") or []) == 2
       and len(col.get("edges") or []) == 4
       and (col.get("drafts") or [{}, {}])[0].get("verification_status") == "passed",
       f"status={status}")
    status, _, exp_cm = get(f"{base}/export?format=md")
    md_c = exp_cm.decode("utf8", "replace")
    ok("markdown export narrates deliberation",
       status == 200 and "## Collaboration" in md_c
       and "DR1" in md_c and "anchor drifted" in md_c,
       f"status={status}")

    status, full, _ = post(collab, {"op": "draft-get", "draft": "DR1"})
    ok("draft-get returns version history",
       status == 200 and len((full or {}).get("history", [])) == 2
       and (full or {}).get("history", [{}])[0].get("body_md") == "first words",
       f"status={status}")

    # C-1 vendor route: bundles served, anything else refused.
    status, _, content = get("/vendor/editor.js")
    ok("vendor editor served",
       status == 200 and len(content) > 100000 and b"makeEditor" in content,
       f"status={status} bytes={len(content)}")
    status, _, content = get("/vendor/render.js")
    ok("vendor renderer served",
       status == 200 and len(content) > 10000,
       f"status={status} bytes={len(content)}")
    status, _, _ = get("/vendor/editor.css")
    ok("non-js vendor refused", status == 404, f"status={status}")
    status, _, _ = get("/vendor/../ui.py")
    ok("vendor traversal refused", status == 404, f"status={status}")
    status, _, home = get("/")
    ok("panel ships per-draft editors",
       status == 200 and b"collab-draftslot-" in home
       and b"collabMountDraftEditors" in home
       and b"collabDraftStash" in home,
       f"status={status}")
    ok("panel ships a11y wiring",
       b'aria-live="polite"' in home
       and b'setAttribute("role", "alert")' in home
       and b"contentDOM.setAttribute" in home
       and b"collabUserRole" in home
       and b"activeElement" in home,
       "served page carries live regions, alert errors, labels, focus restore")

    # Collab state survives a fresh process on the same root.
    probe = (
        "import json,sys; sys.path.insert(0, %r); "
        "from agent_kit.case_record import CaseRecord; "
        "c = CaseRecord(__import__('pathlib').Path(%r), %r); "
        "print(json.dumps(c.collab_summary()))"
        % (str(AGENT), str(root), RUN)
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    persisted = json.loads(out.stdout or "{}")
    ok(
        "fresh process reopens collab state",
        persisted.get("edges") == 4
        and (persisted.get("drafts") or [{}])[0].get("state") == "sent"
        and (persisted.get("drafts") or [{}])[0].get("verification_status") == "passed"
        and (persisted.get("directives") or [{}])[0].get("state") == "applied"
        and (persisted.get("context") or [{}])[0].get("state") == "admitted",
        (out.stdout or out.stderr)[:200],
    )

    tmp.cleanup()
    if failures:
        print(f"{failures} step(s) failed")
        return 1
    print("journey green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
