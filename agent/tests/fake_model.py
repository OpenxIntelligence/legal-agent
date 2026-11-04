"""An offline Model API for the tests: `Scripted(model).client` is a real OpenAI client
whose requests never leave the machine. Nothing here is part of the agent.

ScriptedModel recognises:
- agent turns (requests with `tools`): the next scripted tool call;
- text extraction ("Transcribe document ..."): the transcript you supply;
- document reading ("VERIFIED PAGE TEXT"): evidence quoting the transcript;
- the checklist check ("REFERENCE CHECKLIST"): coverage by matching item ids, plus feedback;
- the claims check ("CLAIMS TO CHECK"): one result per assessment unit, every unit
  supported unless `unsupported` marks it ({"unit": <unit id>, "reason": ...});
- the materiality check ("COUNTEREVIDENCE MATERIALITY"): one verdict per
  objection id, immaterial unless `material` marks it ({"id": ..., "material": True});
  one resolution per open-task id, unaddressed unless `addressed` marks it
  ({"id": ..., "addressed": True});
- signature comparison ("SIGNATURE COMPARISON"): the verdict `signature` returns;
plus SAM segmentation calls, answered from recorded box outputs.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Callable

import httpx
from agent_kit.client import DEFAULT_BASE_URL
from agent_kit.settings import sam_model
from openai import OpenAI

FEEDBACK = (
    "The checklist leaves out an area the question turns on. Look again at who was "
    "involved and what their role means for the answer."
)


DOC_ID = re.compile(r"(?:Transcribe document|case document) ([A-Z]+-[A-Z0-9]+)")


class Refused(Exception):
    """Raise from a responder to make the scripted API answer HTTP 400 with this message."""


def _text_of(request: dict) -> str:
    if isinstance(request.get("input"), str):
        return request["input"]
    parts = []
    for item in request.get("input", []):
        for block in (
            item.get("content", []) if isinstance(item.get("content"), list) else []
        ):
            if isinstance(block, dict) and block.get("type") == "input_text":
                parts.append(block["text"])
    return "\n".join(parts)


def history(request: dict) -> list:
    """Function calls and outputs seen so far in a local-mode conversation."""
    items = request.get("input")
    return [
        item
        for item in (items if isinstance(items, list) else [])
        if item.get("type") in ("function_call", "function_call_output")
    ]


def tool_call(name: str, arguments: dict, n: int) -> dict:
    return {
        "output": [
            {
                "type": "function_call",
                "call_id": f"call-{n}",
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
        "usage": {"input_tokens": 1500 + 50 * n, "output_tokens": 80},
    }


class ScriptedModel:
    def __init__(
        self,
        transcripts: dict,
        planner: Callable[[dict], tuple | None],
        sam_outputs: dict | None = None,
        evidence: Callable[[str, list], list] | None = None,
        unsupported: Callable[[str], list] | None = None,
        support: Callable[[str], dict] | None = None,
        material: Callable[[str], list] | None = None,
        addressed: Callable[[str], list] | None = None,
        feedback: str = FEEDBACK,
        signature: Callable[[str], dict] | None = None,
    ) -> None:
        self.transcripts = transcripts
        self.planner = planner
        self.sam_outputs = sam_outputs or {}
        self.evidence = evidence or default_evidence
        self.unsupported = unsupported or (lambda text: [])
        self.claims_support = support or (lambda text: {})
        self.material = material or (lambda text: [])
        self.addressed = addressed or (lambda text: [])
        self.feedback = feedback
        self.signature = signature
        self.n = 0

    def __call__(self, kind: str, payload) -> dict:
        self.n += 1
        if kind == "segment":
            image_url, _prompt = payload
            return {
                "output_text": self.sam_outputs.get(image_url, ""),
                "usage": {"input_tokens": 800, "output_tokens": 40},
            }
        request = payload
        if "tools" in request:
            action = self.planner(request)
            if action is None:
                return {"output_text": "(script finished)"}
            name, arguments = action
            return tool_call(name, arguments, self.n)
        text = _text_of(request)
        if "REFERENCE CHECKLIST" in text:
            covered = checklist_coverage(text)
            return {
                "output_text": json.dumps(
                    {
                        "covered": covered,
                        "feedback": "" if all(covered.values()) else self.feedback,
                    }
                ),
                "usage": {"input_tokens": 900, "output_tokens": 60},
            }
        if "SIGNATURE COMPARISON" in text:
            verdict = (
                self.signature(text)
                if self.signature
                else {"verdict": "consistent", "reason": "Scripted comparison."}
            )
            return {
                "output_text": json.dumps(verdict),
                "usage": {"input_tokens": 2500, "output_tokens": 60},
            }
        if "CLAIMS TO CHECK" in text:
            units = claims_units(text)
            bad = {
                u.get("unit"): u.get("reason", "")
                for u in self.unsupported(text)
                if isinstance(u, dict) and isinstance(u.get("unit"), str)
            }
            refs = self.claims_support(text)
            if not isinstance(refs, dict):
                refs = {}
            cited = _prompt_citations(text)
            results = []
            for u in units:
                entry: dict = {"id": u}
                if u in bad:
                    entry["verdict"] = "unsupported"
                    if bad[u]:
                        entry["reason"] = str(bad[u])
                else:
                    entry["verdict"] = "supported"
                if isinstance(refs.get(u), list):
                    entry["support"] = [str(s) for s in refs[u]]
                elif u not in bad:
                    entry["support"] = cited.get(u) or ["investigation state"]
                results.append(entry)
            return {
                "output_text": json.dumps({"results": results}),
                "usage": {"input_tokens": 1800, "output_tokens": 60},
            }
        if "COUNTEREVIDENCE MATERIALITY" in text:
            ids = objection_ids(text)
            verdicts = {
                v.get("id"): v
                for v in self.material(text)
                if isinstance(v, dict) and isinstance(v.get("id"), str)
            }
            task_ids = open_task_ids(text)
            addressed = {
                v.get("id"): v
                for v in self.addressed(text)
                if isinstance(v, dict) and isinstance(v.get("id"), str)
            }
            return {
                "output_text": json.dumps(
                    {
                        "verdicts": [
                            {
                                "id": i,
                                "material": bool(verdicts.get(i, {}).get("material")),
                                "reason": str(
                                    verdicts.get(i, {}).get("reason", "")
                                ),
                            }
                            for i in ids
                        ],
                        "resolutions": [
                            {
                                "id": i,
                                "addressed": bool(
                                    addressed.get(i, {}).get("addressed")
                                ),
                                "reason": str(
                                    addressed.get(i, {}).get("reason", "")
                                ),
                            }
                            for i in task_ids
                        ],
                    }
                ),
                "usage": {"input_tokens": 900, "output_tokens": 60},
            }
        match = DOC_ID.search(text)
        doc = match.group(1) if match else ""
        pages = self.transcripts.get(doc, [{"page": 1, "text": ""}])
        if text.startswith("Transcribe document"):
            return {
                "output_text": json.dumps({"pages": pages}),
                "usage": {"input_tokens": 3000, "output_tokens": 900},
            }
        return {
            "output_text": json.dumps(
                {"evidence": self.evidence(doc, pages), "note": ""}
            ),
            "usage": {"input_tokens": 3500, "output_tokens": 300},
        }


def _json_lists(text: str) -> list:
    decoder, found, i = json.JSONDecoder(), [], 0
    while (i := text.find("[", i)) != -1:
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(value, list):
            found.append(value)
        i = end
    return found


UNIT_IDS = re.compile(r"ASSESSMENT UNIT IDS:\s*(\[.*?\])", re.S)
OBJECTION_IDS = re.compile(r"OBJECTION IDS:\s*(\[.*?\])", re.S)


def claims_units(text: str) -> list:
    """Assessment-unit ids the claims judge was asked to assess (may be empty)."""
    match = UNIT_IDS.search(text)
    if not match:
        return []
    try:
        units = json.loads(match.group(1))
    except ValueError:
        return []
    return [u for u in units if isinstance(u, str)]


def _prompt_citations(text: str) -> dict:
    """First citation per assessment unit, from the prompt's UNITS block."""
    match = re.search(r"UNITS \(JSON\):\n(\[.*?\])\n\nINVESTIGATION", text, re.S)
    if not match:
        return {}
    try:
        units = json.loads(match.group(1))
    except ValueError:
        return {}
    cited = {}
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str):
            continue
        for citation in unit.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            doc = str(citation.get("document_id", "")).strip()
            if doc:
                cited[unit["id"]] = [f"{doc} p{citation.get('page', '')}"]
                break
    return cited


def objection_ids(text: str) -> list:
    """Objection ids the materiality judge was asked to assess (may be empty)."""
    match = OBJECTION_IDS.search(text)
    if not match:
        return []
    try:
        ids = json.loads(match.group(1))
    except ValueError:
        return []
    return [u for u in ids if isinstance(u, str)]


OPEN_TASK_IDS = re.compile(r"OPEN TASK IDS[^\[]*(\[.*?\])", re.S)


def open_task_ids(text: str) -> list:
    """Open-task ids the materiality judge was asked to resolve (may be empty)."""
    match = OPEN_TASK_IDS.search(text)
    if not match:
        return []
    try:
        ids = json.loads(match.group(1))
    except ValueError:
        return []
    return [u for u in ids if isinstance(u, str)]


def checklist_coverage(text: str) -> dict:
    """Scripted judge: a reference item is covered (True) by an agent item whose id contains it (or vice versa)."""
    lists = _json_lists(text)
    if len(lists) < 2:
        return {}
    reference, agent = lists[0], lists[1]
    agent_ids = [str(a.get("id", "")) for a in agent if isinstance(a, dict)]
    coverage = {}
    for ref in reference:
        rid = str(ref.get("id", ""))
        coverage[rid] = any(a and (a == rid or rid in a or a in rid) for a in agent_ids)
    return coverage


def default_evidence(doc: str, pages: list) -> list:
    for page in pages:
        lines = [
            line.strip() for line in page["text"].splitlines() if len(line.strip()) > 25
        ]
        if lines:
            return [
                {
                    "page": page["page"],
                    "quote": lines[0],
                    "claim": f"{doc} says: {lines[0][:80]}",
                    "interpretation": "Scripted reading.",
                    "ambiguity": "",
                }
            ]
    return []


def _response_body(answer: dict, n: int) -> dict:
    """A Responses API body for a scripted answer (output items, or output_text)."""
    output = answer.get("output") or []
    if not output and answer.get("output_text"):
        output = [
            {
                "type": "message",
                "id": f"msg-{n}",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": answer["output_text"],
                        "annotations": [],
                    }
                ],
            }
        ]
    usage = answer.get("usage") or {"input_tokens": 100, "output_tokens": 20}
    return {
        "id": f"resp-scripted-{n}",
        "object": "response",
        "created_at": 0,
        "model": "scripted",
        "status": "completed",
        "output": [dict(item, id=item.get("id", f"item-{n}")) for item in output],
        "usage": dict(
            usage, total_tokens=usage["input_tokens"] + usage["output_tokens"]
        ),
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


class ScriptedTransport(httpx.BaseTransport):
    """Answers /responses, /files and /models offline, and records every call in `calls`.

    Each call is (kind, payload): ("respond", request body), ("segment", (image_url, prompt))
    for SAM, or ("upload", file name). `responder(kind, payload)` gives the answer.
    """

    def __init__(self, responder: Callable[[str, object], dict]) -> None:
        self.responder = responder
        self.calls: list = []
        self.headers: list = []
        self._lock = threading.Lock()
        self._n = 0

    def _next(self) -> int:
        with self._lock:
            self._n += 1
            return self._n

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path, n = request.url.path, self._next()
        if path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": []})
        if path.endswith("/files"):
            name = re.search(rb'filename="([^"]*)"', request.read())
            self.calls.append(("upload", name.group(1).decode() if name else ""))
            return httpx.Response(
                200,
                json={
                    "id": f"file-scripted-{n}",
                    "object": "file",
                    "bytes": 0,
                    "created_at": 0,
                    "filename": name.group(1).decode() if name else "file",
                    "purpose": "user_data",
                    "status": "uploaded",
                },
            )
        self.headers.append(dict(request.headers))
        body = json.loads(request.content or b"{}")
        if body.get("model") == sam_model():
            content = body["input"][0]["content"]
            prompt = next(b["text"] for b in content if b["type"] == "input_text")
            image = next(b["image_url"] for b in content if b["type"] == "input_image")
            kind, payload = "segment", (image, prompt)
        else:
            kind, payload = "respond", body
        self.calls.append((kind, payload))
        try:
            answer = self.responder(kind, payload) or {}
        except Refused as error:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": str(error),
                        "type": "invalid_request_error",
                        "code": None,
                        "param": None,
                    }
                },
            )
        return httpx.Response(200, json=_response_body(answer, n))


class Scripted:
    """An OpenAI client answered by a ScriptedModel, plus the calls it received."""

    def __init__(self, model: Callable[[str, object], dict]) -> None:
        self.transport = ScriptedTransport(model)
        self.client: OpenAI = OpenAI(
            api_key="offline-test",
            base_url=DEFAULT_BASE_URL,
            max_retries=0,
            http_client=httpx.Client(transport=self.transport),
        )

    @property
    def calls(self) -> list:
        return self.transport.calls

    @property
    def responder(self) -> Callable[[str, object], dict]:
        return self.transport.responder

    @responder.setter
    def responder(self, value: Callable[[str, object], dict]) -> None:
        self.transport.responder = value
