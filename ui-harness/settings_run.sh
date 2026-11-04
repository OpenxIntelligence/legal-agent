#!/bin/sh
# Real credential persistence in an owned fake project; no provider invocation.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
TASK_TMP=$(mktemp -d "${TMPDIR:-/tmp}/legal-agent-settings-browser.XXXXXX")
ROOT="$TASK_TMP/workshop"
SHOT=${BROWSER_SHOTS:-/tmp/legal-agent-settings-shots}
PORT=${BROWSER_PORT:-8779}
DEMO_PORT=${BROWSER_DEMO_PORT:-8780}
DBGP=${BROWSER_DEBUG_PORT:-19236}
HS=$(sh "$REPO/ui-harness/find_browser.sh")
PY=${PYTHON:-python3}
mkdir -p "$ROOT/agent" "$TASK_TMP/profile" "$TASK_TMP/demo" "$SHOT"
printf '#!/bin/sh\nexit 99\n' > "$ROOT/agent/helper"
printf 'MODEL_API_KEY=settings-fixture-initial-token\nMODEL_API_MODEL=fixture-no-provider\n' > "$ROOT/agent/.env"
chmod 600 "$ROOT/agent/.env"
unset LEGAL_AGENT_ROOT LEGAL_AGENT_FAKE_MODEL
cd "$REPO"
"$PY" ui/ui.py --host 127.0.0.1 --port "$PORT" --workshop "$ROOT" > "$TASK_TMP/live.log" 2>&1 &
LIVE=$!
"$PY" ui/ui.py --host 127.0.0.1 --port "$DEMO_PORT" --agent-root "$TASK_TMP/demo" > "$TASK_TMP/demo.log" 2>&1 &
DEMO=$!
trap 'kill "$LIVE" "$DEMO" 2>/dev/null || true; wait "$LIVE" 2>/dev/null || true; wait "$DEMO" 2>/dev/null || true; rm -rf "$TASK_TMP"' EXIT INT TERM
for i in $(seq 1 40); do
  if ! kill -0 "$LIVE" 2>/dev/null || ! kill -0 "$DEMO" 2>/dev/null; then echo 'Owned settings server exited'; tail -15 "$TASK_TMP/live.log" "$TASK_TMP/demo.log"; exit 3; fi
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/settings" && curl -sf -o /dev/null "http://127.0.0.1:$DEMO_PORT/api/settings"; then break; fi
  sleep 0.5
done
node ui-harness/settings_validate.mjs "http://127.0.0.1:$PORT" "$DBGP" "$SHOT" "$HS" "$TASK_TMP/profile" "$ROOT" "http://127.0.0.1:$DEMO_PORT"
