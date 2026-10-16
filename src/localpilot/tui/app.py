"""Full-screen Textual TUI dashboard implementing Sections 7 & 14."""

from __future__ import annotations
import asyncio
from typing import Any, List, Optional
import psutil
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Rule,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from localpilot.cli.formatting import HOME_ASCII_BANNER
from localpilot.domain.models import TaskCategory
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.activity import ActivityService
from localpilot.sources.catalog_service import CatalogService, RankedCandidate
from localpilot.storage.db import get_db
from localpilot.storage.inventory import StorageManager


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
    ]

    def __init__(self) -> None:
        super().__init__()
        self.inspector = SystemInspector()
        self.cat_svc = CatalogService()
        self.storage_mgr = StorageManager()
        self.activity_svc = ActivityService()
        self._ranked_candidates: List[RankedCandidate] = []
        self._search_timer: Optional[Timer] = None
        self._pending_query: str = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="home-tab", id="main-tabs"):
            with TabPane("Home", id="home-tab"):
                with VerticalScroll():
                    yield Static(HOME_ASCII_BANNER, id="ascii-banner")
                    with Container(id="welcome-box"):
                        yield Label("[bold cyan]LOCAL AI, MADE SIMPLE[/bold cyan]")
                        yield Label("Discover, run, monitor, and update local AI models safely.")
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
        self._refresh_discover_table()

    def _refresh_discover_table(self, query: str = "") -> None:
        table = self.query_one("#discover-table", DataTable)
        table.clear()
        self._ranked_candidates = self.cat_svc.discover(query=query)
        for r in self._ranked_candidates:
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
        db = get_db()
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()
        for r in rows:
            table.add_row(r["name"], r["runtime_name"], r["readiness_stage"], r["installation_id"], key=r["installation_id"])

    def _setup_running_table(self) -> None:
        table = self.query_one("#running-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model ID", "PID", "Status", "Endpoint", "Instance ID")
        self._reconcile_and_populate_running(table)

    def _reconcile_and_populate_running(self, table: DataTable) -> None:
        """Reconciles database records against live OS processes."""
        table.clear()
        db = get_db()
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM running_instances").fetchall()

        active_count = 0
        for r in rows:
            pid = r["pid"]
            endpoint = r["endpoint_url"]
            instance_id = r["instance_id"]
            model_id = r["model_id"]

            is_alive = False
            status_text = "Unknown"

            if pid is not None:
                if psutil.pid_exists(pid):
                    is_alive = True
                    status_text = "Running (Active)"
                else:
                    status_text = "Stopped (Stale)"
            elif endpoint:
                # API service without host PID
                is_alive = True
                status_text = "Running (Service)"

            if is_alive:
                active_count += 1

            table.add_row(
                model_id,
                str(pid) if pid is not None else "Service",
                status_text,
                endpoint or "N/A",
                instance_id,
                key=instance_id,
            )

        self.query_one("#home-running-status", Label).update(
            f"{active_count} active instance(s)"
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
                self.notify(f"Selected: {item.candidate.name}. Run 'localpilot setup {item.candidate.candidate_id}' in terminal.", title="Setup Model")

    def action_stop_action(self) -> None:
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "running-tab":
            table = self.query_one("#running-table", DataTable)
            self.notify("To safely stop a model instance, use: localpilot stop <instance-id>", title="Stop Model")

    def action_remove_action(self) -> None:
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "installed-tab":
            self.notify("To safely remove an installed model, use: localpilot remove <installation-id>", title="Remove Model")

    def action_show_help(self) -> None:
        self.notify("Keys: D (Discover), I (Installed), R (Running), S (Storage), A (Activity), Q (Quit)")
