"""Phase B console surface: scoped source access and gallery semantics.

Direct Console-method tests over a demo matter in a fixture
workshop (no subprocess, no credentials), plus static checks of
the gallery markup's keyboard/assistive-technology semantics.
A live screen-reader or keyboard-only pass was not performed;
that gap is recorded, not claimed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_kit import artifacts
from tests import support

WORKSHOP = Path(__file__).resolve().parents[2]
INDEX = (WORKSHOP / "ui" / "index.html").read_text()


class GalleryConsoleCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workshop = Path(tmp.name)
        self.agent = self.workshop / "agent"
        self.agent.mkdir(parents=True)
        (self.agent / "helper").write_text("")
        self.run_id = artifacts.build_demo(self.agent)
        ui = support.console_ui()
        self.console = ui.Console(None)
        self.console.set_workshop(str(self.workshop))


class ScopeTest(GalleryConsoleCase):
    def test_source_returns_transcript_with_fidelity_note(self) -> None:
        found = self.console.artifact_source(
            self.run_id, "A1", "DEMO-STATUS", "1"
        )
        self.assertEqual("DEMO-STATUS", found["document_id"])
        self.assertIn("Severity 1", found["text"])
        self.assertEqual("extraction", found["fidelity"])
        self.assertTrue(found["fidelity_note"])
        self.assertTrue(found["image"])
        self.assertEqual(12, len(found["sha12"]))

    def test_revoked_source_refused(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self.console.artifact_source(self.run_id, "A1", "DEMO-DRAFT", "1")
        self.assertIn("revoked", str(raised.exception))

    def test_unknown_instance_and_cross_matter_refused(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.console.artifact_source(self.run_id, "A99", "DEMO-STATUS", "1")
        with self.assertRaises(FileNotFoundError):
            self.console.artifact_source(self.run_id, "../../x", "DEMO-STATUS", "1")

    def test_unknown_doc_page_and_traversal_refused(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.console.artifact_source(self.run_id, "A1", "DOC-NOPE", "1")
        with self.assertRaises(FileNotFoundError):
            self.console.artifact_source(self.run_id, "A1", "DEMO-STATUS", "9")
        with self.assertRaises(FileNotFoundError):
            self.console.artifact_source(self.run_id, "A1", "DEMO-STATUS", "x")
        with self.assertRaises(FileNotFoundError):
            self.console.artifact_source(self.run_id, "A1", "../secret", "1")

    def test_scoped_page_serves_bytes_but_refuses_revoked(self) -> None:
        image = self.console.artifact_page(self.run_id, "A1", "DEMO-STATUS", "1")
        self.assertTrue(image.startswith(bytes.fromhex("89504e47")))
        with self.assertRaises(RuntimeError):
            self.console.artifact_page(self.run_id, "A1", "DEMO-DRAFT", "1")

    def test_artifact_list_shows_demo_instances(self) -> None:
        listed = self.console.artifact_list(self.run_id)
        by_id = {row["id"]: row for row in listed["instances"]}
        self.assertEqual(
            {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13", "A14", "A15", "A16"},
            set(by_id),
        )
        self.assertEqual(9, by_id["A1"]["rows"])
        self.assertEqual(4, by_id["A2"]["rows"])
        self.assertEqual(3, by_id["A3"]["rows"])
        self.assertEqual(3, by_id["A4"]["rows"])
        self.assertEqual(3, by_id["A5"]["rows"])
        self.assertEqual(3, by_id["A6"]["rows"])
        self.assertEqual(3, by_id["A7"]["rows"])
        self.assertEqual(3, by_id["A8"]["rows"])
        self.assertEqual(3, by_id["A9"]["rows"])
        self.assertEqual(3, by_id["A10"]["rows"])
        self.assertEqual(3, by_id["A11"]["rows"])
        self.assertEqual(3, by_id["A12"]["rows"])
        self.assertEqual(3, by_id["A13"]["rows"])
        self.assertEqual(3, by_id["A14"]["rows"])
        self.assertEqual(3, by_id["A15"]["rows"])
        self.assertEqual(3, by_id["A16"]["rows"])


class HtmlSemanticsTest(unittest.TestCase):
    def test_gallery_reachable_from_header(self) -> None:
        self.assertIn('id="galleryBtn"', INDEX)
        self.assertIn("#/gallery", INDEX)

    def test_views_are_labelled_landmarks(self) -> None:
        self.assertIn('<section id="galleryView" hidden aria-label="Work products">', INDEX)
        self.assertIn('<section id="artifactView" hidden aria-label="Audit workspace">', INDEX)

    def test_audit_table_has_scoped_headers(self) -> None:
        for header in ("Row", "Assertion", "Checks / state"):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_status_changes_are_announced(self) -> None:
        self.assertIn('role="status" aria-live="polite"', INDEX)

    def test_correction_and_review_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Assertion"',
            'aria-label="Citation document"',
            'aria-label="Citation page"',
            'aria-label="Citation quotation"',
            'aria-label="Feedback"',
            'aria-label="Review notes"',
            'aria-label="Qualifications"',
            'aria-label="Older version"',
            'aria-label="Newer version"',
            'aria-label="Export version"',
            'aria-label="Event label"',
            'aria-label="Event date"',
            'aria-label="Event owner"',
            'aria-label="Event uncertainty"',
            'aria-label="New event label"',
            'aria-label="New event date"',
            'aria-label="New event issues"',
            'aria-label="New event facts"',
            'aria-label="New event uncertainty"',
            'aria-label="Link with event"',
            'aria-label="Link kind"',
            'aria-label="Link reason"',
        ):
            self.assertIn(label, INDEX)

    def test_row_selection_and_images_have_names(self) -> None:
        self.assertIn("aria-label=\"Inspect row", INDEX)
        self.assertIn("aria-label=\"Inspect event", INDEX)
        self.assertIn('alt="Scanned page image"', INDEX)

    def test_board_table_has_scoped_headers(self) -> None:
        for header in ("Event", "Date", "Label", "Issues", "Sources", "Owner", "State"):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_candidate_confirm_inputs_are_labelled(self) -> None:
        self.assertIn('aria-label="Confirm date for ', INDEX)
        self.assertIn('aria-label="Confirm label for ', INDEX)

    def test_view_changes_move_focus(self) -> None:
        self.assertIn('tabindex="-1"', INDEX)
        self.assertIn('.focus({preventScroll: true})', INDEX)

    def test_matrix_table_has_scoped_headers(self) -> None:
        for header in (
            "Cell", "Issue × regime", "Proposition", "Authority",
            "Branch", "Review", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_matrix_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect cell',
            'aria-label="Cell proposition"',
            'aria-label="Authority citation"',
            'aria-label="Effective date"',
            'aria-label="Last checked"',
            'aria-label="Cell caveat"',
            'aria-label="Linked branch"',
            'aria-label="Cell review status"',
            'aria-label="Review note"',
            'aria-label="Retract reason"',
            'aria-label="New cell issue"',
            'aria-label="New cell regime"',
            'aria-label="New cell layer"',
        ):
            self.assertIn(label, INDEX)

    def test_matrix_unknowns_stay_explicit(self) -> None:
        self.assertIn("provisional survey", INDEX)
        self.assertIn("date unknown", INDEX)
        self.assertIn("jurisdiction-matrix", INDEX)

    def test_contract_table_has_scoped_headers(self) -> None:
        for header in (
            "Clause", "Passage", "Effect (reviewer)", "Risk", "Review", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_contract_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect clause',
            'aria-label="Clause heading"',
            'aria-label="Clause effect"',
            'aria-label="Clause risk"',
            'aria-label="Clause risk note"',
            'aria-label="Clause review status"',
            'aria-label="Clause review note"',
            'aria-label="Clause retract reason"',
            'aria-label="New clause document"',
            'aria-label="New clause page"',
            'aria-label="New clause heading"',
            'aria-label="New clause quotation"',
        ):
            self.assertIn(label, INDEX)

    def test_contract_judgment_stays_labelled(self) -> None:
        self.assertIn("contract-review", INDEX)
        self.assertIn("reviewer judgment", INDEX)
        self.assertIn("Exceptions", INDEX)

    def test_playbook_tables_have_scoped_headers(self) -> None:
        for header in (
            "Rule", "Topic", "Position", "Severity", "State",
            "Item", "Clause × rule", "Verdict", "Rationale",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_playbook_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect rule',
            'aria-label="Inspect deviation',
            'aria-label="Rule topic"',
            'aria-label="Rule position"',
            'aria-label="Rule severity"',
            'aria-label="Rule retract reason"',
            'aria-label="New rule topic"',
            'aria-label="New rule position"',
            'aria-label="New rule severity"',
            'aria-label="Assess clause"',
            'aria-label="Assess rule"',
            'aria-label="Assess verdict"',
            'aria-label="Assess rationale"',
            'aria-label="Deviation verdict"',
            'aria-label="Deviation rationale"',
        ):
            self.assertIn(label, INDEX)

    def test_playbook_queue_stays_explicit(self) -> None:
        self.assertIn("playbook-deviation", INDEX)
        self.assertIn("Exception queue", INDEX)
        self.assertIn("Queue clear", INDEX)

    def test_calendar_table_has_scoped_headers(self) -> None:
        for header in (
            "Due", "Obligation", "Kind", "Clause", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_calendar_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect obligation',
            'aria-label="Obligation title"',
            'aria-label="Obligation kind"',
            'aria-label="Obligation due date"',
            'aria-label="Obligation notice days"',
            'aria-label="Obligation notes"',
            'aria-label="Obligation status"',
            'aria-label="Obligation resolution reason"',
            'aria-label="New obligation clause"',
            'aria-label="New obligation kind"',
            'aria-label="New obligation title"',
            'aria-label="New obligation due date"',
            'aria-label="New obligation notice days"',
            'aria-label="New obligation notes"',
        ):
            self.assertIn(label, INDEX)

    def test_calendar_as_of_stays_explicit(self) -> None:
        self.assertIn("obligation-calendar", INDEX)
        self.assertIn("As of", INDEX)
        self.assertIn("stated as-of date", INDEX)

    def test_intake_table_has_scoped_headers(self) -> None:
        for header in (
            "Request", "Title", "Category", "Urgency", "Status", "Owner", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_intake_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect request',
            'aria-label="Request title"',
            'aria-label="Request description"',
            'aria-label="Request category"',
            'aria-label="Request urgency"',
            'aria-label="Request requester"',
            'aria-label="Request owner"',
            'aria-label="Request issue"',
            'aria-label="Triage owner"',
            'aria-label="Route destination"',
            'aria-label="Route rationale"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New request title"',
            'aria-label="New request description"',
            'aria-label="New request category"',
            'aria-label="New request urgency"',
            'aria-label="New request requester"',
            'aria-label="New request issue"',
        ):
            self.assertIn(label, INDEX)

    def test_intake_queue_stays_explicit(self) -> None:
        self.assertIn("legal-intake", INDEX)
        self.assertIn("awaiting routing", INDEX)
        self.assertIn("one step back", INDEX)

    def test_deadlines_table_has_scoped_headers(self) -> None:
        for header in (
            "Due", "Deadline", "Kind", "Owner", "Tracks", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_deadlines_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect deadline',
            'aria-label="Deadline title"',
            'aria-label="Deadline kind"',
            'aria-label="Deadline owner"',
            'aria-label="Deadline due date"',
            'aria-label="Deadline obligation"',
            'aria-label="Deadline notes"',
            'aria-label="Deadline status"',
            'aria-label="Deadline resolution reason"',
            'aria-label="New deadline title"',
            'aria-label="New deadline kind"',
            'aria-label="New deadline owner"',
            'aria-label="New deadline due date"',
            'aria-label="New deadline obligation"',
            'aria-label="New deadline notes"',
        ):
            self.assertIn(label, INDEX)

    def test_deadlines_as_of_stays_explicit(self) -> None:
        self.assertIn("matter-deadlines", INDEX)
        self.assertIn("As of", INDEX)
        self.assertIn("unowned", INDEX)

    def test_discovery_table_has_scoped_headers(self) -> None:
        for header in (
            "Request", "Title", "Direction", "Category", "Due", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_discovery_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect request',
            'aria-label="Request title"',
            'aria-label="Request description"',
            'aria-label="Request direction"',
            'aria-label="Request category"',
            'aria-label="Request due date"',
            'aria-label="Request counterpart"',
            'aria-label="Response received date"',
            'aria-label="Response documents"',
            'aria-label="Response summary"',
            'aria-label="Response notes"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New request title"',
            'aria-label="New request description"',
            'aria-label="New request direction"',
            'aria-label="New request category"',
            'aria-label="New request due date"',
            'aria-label="New request counterpart"',
        ):
            self.assertIn(label, INDEX)

    def test_discovery_queue_stays_explicit(self) -> None:
        self.assertIn("discovery-log", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("next:", INDEX)

    def test_horizon_table_has_scoped_headers(self) -> None:
        for header in (
            "Watch", "Title", "Regime", "Observed", "Effective", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_horizon_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect watch',
            'aria-label="Watch title"',
            'aria-label="Watch summary"',
            'aria-label="Watch regime"',
            'aria-label="Watch source"',
            'aria-label="Watch observed date"',
            'aria-label="Watch effective date"',
            'aria-label="Watch last checked"',
            'aria-label="Watch assignee"',
            'aria-label="Watch cell link"',
            'aria-label="Watch relevance"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New watch title"',
            'aria-label="New watch summary"',
            'aria-label="New watch regime"',
            'aria-label="New watch source"',
            'aria-label="New watch observed date"',
            'aria-label="New watch effective date"',
            'aria-label="New watch assignee"',
            'aria-label="New watch cell link"',
            'aria-label="New watch relevance"',
        ):
            self.assertIn(label, INDEX)

    def test_horizon_limits_stay_explicit(self) -> None:
        self.assertIn("regulatory-horizon", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("never live monitoring", INDEX)

    def test_governance_table_has_scoped_headers(self) -> None:
        for header in (
            "Case", "Title", "System", "Risk", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_governance_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect use case',
            'aria-label="Use case title"',
            'aria-label="Use case purpose"',
            'aria-label="Use case data"',
            'aria-label="Use case system"',
            'aria-label="Use case risk"',
            'aria-label="Use case owner"',
            'aria-label="Use case conditions"',
            'aria-label="Use case intake link"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New use case title"',
            'aria-label="New use case purpose"',
            'aria-label="New use case data"',
            'aria-label="New use case system"',
            'aria-label="New use case owner"',
            'aria-label="New use case intake link"',
        ):
            self.assertIn(label, INDEX)

    def test_governance_limits_stay_explicit(self) -> None:
        self.assertIn("ai-governance", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("never measured evaluations", INDEX)

    def test_entity_tables_have_scoped_headers(self) -> None:
        for header in (
            "Entity", "Name", "Regime", "Identifier", "Filings",
            "Filing", "Title", "Kind", "Due", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_entity_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect entity',
            'aria-label="Inspect filing',
            'aria-label="Entity name"',
            'aria-label="Entity regime"',
            'aria-label="Entity identifier"',
            'aria-label="Filing entity"',
            'aria-label="Filing kind"',
            'aria-label="Filing title"',
            'aria-label="Filing due date"',
            'aria-label="Filing period"',
            'aria-label="Filing notes"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New entity name"',
            'aria-label="New entity regime"',
            'aria-label="New entity identifier"',
            'aria-label="New filing entity"',
            'aria-label="New filing kind"',
            'aria-label="New filing title"',
            'aria-label="New filing due date"',
            'aria-label="New filing period"',
            'aria-label="New filing notes"',
        ):
            self.assertIn(label, INDEX)

    def test_entity_limits_stay_explicit(self) -> None:
        self.assertIn("entity-compliance", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("stated as-of date", INDEX)

    def test_termsheet_tables_have_scoped_headers(self) -> None:
        for header in (
            "Sheet", "Label", "Party", "Date",
            "Term", "Verdict", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_termsheet_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect sheet',
            'aria-label="Inspect term',
            'aria-label="Sheet label"',
            'aria-label="Sheet party"',
            'aria-label="Sheet date"',
            'aria-label="Sheet notes"',
            'aria-label="Term topic"',
            'aria-label="Term proposition"',
            'aria-label="Position sheet"',
            'aria-label="Position text"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New sheet label"',
            'aria-label="New sheet party"',
            'aria-label="New sheet date"',
            'aria-label="New sheet notes"',
            'aria-label="New term topic"',
            'aria-label="New term proposition"',
        ):
            self.assertIn(label, INDEX)

    def test_termsheet_limits_stay_explicit(self) -> None:
        self.assertIn("term-sheet", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("never counts as agreement", INDEX)

    def test_patent_tables_have_scoped_headers(self) -> None:
        for header in (
            "Subject", "Label", "Kind", "Reference",
            "Element", "Verdict", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_patent_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect subject',
            'aria-label="Inspect element',
            'aria-label="Subject label"',
            'aria-label="Subject kind"',
            'aria-label="Subject reference"',
            'aria-label="Subject notes"',
            'aria-label="Element claim reference"',
            'aria-label="Element text"',
            'aria-label="Reading subject"',
            'aria-label="Reading text"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New subject label"',
            'aria-label="New subject kind"',
            'aria-label="New subject reference"',
            'aria-label="New subject notes"',
            'aria-label="New element claim reference"',
            'aria-label="New element text"',
        ):
            self.assertIn(label, INDEX)

    def test_patent_limits_stay_explicit(self) -> None:
        self.assertIn("patent-claims", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("never counts as mapped", INDEX)
        self.assertIn("not infringement or validity opinions", INDEX)

    def test_qa_table_has_scoped_headers(self) -> None:
        for header in (
            "Q", "Question", "Asker", "Due", "Status", "State",
        ):
            self.assertIn(f"<th scope=\"col\">{header}</th>", INDEX)

    def test_qa_inputs_are_labelled(self) -> None:
        for label in (
            'aria-label="Inspect question',
            'aria-label="Question text"',
            'aria-label="Question asker"',
            'aria-label="Question topic"',
            'aria-label="Question assignee"',
            'aria-label="Question due date"',
            'aria-label="Answer text"',
            'aria-label="Support documents"',
            'aria-label="Decide status"',
            'aria-label="Decision reason"',
            'aria-label="New question text"',
            'aria-label="New question asker"',
            'aria-label="New question topic"',
            'aria-label="New question assignee"',
            'aria-label="New question due date"',
        ):
            self.assertIn(label, INDEX)

    def test_qa_limits_stay_explicit(self) -> None:
        self.assertIn("sellside-qa", INDEX)
        self.assertIn("awaiting action", INDEX)
        self.assertIn("stated as-of date", INDEX)

    def test_queue_shortcuts_name_their_targets(self) -> None:
        for label in (
            'aria-label="Inspect clause ',
            'aria-label="Inspect deviation ',
            'aria-label="Inspect request ',
            'aria-label="Inspect watch ',
            'aria-label="Inspect use case ',
            'aria-label="Inspect filing ',
            'aria-label="Inspect term ',
            'aria-label="Inspect element ',
            'aria-label="Inspect question ',
            'aria-label="Inspect invoice ',
            'aria-label="Inspect source ',
            'aria-label="Run"',
        ):
            self.assertIn(label, INDEX)

    def test_examples_panel_is_labelled_and_escaped(self) -> None:
        self.assertIn("function examplesHtml(inst)", INDEX)
        self.assertIn("${examplesHtml(inst)}", INDEX)
        self.assertIn("<h2>Regression examples</h2>", INDEX)
        for label in (
            'aria-label="Example id"',
            'aria-label="Example role"',
            'aria-label="Promoting actor"',
            'aria-label="Original failure"',
            'aria-label="Expected behavior"',
            'aria-label="Defect class for ',
            'aria-label="Replay example ',
        ):
            self.assertIn(label, INDEX)
        for escaped in (
            "${esc(txt(e.id))}",
            "${esc(txt(e.role))}",
            "${esc(short(txt(e.failure), 140))}",
            "${esc(txt(r.row_id))}",
        ):
            self.assertIn(escaped, INDEX)


class VerbTableDriftTest(unittest.TestCase):
    def test_console_keys_match_cli_options(self) -> None:
        import importlib.util

        cli_path = (
            Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"
        )
        spec = importlib.util.spec_from_file_location("artifact_cli", cli_path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        ui = support.console_ui()
        self.assertEqual(set(cli._VERB_OPTIONS), set(ui.Console._ARTIFACT_OPS))
        for verb, (_, _, keys, extras) in ui.Console._ARTIFACT_OPS.items():
            opts, flags, dests = cli._VERB_OPTIONS[verb]
            parser_keys = {name for name, _ in opts} | set(flags)
            for key in keys:
                with self.subTest(verb=verb, key=key):
                    self.assertIn(key, parser_keys)
            for extra in extras:
                with self.subTest(verb=verb, extra=extra):
                    if extra == "with":
                        self.assertIn("with", dests)
                    else:
                        self.assertIn(extra, flags)


class SubmitContractTest(unittest.TestCase):
    def test_submit_bodies_match_console_contract(self) -> None:
        import re

        ui = support.console_ui()
        table = ui.Console._ARTIFACT_OPS
        fns = re.findall(
            r"async function (submit\w+Op)\(op[^)]*\) \{(.*?)\n\}",
            INDEX,
            re.S,
        )
        self.assertEqual(15, len(fns))
        for name, body in fns:
            with self.subTest(fn=name):
                verbs = set(re.findall(r"\$\{gal\.base\}/(\w+)", body))
                self.assertEqual(1, len(verbs))
                verb = verbs.pop()
                self.assertIn(verb, table)
                _, ops, keys, extras = table[verb]
                lits = set(re.findall(r'op === "([^"]+)"', body))
                say = re.search(r"galSay\(\{(.*?)\}\[op\]", body, re.S)
                if say:
                    lits |= set(re.findall(r'"?([\w-]+)"?: "', say.group(1)))
                allowed = set(ops)
                if 'body.op = "' in body:
                    allowed |= lits - set(ops)
                for op in lits:
                    self.assertIn(op, allowed)
                sent = set(re.findall(r"body\.(\w+) =", body))
                sent.discard("op")
                for key in sent:
                    self.assertIn(key, set(keys) | set(extras))


if __name__ == "__main__":
    unittest.main()
