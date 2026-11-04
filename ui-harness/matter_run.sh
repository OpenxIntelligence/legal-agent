#!/bin/sh
# Isolated, no-provider browser acceptance for matter intake and persistence.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
TASK_TMP=$(mktemp -d "${TMPDIR:-/tmp}/legal-agent-matter-browser.XXXXXX")
ROOT="$TASK_TMP/root"
SHOT=${BROWSER_SHOTS:-/tmp/legal-agent-matter-shots}
PROF="$TASK_TMP/profile"
PORT=${BROWSER_PORT:-8776}
DBGP=${BROWSER_DEBUG_PORT:-19233}
HS=$(sh "$REPO/ui-harness/find_browser.sh")
PY=${PYTHON:-python3}
mkdir -p "$ROOT" "$SHOT" "$PROF"
export LEGAL_AGENT_ROOT="$ROOT" LEGAL_AGENT_FAKE_MODEL=1
cd "$REPO"
"$PY" ui/ui.py --host 127.0.0.1 --port "$PORT" --agent-root "$ROOT" > "$TASK_TMP/server.log" 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true; wait "$SRV" 2>/dev/null || true; rm -rf "$TASK_TMP"' EXIT INT TERM
for i in $(seq 1 40); do
  if ! kill -0 "$SRV" 2>/dev/null; then echo 'Owned test server exited'; tail -20 "$TASK_TMP/server.log"; exit 3; fi
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/matters"; then break; fi
  sleep 0.5
done
if ! curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/matters"; then
  echo 'SERVER FAILED'; tail -20 "$TASK_TMP/server.log"; exit 3
fi
node "$REPO/ui-harness/matter_validate.mjs" "http://127.0.0.1:$PORT" "$DBGP" "$SHOT" "$HS" "$PROF" "$ROOT"
