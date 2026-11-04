# Local settings and execution protections

The Settings page manages the model key on the local server. It does not enable a sandbox or connect a database. A configured key means local configuration exists, not that provider authentication, billing, or a model request has succeeded.

## Credential contract

The password field is write-only and is cleared on submission and navigation. The client stores no key in browser storage, URLs, conversation, or workspace state. The server returns configuration presence and provider hostname only. Changes require a loopback Host, matching Origin, JSON content type and a server-issued CSRF token. Synthetic demo roots cannot save credentials. Running investigations prevent changes.

The server saves the key into the existing local `agent/.env` configuration atomically with owner-only permissions, preserving unrelated configuration. This is local plaintext protected by filesystem permissions, **not an encrypted vault**. The process and other programs running as the same account can access it. The accepted token alphabet excludes shell syntax; direct CLI use of the helper can still load shell configuration. Tests must use temporary fake keys; never replace the user's actual key during validation.

UI-launched subprocesses use `allowlist-v1`: only required OS/locale/certificate settings, known application limits, model connection values, and CourtListener credentials are passed. Unknown `.env` assignments, ambient cloud/provider credentials, shell startup hooks and Python injection variables are omitted. The saved model key is authoritative: removing it cannot fall back to an inherited key or key-file setting. Every UI launch explicitly supplies the data root, case path and interpreter so the helper skips shell-sourcing `.env`. Direct CLI invocations are outside this UI contract.

This reduces accidental credential inheritance; it does not isolate files, network, host executables or same-account processes. Workers can still read files their user can access, including credential files. The model and optional CourtListener credentials remain available to the trusted local worker; separating those from untrusted tools requires a broker and enforced runtime restrictions.

Settings reports these capabilities from the backend, including inactive OS isolation, disconnected external databases, absent external telemetry export, and the limits of manual continuation. Cooperating CLI runners now hold a workspace-wide OS file lock throughout execution. A restarted UI can detect a surviving runner and block duplicate launches, credential changes, and document mutations. Saved running checkpoints with no owner show an explicit **Recover saved run** action. Recovery takes the same lock and changes the saved state to paused without model calls; the operator must inspect activity and records before choosing Resume. A run with no conversation checkpoint requires a new investigation. On macOS and Linux, process exit releases ownership automatically; this implementation uses `fcntl` and does not support Windows. Recovery does not reattach console output, undo tool effects, or guarantee exactly-once execution. Direct Python callers and external helper mutations are outside the ownership contract. The infrastructure [selection document](agentic-infrastructure-landscape.md) records the adoption gates.

The current UI is a local operator console, not an authenticated multi-user deployment. Do not expose its listener publicly. Credential protection needs an authentication/session design and HTTPS before remote hosting. Same-origin checks protect against unrelated browser origins, not compromised same-origin JavaScript or other local processes.

## Open-source isolation candidates

Source review on 2026-11-03; neither candidate is integrated or installed by this change.

| Candidate | Fit and integration work |
| --- | --- |
| [Anthropic sandbox-runtime](https://github.com/anthropics/sandbox-runtime) | A local CLI/library for filesystem and network restrictions. Its documented platform mechanisms include macOS `sandbox-exec` and Linux bubblewrap. A suitable first experiment for existing subprocess jobs, with an explicit restrictive configuration. Defaults include unrestricted reads, so merely wrapping a command is insufficient for legal records. |
| [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | A managed sandbox/gateway design with policy and provider credential controls. A stronger candidate for isolated deployed workers, with additional runtime and operations requirements. Upstream documents Docker, Podman, or host virtualization prerequisites. |

The current machine exposes `sandbox-exec`; availability alone proves no policy is active. The app still launches local processes. Settings must report this honestly and must not offer an “enabled” switch until enforcement is real.

## Integration boundary to implement and verify

Wrap the common job-launch path and every other tool subprocess that can access user data. Separate trusted document ingestion from agent-controlled work. Mount only an immutable copy of the authorized matter and a writable output directory; deny unrelated matters, home-directory secrets, configuration files, arbitrary network destinations and control sockets. Never pass the model key into an arbitrary tool process. Route model traffic through a trusted broker with an allowlisted provider destination and request limits.

Fail closed if the runtime or policy cannot load; never retry outside isolation. Test allowed evidence reads and output writes, denied reads of sibling matters and a fake secret, denied host writes, network egress, subprocess escape, timeout/cancellation and policy failures. Record the policy version and verified runtime in the run receipt. Report active isolation from that receipt rather than a user preference.

SQL querying has a separate database permission boundary. See [Auto Ontology assessment](auto-ontology-assessment.md) for an optional matter-scoped read-only projection and guarded query execution. OS isolation does not grant permission to query unrelated matters or establish legal correctness.

## Durable chat delivery

At each execution slice, queued directions and their IDs are saved together in the conversation checkpoint before their case-record delivery receipt is written. A crash before the checkpoint leaves the direction pending; a crash after it preserves the text and prevents duplicate insertion when the receipt is retried. This does not consume a model turn or apply a directive. Chat directions still arrive at the next slice boundary, rather than interrupting a tool in flight.
