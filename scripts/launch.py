#!/usr/bin/env python3
"""Local Legal Agent launcher. Demo is isolated and never loads .env."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".legal-agent-demo"


def prepare_demo(path: Path) -> Path:
    path = path.expanduser().resolve()
    # Demo data must never become the live application's workspace.
    agent = (ROOT / "agent").resolve()
    if path == ROOT or path == agent or agent in path.parents:
        raise ValueError("Choose a separate demo folder outside agent/.")
    if path.exists() and not path.is_dir():
        raise ValueError("Demo root must be a directory.")
    if path.exists() and any(path.iterdir()) and not (path / MARKER).is_file():
        raise ValueError("Demo folder is not owned by Legal Agent; choose an empty folder.")
    path.mkdir(parents=True, exist_ok=True)
    (path / MARKER).write_text("Legal Agent synthetic demo only\n")
    return path


def demo_environment(path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    env.update(LEGAL_AGENT_ROOT=str(path.resolve()), LEGAL_AGENT_FAKE_MODEL="1",
               LEGAL_AGENT_DEMO="1", PYTHON=sys.executable)
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Legal Agent — evidence to reviewed work product")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="isolated fictional workspace (default; no API calls)")
    mode.add_argument("--live", action="store_true", help="local investigation console; uses your agent/.env when you start a run")
    parser.add_argument("--demo-root", type=Path, default=ROOT / ".local/demo")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    command = [sys.executable, str(ROOT / "ui/ui.py"), "--port", str(args.port)]
    if args.live:
        env.pop("LEGAL_AGENT_ROOT", None)
        env.pop("LEGAL_AGENT_FAKE_MODEL", None)
        env.pop("LEGAL_AGENT_DEMO", None)
        print("Legal Agent · local investigation · attorney review required", flush=True)
    else:
        try:
            path = prepare_demo(args.demo_root)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        env = demo_environment(path)
        command += ["--agent-root", str(path)]
        print("Legal Agent · synthetic demo · model calls disabled", flush=True)
        print(f"Your demo edits persist in {path}", flush=True)
    print(f"Open http://127.0.0.1:{args.port}", flush=True)
    try:
        return subprocess.call(command, env=env, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
