# Provenance and licensing status

Legal Agent was developed from Meta’s [legal-agent-workshop](https://github.com/meta-models/legal-agent-workshop). The local workspace, product UI, matter persistence, review workflows, infrastructure controls, and related tests extend that source. The Meta Model API remains supported through the OpenAI Python SDK.

This attribution describes origin; it does not imply endorsement by Meta or transfer rights in upstream material.

## Project license

No root upstream `LICENSE` file was identified in the inherited source available in this checkout. This repository does not invent a license for that material or imply a new blanket license grant. Attribution and public availability alone do not establish permission to reuse or redistribute every component. The applicable upstream terms and rights must be established before offering this project under a project-wide license.

## Third-party components

Bundled UI dependency notices and license texts are retained in [`ui/vendor/licenses/`](ui/vendor/licenses/). [`ui/vendor/SOURCES.md`](ui/vendor/SOURCES.md) records bundle sources, and [`ui/vendor/SHA256SUMS`](ui/vendor/SHA256SUMS) records checksums. Those component-specific terms remain applicable; they do not license unrelated project code.

Python dependencies are declared in [`agent/requirements.txt`](agent/requirements.txt). Their own distributions and terms govern them separately. Infrastructure assessments in `docs/` describe possible integrations, not bundled implementations or additional grants of rights.

Fictional case fixtures are supplied for demonstrations and testing. They are not records of actual client matters or legal advice.
