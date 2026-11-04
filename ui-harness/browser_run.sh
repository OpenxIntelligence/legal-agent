#!/bin/sh
# Real-browser validation runner: isolated synthetic server + CDP driver.
# From the repo root: sh ui-harness/browser_run.sh
# Env overrides: BROWSER_PORT, BROWSER_DEBUG_PORT, BROWSER_SHELL, BROWSER_SHOTS.
# Needs a loopback listener and an unsandboxed browser launch (see README.md).
REPO=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/legal-agent-browser.XXXXXX")
ROOT="$TMP/root"
SHOT=${BROWSER_SHOTS:-/tmp/browser-shots}
PROF="$TMP/profile"
PORT=${BROWSER_PORT:-8765}
DBGP=${BROWSER_DEBUG_PORT:-19222}
HS=$(sh "$REPO/ui-harness/find_browser.sh")
mkdir -p "$ROOT" "$SHOT" "$PROF"
export LEGAL_AGENT_ROOT="$ROOT" LEGAL_AGENT_FAKE_MODEL=1
cd "$REPO" || exit 2
"${PYTHON:-$REPO/agent/.venv/bin/python}" ui/ui.py --host 127.0.0.1 --port "$PORT" --agent-root "$ROOT" > "$TMP/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; wait $SRV 2>/dev/null; rm -rf "$TMP"' EXIT INT TERM
for i in $(seq 1 40); do
  curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/gallery" && break
  sleep 0.5
done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/gallery" || { echo "SERVER FAILED"; tail -20 "$TMP/server.log"; exit 3; }
echo "server up on $PORT (pid $SRV), root $ROOT"
node "$REPO/ui-harness/browser_validate.mjs" "http://127.0.0.1:$PORT" "$DBGP" "$SHOT" "$HS" "$PROF"
RC=$?
echo "driver exit=$RC"
kill $SRV 2>/dev/null
wait $SRV 2>/dev/null
echo "shots:"; ls "$SHOT"
exit $RC
