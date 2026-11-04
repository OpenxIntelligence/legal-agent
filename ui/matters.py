"""Local matter intake and instruction persistence, with no model dependency.

The checklist and workflow here are deterministic preparation aids, not model
responses or legal assessments. Document bytes go only to the supplied ingestion
pipeline; matter.json contains metadata and document receipts.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Callable
from uuid import uuid4

_SCHEMA = "legal-agent-matter/v1"
_MATTER_ID = re.compile(r"matter-[a-f0-9]{32}")
_FILENAME = re.compile(r"([A-Za-z0-9_-]{1,64})\.(txt|md|pdf)", re.IGNORECASE)
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _field(body: dict, key: str, limit: int, default: str = "") -> str:
    value = body.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if len(value) > limit or "\x00" in value:
        raise ValueError(f"{key} must be at most {limit} characters and contain no NUL")
    return value


def _documents(body: dict) -> list[dict]:
    docs = body.get("documents", [])
    if not isinstance(docs, list) or len(docs) > 10:
        raise ValueError("Attach at most 10 documents")
    validated = []
    stems = set()
    for doc in docs:
        if not isinstance(doc, dict):
            raise ValueError("Each document must be an object")
        filename = doc.get("filename")
        match = _FILENAME.fullmatch(filename) if isinstance(filename, str) else None
        if not match:
            raise ValueError("Document names need 1–64 letters, digits, underscores or dashes and .txt, .md or .pdf")
        stem, suffix = match.groups()
        if stem.casefold() in stems:
            raise ValueError("Each document needs a distinct name before its extension")
        stems.add(stem.casefold())
        if suffix.lower() in {"txt", "md"}:
            text = doc.get("text")
            if not isinstance(text, str) or len(text) > 256_000 or doc.get("content_b64") is not None:
                raise ValueError("Text documents require text of at most 256000 characters")
            validated.append({"filename": filename, "text": text})
        else:
            blob = doc.get("content_b64")
            if not isinstance(blob, str) or len(blob) > 6_666_668 or doc.get("text") is not None:
                raise ValueError("PDF documents require base64 bytes of at most 5MB")
            try:
                decoded = base64.b64decode(blob, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("PDF bytes are not valid base64") from None
            if len(decoded) > 5_000_000 or not decoded.startswith(b"%PDF"):
                raise ValueError("PDF documents must contain real PDF bytes (%PDF header), at most 5MB")
            validated.append({"filename": filename, "content_b64": blob})
    return validated


class MatterStore:
    """Own UUID-named matter directories below one configured local root."""

    def __init__(self, root: Path, ingest: Callable[[Path, dict], dict]):
        self.root = Path(root).expanduser()
        if self.root.is_symlink():
            raise ValueError("Matter storage root must not be a symlink")
        self.root = self.root.resolve()
        self.ingest = ingest
        # Requests may reopen a store for the same root; serialize their writes too.
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.root, threading.RLock())

    def _safe(self, path: Path) -> Path:
        """Reject symlink components before reads or writes below the owned root."""
        if self.root.is_symlink():
            raise ValueError("Matter storage root must not be a symlink")
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ValueError("Matter path must remain inside its storage root") from None
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Matter paths must not contain symlinks")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Matter path escaped its storage root")
        return path

    def path(self, matter_id: str) -> Path:
        if not isinstance(matter_id, str) or not _MATTER_ID.fullmatch(matter_id):
            raise ValueError("Invalid matter id")
        path = self._safe(self.root / matter_id)
        record = self._safe(path / "matter.json")
        if not path.is_dir() or not record.is_file():
            raise FileNotFoundError("Matter not found")
        data = json.loads(record.read_text())
        if not isinstance(data, dict) or data.get("schema") != _SCHEMA or data.get("id") != matter_id:
            raise FileNotFoundError("Directory is not an owned Legal Agent matter")
        return path

    def get(self, matter_id: str) -> dict:
        with self._lock:
            return json.loads((self.path(matter_id) / "matter.json").read_text())

    def list(self) -> list[dict]:
        with self._lock:
            if not self.root.exists():
                return []
            self._safe(self.root)
            records = []
            for path in self.root.iterdir():
                if not _MATTER_ID.fullmatch(path.name):
                    continue
                try:
                    records.append(self.get(path.name))
                except (ValueError, FileNotFoundError, OSError):
                    # Foreign directories, malformed records and symlinks are never adopted.
                    continue
            return sorted(records, key=lambda item: item["updated"], reverse=True)

    @staticmethod
    def _missing(data: dict) -> list[str]:
        missing = []
        if not data["documents"]:
            missing.append("Add documents or identify which records should support the answer.")
        if not data["jurisdiction"]:
            missing.append("Jurisdiction is unspecified; applicable law cannot be assumed.")
        if not data["deadline"]:
            missing.append("No deadline reported; confirm whether any response or filing date applies.")
        if any(doc.get("state") == "transcription_pending" for doc in data["documents"]):
            missing.append("Some PDFs still need extraction before their text can be analyzed.")
        if any(doc.get("state") == "quarantined" for doc in data["documents"]):
            missing.append("Quarantined documents must be reviewed before their text can be analyzed.")
        if data["status"] == "incomplete":
            missing.append("One or more document uploads failed; inspect the intake record before starting.")
        return missing

    def _write(self, path: Path, data: dict) -> None:
        target = self._safe(path / "matter.json")
        # A unique temp name prevents a pre-existing local temp file from being adopted.
        temp = self._safe(path / f".matter-{uuid4().hex}.tmp")
        with temp.open("x") as out:
            out.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        temp.replace(target)

    def _objective(self, path: Path, data: dict) -> None:
        case = self._safe(path / "case")
        case.mkdir(exist_ok=True)
        target = self._safe(case / "objective.md")
        followups = [m["body"] for m in data["messages"] if m.get("kind") == "followup"]
        content = (
            "# Matter assignment\n\n"
            f"## Question\n{data['question']}\n\n"
            "## User-reported context (not verified)\n"
            f"- Jurisdiction: {data['jurisdiction'] or 'Unspecified; ask before applying law'}\n"
            f"- Deadline: {data['deadline'] or 'Not reported; do not calculate or invent one'}\n"
            f"- Requested deliverable: {data['deliverable']}\n"
            f"- User role: {data['role'] or 'Unspecified'}\n\n"
            "## Investigation requirements\n"
            "Distinguish user allegations, recorded facts, inferences, and legal conclusions. "
            "Use the selected matter's inbox; do not assume facts from another matter. "
            "Link each material factual assertion to its document and page with an exact passage. "
            "Seek counterevidence and alternative explanations. State gaps and unresolved conflicts. "
            "Do not invent laws, authorities, citations, dates, or missing evidence. "
            "Ask for missing jurisdiction or scope when needed; do not imply deterministic checks "
            "establish legal correctness. Produce a reviewable draft for qualified human review.\n"
        )
        if followups:
            content += "\n## Later user instructions (not yet evidence)\n"
            content += "\n\n".join(f"### Follow-up {i}\n{text}" for i, text in enumerate(followups, 1)) + "\n"
        temp = self._safe(case / f".objective-{uuid4().hex}.tmp")
        with temp.open("x") as out:
            out.write(content)
        temp.replace(target)

    def create(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Matter intake must be an object")
        question = _field(body, "question", 12_000)
        if len(question) < 10:
            raise ValueError("Describe your question in at least 10 characters")
        fields = {"jurisdiction": _field(body, "jurisdiction", 500),
                  "deadline": _field(body, "deadline", 200),
                  "deliverable": _field(body, "deliverable", 500, "Evidence memo") or "Evidence memo",
                  "role": _field(body, "role", 100)}
        docs = _documents(body)
        stamp = _now()
        data = {"schema": _SCHEMA, "id": f"matter-{uuid4().hex}",
                "title": question.splitlines()[0][:90], "question": question,
                **fields, "status": "prepared", "documents": [], "created": stamp,
                "updated": stamp, "messages": [{"role": "user", "body": question,
                                                   "at": stamp, "kind": "assignment"}],
                "plan": ["Confirm the question, scope, jurisdiction, and reported dates.",
                         "Inspect the supplied records and link findings to exact source passages.",
                         "Test contrary evidence and identify gaps before drafting.",
                         "Prepare the requested work product for human review."], "missing": []}
        with self._lock:
            self._safe(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._safe(self.root / data["id"])
            path.mkdir()  # A collision fails rather than adopting an existing directory.
            self._write(path, data)
            self._objective(path, data)
            failures = []
            for doc in docs:
                try:
                    result = self.ingest(path, doc)
                    if not isinstance(result, dict):
                        raise RuntimeError("Document ingestion returned no receipt")
                    receipt = {key: value for key, value in result.items()
                               if key in {"document_id", "file", "state", "pages", "sha256", "quarantine_reason"}}
                    receipt["id"] = receipt.get("document_id", Path(doc["filename"]).stem)
                    receipt.setdefault("file", doc["filename"])
                    data["documents"].append(receipt)
                except (RuntimeError, ValueError, OSError) as error:
                    failures.append({"file": doc["filename"], "error": str(error)[:500]})
            if failures:
                data["status"] = "incomplete"
                data["upload_errors"] = failures
            data["missing"] = self._missing(data)
            note = ("Intake saved. This is a deterministic preparation checklist, not a model "
                    "assessment or legal advice. No investigation has run. The proposed workflow "
                    "can be reviewed before starting.")
            if failures:
                note += f" {len(failures)} document upload(s) failed; successful uploads were retained."
            data["messages"].append({"role": "assistant", "body": note, "at": _now(),
                                      "kind": "intake_checklist"})
            data["updated"] = _now()
            self._write(path, data)
            return data

    def add_documents(self, matter_id: str, body: dict) -> dict:
        """Append validated records; failed files never erase successful receipts."""
        if not isinstance(body, dict):
            raise ValueError("Document upload must be an object")
        docs = _documents(body)
        if not docs:
            raise ValueError("Choose at least one document")
        with self._lock:
            path = self.path(matter_id)
            data = self.get(matter_id)
            existing = {Path(str(doc.get("file") or doc.get("id") or "")).stem.casefold()
                        for doc in data["documents"]}
            if len(data["documents"]) + len(docs) > 10:
                raise ValueError("A matter can contain at most 10 documents")
            if any(Path(doc["filename"]).stem.casefold() in existing for doc in docs):
                raise ValueError("A document with this name already exists; rename the new upload. Existing records cannot be overwritten.")
            failures = list(data.get("upload_errors") or [])
            added = []
            for doc in docs:
                try:
                    result = self.ingest(path, doc)
                    if not isinstance(result, dict):
                        raise RuntimeError("Document ingestion returned no receipt")
                    receipt = {key: value for key, value in result.items()
                               if key in {"document_id", "file", "state", "pages", "sha256", "quarantine_reason"}}
                    receipt["id"] = receipt.get("document_id", Path(doc["filename"]).stem)
                    receipt.setdefault("file", doc["filename"])
                    data["documents"].append(receipt)
                    added.append(receipt["file"])
                    failures = [failure for failure in failures
                                if failure.get("file", "").casefold() != doc["filename"].casefold()]
                except (RuntimeError, ValueError, OSError) as error:
                    failures = [failure for failure in failures
                                if failure.get("file", "").casefold() != doc["filename"].casefold()]
                    failures.append({"file": doc["filename"], "error": str(error)[:500]})
            data["upload_errors"] = failures
            data["status"] = "incomplete" if failures else "prepared"
            data["missing"] = self._missing(data)
            note = f"{len(added)} document(s) added locally. Existing run findings have not been updated. Start a new investigation to analyze the new records."
            if failures:
                note += f" {len(failures)} upload(s) remain unsuccessful; successful documents were retained."
            data["messages"].append({"role": "assistant", "kind": "documents_saved", "body": note, "at": _now()})
            data["updated"] = _now()
            self._write(path, data)
            return data

    def message(self, matter_id: str, text: str, delivery: dict | None = None) -> dict:
        text = _field({"message": text}, "message", 12_000)
        if not text:
            raise ValueError("Enter a follow-up instruction")
        with self._lock:
            path = self.path(matter_id)
            # Validate both destinations before changing either persisted record.
            self._safe(path / "case/objective.md")
            self._safe(path / "matter.json")
            data = self.get(matter_id)
            metadata = dict(delivery) if isinstance(delivery, dict) else None
            acknowledgment = (
                "Instruction saved for the next new investigation. The agent has not run on this "
                "instruction; existing runs and their saved instruction snapshots are unchanged.")
            if metadata and (metadata.get("queued") is True or any(
                    metadata.get(key) == "queued" for key in ("status", "state", "kind"))):
                run_id = str(metadata.get("run_id") or "selected run")
                acknowledgment = f"Queued for next resume of run {run_id}; not yet applied."
            user_message = {"role": "user", "body": text, "at": _now(), "kind": "followup"}
            receipt = {"role": "assistant", "body": acknowledgment,
                       "at": _now(), "kind": "instruction_saved"}
            if metadata is not None:
                user_message["delivery"] = metadata
                receipt["delivery"] = metadata
            data["messages"].extend([user_message, receipt])
            data["updated"] = _now()
            self._objective(path, data)
            self._write(path, data)
            return data
