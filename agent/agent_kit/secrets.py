"""Key engine: one place where secrets resolve, display, and redact.

Industry-standard shape, kept small for this repo:

- Resolution order per secret: direct environment variable,
  then `<NAME>_FILE` (Docker-secrets convention: the variable
  names a file whose contents are the secret), then a clear
  error naming where to put it. Values are stripped; empty
  counts as missing.
- Display: fingerprints (sha256 prefix), never values or
  lengths that hint at them. Any status surface shows source +
  fingerprint only.
- Redaction: `redact` scrubs known values from text destined
  for logs and traces.
- Hygiene: `check_env_file` warns on missing files, loose
  permissions (group/other readable), and unparsable lines —
  reporting variable NAMES only, never values.

What this module never does: print, log, return-in-error, or
persist a secret value; auto-modify file permissions (warn,
let the operator tighten); cache values beyond the caller.

    cd agent && .venv/bin/python -m agent_kit.secrets status
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

#: Secrets this repo resolves, in status-check order.
KNOWN_SECRETS = ("MODEL_API_KEY",)

#: Placeholder text replacing secret values in logs and traces.
REDACTED = "[REDACTED]"


class MissingSecretError(RuntimeError):
    """A required secret resolved nowhere."""


def resolve(name: str, *, hint: str = "") -> str:
    """Resolve a secret: $<NAME>, then $<NAME>_FILE, else raise.

    Never returns blank: empty values count as missing. `hint`
    names where to put the secret (shown in the error).
    """
    direct = (os.environ.get(name, "") or "").strip()
    if direct:
        return direct
    path = (os.environ.get(f"{name}_FILE", "") or "").strip()
    if path:
        try:
            value = Path(path).read_text().strip()
        except OSError as error:
            raise MissingSecretError(
                f"${name}_FILE={path} is unreadable: {error}"
            ) from None
        if value:
            return value
    where = hint or f"set ${name} (or ${name}_FILE)"
    raise MissingSecretError(f"{name} is not set: {where}")


def source_of(name: str) -> str:
    """Where a secret would resolve from: environment, file, missing."""
    if (os.environ.get(name, "") or "").strip():
        return "environment"
    if (os.environ.get(f"{name}_FILE", "") or "").strip():
        return "file"
    return "missing"


def fingerprint(secret: str) -> str:
    """A displayable identity for a secret that reveals nothing.

    sha256 prefix: stable per value, irreversible, and carries no
    key characters (unlike last-four conventions).
    """
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def redact(text: str, values: list[str]) -> str:
    """Scrub known secret values from text bound for logs/traces."""
    out = str(text or "")
    for value in values:
        if value:
            out = out.replace(value, REDACTED)
    return out


def check_env_file(path: str | Path) -> list[str]:
    """Hygiene warnings for a .env file. Reports names, never values."""
    warnings: list[str] = []
    candidate = Path(path)
    if not candidate.exists():
        return [f"{candidate}: no such file (copy .env.example to start)"]
    try:
        mode = stat.S_IMODE(candidate.stat().st_mode)
    except OSError as error:
        return [f"{candidate}: cannot stat: {error}"]
    if mode & 0o077:
        warnings.append(
            f"{candidate}: permissions {mode:o} are group/other readable; "
            "tighten with: chmod 600 "
            + str(candidate)
        )
    try:
        lines = candidate.read_text().splitlines()
    except OSError as error:
        warnings.append(f"{candidate}: cannot read: {error}")
        return warnings
    names = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, sep, _ = stripped.partition("=")
        if not sep or not name.strip().replace("_", "").isalnum():
            warnings.append(f"{candidate}:{number}: unparsable line")
            continue
        names.append(name.strip())
    for secret in KNOWN_SECRETS:
        if secret not in names:
            warnings.append(f"{candidate}: {secret} not listed")
    return warnings


def status(root: str | Path) -> dict:
    """Secrets status for a repo root: sources, fingerprints, warnings."""
    entries = {}
    for name in KNOWN_SECRETS:
        source = source_of(name)
        entry: dict = {"source": source, "fingerprint": ""}
        if source != "missing":
            try:
                entry["fingerprint"] = fingerprint(resolve(name))
            except MissingSecretError:
                entry["source"] = "missing"
        entries[name] = entry
    warnings = check_env_file(Path(root) / ".env")
    return {
        "secrets": entries,
        "warnings": warnings,
        "healthy": all(e["source"] != "missing" for e in entries.values())
        and not warnings,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["status"]:
        print("usage: python -m agent_kit.secrets status", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    report = status(root)
    for name, entry in report["secrets"].items():
        print(f"{name}: {entry['source']} {entry['fingerprint']}".rstrip())
    for warning in report["warnings"]:
        print(f"warning: {warning}")
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
