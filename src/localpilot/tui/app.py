"""Full-screen Textual TUI dashboard implementing Sections 7 & 14."""

from __future__ import annotations
from typing import List, Optional
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
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
        margin: 1 2;
    }
    .panel {
        border: round #333333;
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
        ("?", "show_help", "Help"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.inspector = SystemInspector()
        self.cat_svc = CatalogService()
        self.storage_mgr = StorageManager()
        self.activity_svc = ActivityService()
        self._ranked_candidates: List[RankedCandidate] = []

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
                        yield Label("Select a model above to see why it matches your computer.", id="explanation-text")

            with TabPane("Installed Models", id="installed-tab"):
                with Vertical():
                    yield DataTable(id="installed-table")

            with TabPane("Running Models", id="running-tab"):
                with Vertical():
                    yield DataTable(id="running-table")

            with TabPane("Storage", id="storage-tab"):
                with VerticalScroll():
                    yield Label("[bold cyan]Managed Storage Breakdown[/bold cyan]", classes="panel")
                    yield Label("Loading storage details...", id="storage-details")

            with TabPane("Activity History", id="activity-tab"):
                with Vertical():
                    yield DataTable(id="activity-table")

        yield Footer()

    def on_mount(self) -> None:
        self._refresh_home()
        self._setup_discover_table()
        self._setup_installed_table()
        self._setup_running_table()
        self._setup_activity_table()
        self._refresh_storage()

    def _refresh_home(self) -> None:
        report = self.inspector.inspect()
        hw = report.hardware
        ram_gb = hw.ram_available_bytes / (1024**3)
        self.query_one("#home-hw-status", Label).update(
            f"[green]Ready[/green]: {hw.os_name} {hw.cpu_arch}, {ram_gb:.1f}GB RAM safe"
        )

        db = get_db()
        conn = db.get_connection()
        running_cnt = conn.execute("SELECT COUNT(*) as c FROM running_instances").fetchone()["c"]
        self.query_one("#home-running-status", Label).update(
            f"{running_cnt} active instance(s)"
        )

        storage = self.storage_mgr.inspect_storage_categories()
        total_mb = storage.total_managed_bytes / (1024 * 1024)
        self.query_one("#home-storage-status", Label).update(
            f"{total_mb:.1f} MB in application folder"
        )

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
                    f"{item.score.explanation}{plan_info}\n[dim]Recommendation confidence: {item.candidate.confidence.value}[/dim]"
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._refresh_discover_table(query=event.value)

    def _setup_installed_table(self) -> None:
        table = self.query_one("#installed-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model Name", "Runtime", "Status", "Installation ID")
        db = get_db()
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()
        for r in rows:
            table.add_row(r["name"], r["runtime_name"], r["readiness_stage"], r["installation_id"])

    def _setup_running_table(self) -> None:
        table = self.query_one("#running-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model ID", "PID", "Endpoint", "Instance ID")
        db = get_db()
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM running_instances").fetchall()
        for r in rows:
            table.add_row(r["model_id"], str(r["pid"]), r["endpoint_url"], r["instance_id"])

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

    def action_switch_to_storage(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "storage-tab"

    def action_switch_to_activity(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "activity-tab"

    def action_show_help(self) -> None:
        self.notify("Press D (Discover), I (Installed), R (Running), S (Storage), A (Activity), Q (Quit)")
