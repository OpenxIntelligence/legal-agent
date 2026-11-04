# Legal Agent workspace

Run `./legal-agent --demo` from the repository root for an isolated, persistent synthetic workspace. Use `./legal-agent --live` for the locally configured investigation console.

`ui.py` serves the HTTP API and static assets on loopback. `index.html` implements navigation and work-product interactions; `theme.css` defines the product appearance and responsive layout. The existing vendor assets stay local.

The default home screen introduces citation audits and chronologies, links to saved work, and shows whether model calls are disabled. Work products, investigation, and evaluation have dedicated navigation. Artifact routes preserve deep links and existing review operations.

For HTTP workflow checks use `ui-harness/journey.py`; for real Chromium interaction checks use `ui-harness/browser_run.sh`. Both should use isolated fictional workspaces. Never point a test harness at a live matter directory.
