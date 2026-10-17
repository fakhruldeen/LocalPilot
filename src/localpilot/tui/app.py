"""Full-screen Textual TUI dashboard implementing Sections 7 & 14."""

from __future__ import annotations
import asyncio
from typing import Any, List, Optional
import psutil
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    Rule,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import ApprovalRecord, SetupWorkflow
from localpilot.cli.formatting import HOME_ASCII_BANNER
from localpilot.domain.documentation import DOCUMENTATION_TOPICS
from localpilot.domain.models import TaskCategory
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.activity import ActivityService
from localpilot.sources.catalog_service import CatalogService, RankedCandidate
from localpilot.storage.db import get_db
from localpilot.storage.inventory import StorageManager


class HelpModal(ModalScreen[None]):
    """Comprehensive documentation modal for beginners and technical users."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
        ("?", "dismiss", "Close"),
        ("h", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Label("[bold cyan]LocalPilot Help & Documentation Manual[/bold cyan]", id="help-title")
            with TabbedContent(initial="help-novice"):
                with TabPane("Beginner Guide", id="help-novice"):
                    with VerticalScroll(id="help-scroll-novice"):
                        yield Markdown(DOCUMENTATION_TOPICS["novice"]["content"])
                with TabPane("Technical Architecture", id="help-technical"):
                    with VerticalScroll(id="help-scroll-tech"):
                        yield Markdown(DOCUMENTATION_TOPICS["technical"]["content"])
                with TabPane("FAQ & Troubleshooting", id="help-faq"):
                    with VerticalScroll(id="help-scroll-faq"):
                        yield Markdown(DOCUMENTATION_TOPICS["faq"]["content"])
                with TabPane("Commands & Keys", id="help-cmd"):
                    with VerticalScroll(id="help-scroll-cmd"):
                        yield Markdown(DOCUMENTATION_TOPICS["commands"]["content"])
            with Horizontal(id="help-buttons"):
                yield Button("Close (Esc)", variant="primary", id="close-help-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help-btn":
            self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Modal dialog for confirming an action."""

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._dialog_title = title
        self._dialog_message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(f"[bold]{self._dialog_title}[/bold]", id="confirm-title")
            yield Label(self._dialog_message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Confirm", variant="primary", id="confirm-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)


class LocalPilotTUI(App):
    """LocalPilot Full-Screen Terminal User Interface."""

    CSS = """
    Screen {
        background: #121212;
        color: #e0e0e0;
    }
    #ascii-banner {
        color: #00e5ff;
        text-align: center;
        margin-bottom: 1;
    }
    #welcome-box {
        border: solid #00e5ff;
        padding: 1 2;
        margin-bottom: 1;
    }
    .panel {
        border: solid #333333;
        padding: 1;
        margin: 1;
    }
    .status-text {
        color: #00e676;
    }
    DataTable {
        height: 14;
        margin: 1 0;
    }
    #explanation-box {
        border: solid #555555;
        padding: 1;
        height: 5;
        margin-top: 1;
    }
    ConfirmModal {
        align: center middle;
    }
    #confirm-dialog {
        padding: 1 2;
        width: 60;
        height: auto;
        border: solid #00e5ff;
        background: #1e1e1e;
    }
    #confirm-title {
        margin-bottom: 1;
        color: #00e5ff;
    }
    #confirm-message {
        margin-bottom: 1;
    }
    #confirm-buttons {
        align: right middle;
        height: 3;
    }
    #confirm-buttons Button {
        margin-left: 1;
    }
    HelpModal {
        align: center middle;
    }
    #help-dialog {
        padding: 1 2;
        width: 90%;
        height: 88%;
        border: solid #00e5ff;
        background: #1a1a1a;
    }
    #help-title {
        text-align: center;
        margin-bottom: 1;
        color: #00e5ff;
    }
    #help-scroll-novice, #help-scroll-tech, #help-scroll-faq, #help-scroll-cmd {
        height: 1fr;
        padding: 1;
    }
    #help-buttons {
        align: right middle;
        height: 3;
        margin-top: 1;
    }
    #help-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Exit"),
        ("d", "switch_to_discover", "Discover"),
        ("i", "switch_to_installed", "Installed"),
        ("r", "switch_to_running", "Running"),
        ("s", "switch_to_storage", "Storage"),
        ("a", "switch_to_activity", "Activity"),
        ("enter", "select_action", "Action"),
        ("x", "stop_action", "Stop"),
        ("delete", "remove_action", "Remove"),
        ("?", "show_help", "Help"),
        ("h", "show_help", "Guide"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.inspector = SystemInspector()
        self.cat_svc = CatalogService()
        self.storage_mgr = StorageManager()
        self.activity_svc = ActivityService()
        self.runtime_svc = RuntimeLifecycleService()
        self._ranked_candidates: List[RankedCandidate] = []
        self._search_timer: Optional[Timer] = None
        self._pending_query: str = ""
        self._search_seq: int = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="home-tab", id="main-tabs"):
            with TabPane("Home", id="home-tab"):
                with VerticalScroll():
                    yield Static(HOME_ASCII_BANNER, id="ascii-banner")
                    with Container(id="welcome-box"):
                        yield Label("[bold cyan]LOCAL AI, MADE SIMPLE[/bold cyan]")
                        yield Label("Discover, run, monitor, and update local AI models safely.")
                        yield Label("[dim]Press [bold cyan]?[/bold cyan] or [bold cyan]H[/bold cyan] anytime for complete beginner & technical documentation.[/dim]")
                    with Horizontal():
                        with Vertical(classes="panel"):
                            yield Label("[bold]Computer Health[/bold]")
                            yield Label("Checking...", id="home-hw-status")
                        with Vertical(classes="panel"):
                            yield Label("[bold]Active Models[/bold]")
                            yield Label("Checking...", id="home-running-status")
                        with Vertical(classes="panel"):
                            yield Label("[bold]Storage Managed[/bold]")
                            yield Label("Checking...", id="home-storage-status")

            with TabPane("Find a Model", id="discover-tab"):
                with Vertical():
                    with Horizontal():
                        yield Input(placeholder="Search models (e.g. coding, llama, 3b)...", id="search-input")
                    yield DataTable(id="discover-table")
                    with Vertical(id="explanation-box"):
                        yield Label("[bold]Why this model?[/bold]")
                        yield Label("Select a model above to see why it matches your computer. Press Enter to Setup.", id="explanation-text")

            with TabPane("Installed Models", id="installed-tab"):
                with Vertical():
                    yield DataTable(id="installed-table")
                    yield Label("[dim]Press Delete to remove an installed model.[/dim]")

            with TabPane("Running Models", id="running-tab"):
                with Vertical():
                    yield DataTable(id="running-table")
                    yield Label("[dim]Press X to stop a selected active instance.[/dim]")

            with TabPane("Storage", id="storage-tab"):
                with VerticalScroll():
                    yield Label("[bold]Storage Breakdown[/bold]")
                    yield Label("", id="storage-details")

            with TabPane("Activity Log", id="activity-tab"):
                with Vertical():
                    yield DataTable(id="activity-table")

        yield Footer()

    def on_mount(self) -> None:
        self._setup_discover_table()
        self._setup_installed_table()
        self._setup_running_table()
        self._setup_activity_table()
        self._refresh_storage()
        self._load_health_background()

    @work(exclusive=True, thread=True)
    def _load_health_background(self) -> None:
        report = self.inspector.inspect()
        hw = report.hardware
        avail_gb = hw.ram_available_bytes / (1024**3)
        safe_gb = hw.safe_ram_budget_bytes / (1024**3)
        text = f"{hw.os_name} • {avail_gb:.1f}GB RAM free (Safe: {safe_gb:.1f}GB)"
        self.call_from_thread(self._update_home_health, text)

    def _update_home_health(self, text: str) -> None:
        self.query_one("#home-hw-status", Label).update(text)

    def _setup_discover_table(self) -> None:
        table = self.query_one("#discover-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Score", "Model Name", "Source", "Size", "Context", "Fit Status")
        try:
            initial_results = self.cat_svc.discover(query="")
            self._update_discover_table(initial_results, self._search_seq)
        except Exception:
            pass

    def _refresh_discover_table(self, query: str = "") -> None:
        self._search_seq += 1
        self._run_discover_worker(query, self._search_seq)

    @work(exclusive=True, thread=True)
    def _run_discover_worker(self, query: str, seq: int) -> None:
        results = self.cat_svc.discover(query=query)
        self.call_from_thread(self._update_discover_table, results, seq)

    def _update_discover_table(self, results: List[RankedCandidate], seq: int) -> None:
        if seq < self._search_seq:
            return  # Discard out-of-order search results
        self._ranked_candidates = results
        table = self.query_one("#discover-table", DataTable)
        table.clear()
        for r in results:
            cand = r.candidate
            sz_mb = cand.total_bytes / (1024 * 1024)
            table.add_row(
                f"{r.score.total_score:.0f}",
                cand.name,
                cand.source.title(),
                f"{sz_mb:.0f} MB",
                str(cand.usable_context),
                r.compatibility.friendly_explanation,
                key=cand.candidate_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "discover-table":
            row_idx = event.cursor_row
            if 0 <= row_idx < len(self._ranked_candidates):
                item = self._ranked_candidates[row_idx]
                expl_label = self.query_one("#explanation-text", Label)
                plan_info = ""
                if item.recommended_plan:
                    rec = item.recommended_plan
                    plan_info = f" | Recommended: {rec.inference_backend.value} ({rec.deployment_method.value})"
                    if item.alternative_plans:
                        plan_info += f" | {len(item.alternative_plans)} other option(s) supported"
                expl_label.update(
                    f"{item.score.explanation}{plan_info}\n[dim]Recommendation confidence: {item.candidate.confidence.value} • Press Enter to Setup[/dim]"
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            # Debounce search input by 350ms to keep UI responsive
            self._pending_query = event.value
            if self._search_timer:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.35, self._debounced_search)

    def _debounced_search(self) -> None:
        self._refresh_discover_table(query=self._pending_query)

    def _setup_installed_table(self) -> None:
        table = self.query_one("#installed-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model Name", "Runtime", "Stage", "Installation ID")
        self._refresh_installed_table()

    def _refresh_installed_table(self) -> None:
        table = self.query_one("#installed-table", DataTable)
        table.clear()
        conn = get_db().get_connection()
        rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()
        for r in rows:
            table.add_row(r["name"], r["runtime_name"], r["readiness_stage"], r["installation_id"], key=r["installation_id"])

    def _setup_running_table(self) -> None:
        table = self.query_one("#running-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model ID", "PID", "Status", "Endpoint", "Instance ID")
        self._reconcile_and_populate_running(table)

    def _reconcile_and_populate_running(self, table: Optional[DataTable] = None) -> None:
        """Reconciles database records against live OS processes using RuntimeLifecycleService."""
        if table is None:
            table = self.query_one("#running-table", DataTable)
        table.clear()
        active = self.runtime_svc.reconcile_running_instances()

        for r in active:
            health = r.get("health_status", "healthy")
            if health == "healthy":
                status_label = "Running (Verified)"
            elif health == "degraded":
                status_label = "Running (Degraded)"
            elif health == "unresponsive":
                status_label = "Running (Unresponsive)"
            else:
                status_label = f"Running ({health})"

            table.add_row(
                r.get("model_id", ""),
                str(r.get("pid", "Service")),
                status_label,
                r.get("endpoint_url", "N/A"),
                r.get("instance_id", ""),
                key=r.get("instance_id", ""),
            )

        self.query_one("#home-running-status", Label).update(
            f"{len(active)} active instance(s)"
        )

    def _setup_activity_table(self) -> None:
        table = self.query_one("#activity-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Time", "Title", "Summary")
        activities = self.activity_svc.get_activities(limit=30)
        for a in activities:
            table.add_row(a.timestamp[:19].replace("T", " "), a.title, a.summary)

    def _refresh_storage(self) -> None:
        summary = self.storage_mgr.inspect_storage_categories()
        to_mb = lambda b: b / (1024 * 1024)
        text = (
            f"• Model Weights: {to_mb(summary.model_files_bytes):.1f} MB\n"
            f"• Supporting Runtimes: {to_mb(summary.runtime_installations_bytes):.1f} MB\n"
            f"• Downloads & Temp: {to_mb(summary.incomplete_downloads_bytes + summary.temporary_files_bytes):.1f} MB\n"
            f"• Total Managed Storage: {to_mb(summary.total_managed_bytes):.1f} MB"
        )
        self.query_one("#storage-details", Label).update(text)

    def action_switch_to_discover(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "discover-tab"

    def action_switch_to_installed(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "installed-tab"

    def action_switch_to_running(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "running-tab"
        table = self.query_one("#running-table", DataTable)
        self._reconcile_and_populate_running(table)

    def action_switch_to_storage(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "storage-tab"
        self._refresh_storage()

    def action_switch_to_activity(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "activity-tab"

    def action_select_action(self) -> None:
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "discover-tab":
            table = self.query_one("#discover-table", DataTable)
            row_idx = table.cursor_row
            if 0 <= row_idx < len(self._ranked_candidates):
                item = self._ranked_candidates[row_idx]
                workflow = SetupWorkflow()
                built_plan = workflow.build_plan(
                    item.candidate,
                    execution_plan=item.recommended_plan,
                    alternative_plans=item.alternative_plans,
                )

                def _on_confirm(confirmed: bool) -> None:
                    if confirmed:
                        approval = ApprovalRecord.create_for_plan(built_plan, approved_by="user_tui")
                        self.notify(f"Starting setup for {item.candidate.name}...", title="Setup Started")
                        self._run_setup_worker(item.candidate, built_plan, approval)

                msg = (
                    f"Set up {item.candidate.name}?\n\n"
                    f"• Backend: {built_plan.inference_backend} ({built_plan.runtime_name})\n"
                    f"• Download: ~{built_plan.download_bytes / (1024*1024):.0f} MB\n"
                    f"• Storage: {built_plan.target_install_dir}\n"
                    f"• Execution Mode: {built_plan.execution_mode}"
                )
                self.push_screen(
                    ConfirmModal("Approve Model Setup", msg),
                    _on_confirm,
                )

        elif current_tab == "installed-tab":
            table = self.query_one("#installed-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No installed model selected.", title="Start Model")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            installation_id = str(row_key.value) if row_key else ""
            if installation_id:
                self.notify(f"Starting model {installation_id}...", title="Starting")
                self._run_start_worker(installation_id)

    @work(exclusive=True, thread=True)
    def _run_setup_worker(self, candidate, plan, approval) -> None:
        workflow = SetupWorkflow()
        try:
            res = asyncio.run(workflow.execute_setup(candidate, plan, approval=approval))
            self.call_from_thread(self._on_setup_completed, res["installation_id"], candidate.name)
        except Exception as e:
            self.call_from_thread(self.notify, f"Setup failed: {e}", title="Error", severity="error")

    def _on_setup_completed(self, inst_id: str, name: str) -> None:
        self.notify(f"Successfully set up {name} (ID: {inst_id})", title="Setup Finished")
        self._refresh_installed_table()
        self._refresh_storage()

    @work(exclusive=True, thread=True)
    def _run_start_worker(self, installation_id: str) -> None:
        try:
            res = asyncio.run(self.runtime_svc.start_installed_model(installation_id))
            self.call_from_thread(self._on_start_completed, res)
        except Exception as e:
            self.call_from_thread(self.notify, f"Start failed: {e}", title="Error", severity="error")

    def _on_start_completed(self, res: dict) -> None:
        inst_id = res.get("instance_id", "")
        ep = res.get("endpoint_url", "")
        self.notify(f"Model running at {ep} (ID: {inst_id})", title="Started")
        self.action_switch_to_running()

    def action_stop_action(self) -> None:
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "running-tab":
            table = self.query_one("#running-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No running instance selected.", title="Stop Instance")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            instance_id = str(row_key.value) if row_key else ""
            if not instance_id:
                return

            def _on_confirm(confirmed: bool) -> None:
                if confirmed:
                    self.notify(f"Stopping instance {instance_id}...", title="Stopping")
                    self._run_stop_worker(instance_id)

            self.push_screen(
                ConfirmModal("Stop Instance", f"Stop running model instance {instance_id}?"),
                _on_confirm,
            )

    @work(exclusive=True, thread=True)
    def _run_stop_worker(self, instance_id: str) -> None:
        try:
            res = asyncio.run(self.runtime_svc.stop_running_instance(instance_id))
            self.call_from_thread(self._on_stop_completed, instance_id, res)
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to stop: {e}", title="Error", severity="error")

    def _on_stop_completed(self, instance_id: str, res: Any) -> None:
        succeeded = getattr(res, "request_succeeded", bool(res))
        outcome = getattr(res, "observed_outcome", None)
        outcome_val = getattr(outcome, "value", str(outcome)) if outcome else ""
        if succeeded:
            self.notify(f"Instance {instance_id} stopped ({outcome_val or 'success'}).", title="Stopped")
        else:
            reason = getattr(res, "reason", "Stop failed")
            self.notify(f"Instance {instance_id}: {reason}", title="Stop Failed", severity="warning")
        self._reconcile_and_populate_running()

    def action_remove_action(self) -> None:
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "installed-tab":
            table = self.query_one("#installed-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No installation selected.", title="Remove Model")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            installation_id = str(row_key.value) if row_key else ""
            if not installation_id:
                return

            def _on_confirm(confirmed: bool) -> None:
                if confirmed:
                    self.notify(f"Removing {installation_id}...", title="Removing")
                    self._run_remove_worker(installation_id)

            self.push_screen(
                ConfirmModal("Remove Model", f"Safely remove installation {installation_id} and free disk space?"),
                _on_confirm,
            )

    @work(exclusive=True, thread=True)
    def _run_remove_worker(self, installation_id: str) -> None:
        try:
            res = self.storage_mgr.safe_remove_installation(installation_id)
            self.call_from_thread(self._on_remove_completed, installation_id, res)
        except Exception as e:
            self.call_from_thread(self.notify, f"Removal failed: {e}", title="Error", severity="error")

    def _on_remove_completed(self, installation_id: str, res: dict) -> None:
        if res.get("success"):
            freed_mb = res.get("bytes_freed", 0) / (1024 * 1024)
            self.notify(f"Removed {installation_id}. Freed: {freed_mb:.1f} MB.", title="Removed")
        else:
            self.notify(res.get("error", "Removal failed."), title="Error", severity="error")
        self._refresh_installed_table()
        self._refresh_storage()

    def action_show_help(self) -> None:
        self.push_screen(HelpModal())
