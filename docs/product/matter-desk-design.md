# Matter workspace

One workspace per investigation, with a saved-investigation switcher and the
actual selected run. Conversation and steering remain visible beside findings
on desktop, like an IDE. At narrow widths both sections stay on the page with
jump links; changing focus never hides work. On desktop the copilot sits at the far left, output in the center, and documents
and source inspection in a third column. Compact navigation and the status
dashboard sit above the work area. Narrow screens stack these sections.
Preparation notes are folded away behind disclosures, with documents first.

The conversation composer stays accessible while its transcript scrolls on
desktop, including short laptop viewports. Enter submits; Shift+Enter inserts a
new line. In live mode, Send persists the message and starts or resumes an
investigation. Running slices accept queued directions; an unanswered blocking
request still requires an explicit answer. Model dispatch failures remain visible
without discarding saved messages. Demo mode explicitly saves only. Preparation
checklists stay in the context inspector rather than posing as chat responses.

Documents can be uploaded from the inspector after intake. Uploads preserve
existing records, reject duplicate names and excessive batches, and expose
partial failures with retry guidance. Active runs block uploads. New records
require a new investigation; earlier findings do not silently change.

 Findings retain source links and access to the existing editable,
versioned evidence-review work product. An inline editor explicitly creates or
reuses that work product and saves individual claim revisions with API readback.
Unsent claim edits survive normal polling in page memory; approval and checks
remain explicit. Run history and earlier-run steering
restrictions remain intact. Source inspection returns focus to its originating
finding or document control.

Unsent text and request/run provenance stay per investigation in the browser tab,
survive reload and review navigation, and are never sent automatically. A request
replacement or historical-run selection cannot silently reassign an answer.
Successful save clears only the exact submitted text, preserving newer edits.
Unavailable browser storage falls back to page memory and says so.

The synthetic demo cannot answer real legal questions. Its limitation appears
before intake, and its submit action says Save demo intake. The live launcher
is ./legal-agent --live; provider configuration and an explicit Send or Start are required. Demo data stays separate. No credential values are shown or committed.

Development uses lawyer, client, and reviewer perspectives in critique,
implementation, and browser review loops. Browser dispatch race checks mock
provider execution; they are not live-model or legal-answer validation.

Validation uses real Chromium interaction and persistence checks, including native
Enter/Shift+Enter, short-window composer visibility, provider dispatch errors,
and document upload/source inspection. Offline matter tests cover upload
preservation, failure recovery, isolation, and upload/start concurrency. Browser
model dispatch checks intercept provider execution. Key presence does not prove
provider authentication or legal-answer quality.

This revision passed 23 Chromium acceptance groups, 36 offline matter tests,
browser draft-storage checks, syntax checks, and diff whitespace validation.
Desktop, short-laptop, source, and mobile editor screenshots were inspected.
