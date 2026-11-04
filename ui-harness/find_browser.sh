#!/bin/sh
# Find a local Chromium executable without a machine-specific path.
set -eu
if [ -n "${BROWSER_SHELL:-}" ]; then
  if [ -x "$BROWSER_SHELL" ]; then printf '%s\n' "$BROWSER_SHELL"; exit 0; fi
  echo 'BROWSER_SHELL must name an executable Chromium binary.' >&2
  exit 2
fi
for command_name in chromium chromium-browser google-chrome; do
  if command -v "$command_name" >/dev/null 2>&1; then command -v "$command_name"; exit 0; fi
done
for browser_path in \
  "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell \
  "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell \
  "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell \
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'; do
  if [ -x "$browser_path" ]; then printf '%s\n' "$browser_path"; exit 0; fi
done
echo 'Chromium was not found. Set BROWSER_SHELL to its executable path.' >&2
exit 2
