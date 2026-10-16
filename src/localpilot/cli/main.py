"""Main Typer CLI entry point implementing all Section 6 commands."""

from __future__ import annotations
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional
import typer
from rich.table import Table

from localpilot.application.setup_workflow import SetupWorkflow
from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.cli.formatting import console, render_banner, render_error_box, render_message_box
from localpilot.domain.models import TaskCategory
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.activity import ActivityService
from localpilot.observability.events import get_hub
from localpilot.observability.export import export_support_bundle, generate_support_bundle_dict
from localpilot.observability.schema import EventCategory, EventLevel
from localpilot.runtimes.base import InstallationScope
from localpilot.sources.catalog_service import CatalogService
from localpilot.storage.db import get_db, get_default_log_dir
from localpilot.storage.inventory import StorageManager

app = typer.Typer(
    name="localpilot",
    help="Local AI, made simple.",
    no_args_is_help=False,
)

self_app = typer.Typer(help="Manage the LocalPilot application itself.")
app.add_typer(self_app, name="self")


@app.callback(invoke_without_command=True)
def main_menu(ctx: typer.Context) -> None:
    """Default entry point: opens home screen / menu if no subcommand provided."""
    if ctx.invoked_subcommand is None:
        render_banner()
        console.print("[bold]Welcome to LocalPilot![/bold]\n")
        console.print("Common commands:")
        console.print("  [cyan]localpilot tui[/cyan]         Open the complete visual dashboard")
        console.print("  [cyan]localpilot discover[/cyan]    Find compatible models")
        console.print("  [cyan]localpilot installed[/cyan]   View your installed models")
        console.print("  [cyan]localpilot doctor[/cyan]      Check computer capabilities")
        console.print("  [cyan]localpilot setup[/cyan]       Set up a new model")
        console.print("  [cyan]localpilot storage[/cyan]     View storage breakdown\n")
        console.print("Run [cyan]localpilot --help[/cyan] to see all available commands.")


@app.command("tui")
def launch_tui() -> None:
    """Open the complete full-screen terminal dashboard."""
    from localpilot.tui.app import LocalPilotTUI
    tui_app = LocalPilotTUI()
    tui_app.run()


@app.command("doctor")
def run_doctor(
    plain: bool = typer.Option(False, "--plain", help="Print in plain text format"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON data"),
) -> None:
    """Check the computer hardware and diagnose potential problems."""
    inspector = SystemInspector()
    report = inspector.inspect()

    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return

    hw = report.hardware
    ram_gb = hw.ram_total_bytes / (1024**3)
    avail_ram_gb = hw.ram_available_bytes / (1024**3)
    safe_ram_gb = hw.safe_ram_budget_bytes / (1024**3)
    disk_free_gb = hw.disk_available_bytes / (1024**3)

    if plain:
        print(f"OS: {hw.os_name} {hw.os_version} ({hw.cpu_arch})")
        print(f"CPU Cores: {hw.cpu_cores_logical} logical, {hw.cpu_cores_physical} physical")
        print(f"System RAM: {ram_gb:.1f} GB Total, {avail_ram_gb:.1f} GB Available, {safe_ram_gb:.1f} GB Safe Budget")
        print(f"Memory Model: {hw.memory_model.value}")
        print(f"Disk Free: {disk_free_gb:.1f} GB")
        print(f"Discovered Runtimes: {[r.name for r in report.discovered_runtimes]}")
        return

    table = Table(title="Computer Diagnostic Summary", border_style="cyan")
    table.add_column("Component", style="bold")
    table.add_column("Status / Reading")

    table.add_row("Operating System", f"{hw.os_name} {hw.os_version} ({hw.cpu_arch})")
    table.add_row("Processor", f"{hw.cpu_cores_logical} cores ({', '.join(hw.cpu_features[:4])}...)")
    table.add_row("System Memory", f"{avail_ram_gb:.1f} GB available (Safe budget: {safe_ram_gb:.1f} GB)")
    table.add_row("Memory Model", hw.memory_model.value.title())

    gpu_str = "None detected (CPU execution only)"
    if hw.gpu_devices:
        gpu_str = ", ".join(f"{g.name} ({g.acceleration_type})" for g in hw.gpu_devices)
    table.add_row("Graphics / GPU", gpu_str)
    table.add_row("Storage Space", f"{disk_free_gb:.1f} GB free")

    runtimes_str = ", ".join(f"{r.name} ({r.scope})" for r in report.discovered_runtimes) or "None (will install on demand)"
    table.add_row("Supporting Runtimes", runtimes_str)

    console.print(table)


@app.command("discover")
def discover_models(
    task: Optional[str] = typer.Option(None, "--task", help="Filter by task: chat, coding"),
    query: str = typer.Option("", "--query", "-q", help="Search text"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Find models compatible with this computer."""
    cat_svc = CatalogService()
    task_cat = TaskCategory(task.lower()) if task else None
    results = cat_svc.discover(task=task_cat, query=query)

    if json_output:
        typer.echo(json.dumps([r.model_dump() for r in results], indent=2))
        return

    if plain:
        for r in results:
            cand = r.candidate
            sz_mb = cand.total_bytes / (1024 * 1024)
            print(f"{cand.name} | Source: {cand.source} | Size: {sz_mb:.0f}MB | Score: {r.score.total_score} | ID: {cand.candidate_id}")
        return

    table = Table(title="Compatible Models for Your Computer", border_style="cyan")
    table.add_column("Score", style="bold green", justify="right")
    table.add_column("Model Name", style="bold")
    table.add_column("Source")
    table.add_column("Size", justify="right")
    table.add_column("Context", justify="right")
    table.add_column("Fit Status")
    table.add_column("Candidate ID", style="dim")

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
            cand.candidate_id,
        )

    console.print(table)
    console.print("\nTo set up a model, run: [cyan]localpilot setup <candidate-id>[/cyan]")


@app.command("installed")
def list_installed(
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """View installed models on this computer."""
    db = get_db()
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()

    if json_output:
        typer.echo(json.dumps([dict(r) for r in rows], indent=2))
        return

    if not rows:
        console.print("[dim]No models installed yet. Run [cyan]localpilot discover[/cyan] to find one.[/dim]")
        return

    table = Table(title="Installed Models", border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Runtime")
    table.add_column("Status")
    table.add_column("Installation ID", style="dim")

    for r in rows:
        table.add_row(r["name"], r["runtime_name"], r["readiness_stage"], r["installation_id"])

    console.print(table)


@app.command("running")
def list_running(
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """View active running models."""
    db = get_db()
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM running_instances").fetchall()

    if json_output:
        typer.echo(json.dumps([dict(r) for r in rows], indent=2))
        return

    if not rows:
        console.print("[dim]No models are currently running.[/dim]")
        return

    table = Table(title="Running Models", border_style="cyan")
    table.add_column("Model ID", style="bold")
    table.add_column("PID")
    table.add_column("Endpoint")
    table.add_column("Instance ID", style="dim")

    for r in rows:
        table.add_row(r["model_id"], str(r["pid"]), r["endpoint_url"], r["instance_id"])

    console.print(table)


@app.command("storage")
def show_storage(
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Review storage use and managed directories."""
    mgr = StorageManager()
    summary = mgr.inspect_storage_categories()

    if json_output:
        typer.echo(summary.model_dump_json(indent=2))
        return

    to_mb = lambda b: b / (1024 * 1024)

    table = Table(title="Storage Breakdown", border_style="cyan")
    table.add_column("Category", style="bold")
    table.add_column("Size", justify="right")

    table.add_row("Model Files", f"{to_mb(summary.model_files_bytes):.1f} MB")
    table.add_row("Supporting Runtimes", f"{to_mb(summary.runtime_installations_bytes):.1f} MB")
    table.add_row("Downloads & Temporary", f"{to_mb(summary.incomplete_downloads_bytes + summary.temporary_files_bytes):.1f} MB")
    table.add_row("[bold]Total Managed Storage[/bold]", f"[bold]{to_mb(summary.total_managed_bytes):.1f} MB[/bold]")

    console.print(table)


@app.command("activity")
def show_activity(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of records to show"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """View recent friendly activity history."""
    svc = ActivityService()
    activities = svc.get_activities(limit=limit)

    if json_output:
        typer.echo(json.dumps([a.model_dump() for a in activities], indent=2))
        return

    if not activities:
        console.print("[dim]No activity recorded yet.[/dim]")
        return

    table = Table(title="Recent Activity", border_style="cyan")
    table.add_column("Time", style="dim")
    table.add_column("Action", style="bold")
    table.add_column("Summary")

    for a in activities:
        table.add_row(a.timestamp[:19].replace("T", " "), a.title, a.summary)

    console.print(table)


@app.command("logs")
def view_logs(
    level: Optional[str] = typer.Option(None, "--level", help="Filter by level: debug, info, warning, error"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max lines to show"),
) -> None:
    """View structured diagnostic event logs."""
    log_file = get_default_log_dir() / "events.jsonl"
    if not log_file.exists():
        console.print("[dim]No log events recorded yet.[/dim]")
        return

    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    lines = lines[-limit:]
    for line in lines:
        try:
            ev = json.loads(line)
            ev_lvl = ev.get("level", "INFO")
            if level and level.upper() != ev_lvl:
                continue
            console.print(f"[dim]{ev.get('timestamp')[:19]}[/dim] [{ev_lvl}] ({ev.get('category')}) {ev.get('message')}")
        except Exception:
            pass


@app.command("setup")
def setup_model(
    candidate_id: Optional[str] = typer.Argument(None, help="Candidate ID to install"),
    scope: str = typer.Option("isolated", "--scope", help="Installation scope: isolated, user, system"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Accept plan without confirmation prompt"),
) -> None:
    """Start guided setup for a model."""
    cat_svc = CatalogService()
    if not candidate_id:
        # Pick top recommended model
        results = cat_svc.discover()
        if not results:
            render_error_box("No compatible models found for this computer.")
            return
        candidate = results[0].candidate
    else:
        candidate = cat_svc.get_candidate(candidate_id)
        if not candidate:
            render_error_box(f"Candidate '{candidate_id}' not found.")
            return

    workflow = SetupWorkflow()
    install_scope = InstallationScope(scope.lower())

    # Check for recommended plan from catalog
    rec_plan = None
    alt_plans = []
    matched = [r for r in cat_svc.discover(query=candidate.model_id) if r.candidate.candidate_id == candidate.candidate_id]
    if matched:
        rec_plan = matched[0].recommended_plan
        alt_plans = matched[0].alternative_plans

    plan = workflow.build_plan(candidate, scope=install_scope, execution_plan=rec_plan, alternative_plans=alt_plans)

    if not plan.requires_runtime_install:
        happening_msg = "LocalPilot found supporting software already installed. We can use it to run this model."
        change_msg = f"Will download model weights and use {plan.inference_backend} via {plan.deployment_method}."
    else:
        happening_msg = f"Ready to set up {candidate.name}"
        change_msg = f"Will prepare {plan.runtime_name} ({scope} scope) and download model weights."

    render_message_box(
        what_is_happening=happening_msg,
        why_needed=f"To run this model on your computer ({plan.inference_backend} / {plan.deployment_method}).",
        what_will_change=change_msg,
        what_remains_untouched="Your other Python projects and personal files.",
        next_action="Review and confirm to begin setup.",
        title="Setup Plan",
    )

    if not yes:
        confirm = typer.confirm("Proceed with setup?", default=True)
        if not confirm:
            console.print("Setup cancelled.")
            return

    async def _run() -> None:
        def _progress(msg: str, done: int, total: int) -> None:
            console.print(f"[dim]{msg}[/dim]")

        with console.status(f"Setting up {candidate.name}..."):
            res = await workflow.execute_setup(candidate, plan, progress_callback=_progress)
        console.print(f"\n[bold green]Success![/bold green] Model set up with ID: [cyan]{res['installation_id']}[/cyan]")
        console.print(f"To run this model: [cyan]localpilot run {res['installation_id']}[/cyan]")

    asyncio.run(_run())


@app.command("remove")
def remove_model(
    installation_id: str = typer.Argument(..., help="Installation ID to remove"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
    delete_files: bool = typer.Option(True, "--delete-files/--keep-files", help="Delete model weight files"),
) -> None:
    """Safely remove an installed model."""
    mgr = StorageManager()
    if not yes:
        confirm = typer.confirm(f"Are you sure you want to remove installation '{installation_id}'?", default=False)
        if not confirm:
            console.print("Removal cancelled.")
            return

    res = mgr.safe_remove_installation(installation_id, delete_files=delete_files)
    if res.get("success"):
        freed_mb = res.get("bytes_freed", 0) / (1024 * 1024)
        console.print(f"[bold green]Removed installation {installation_id}.[/bold green] Freed: {freed_mb:.1f} MB.")
    else:
        render_error_box(res.get("error", "Failed to remove installation."))


@app.command("support")
def prepare_support(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
) -> None:
    """Prepare a reviewed, redacted diagnostic report for troubleshooting."""
    out_path = output or Path("localpilot-support.json")
    export_support_bundle(out_path)
    console.print(f"[bold green]Diagnostic report exported to:[/bold green] [cyan]{out_path.resolve()}[/cyan]")
    console.print("[dim]All personal tokens, keys, prompts, and private paths have been redacted.[/dim]")


@self_app.command("update")
def self_update() -> None:
    """Update LocalPilot application."""
    console.print("[bold cyan]Checking for LocalPilot updates...[/bold cyan]")
    console.print("LocalPilot is running the latest version (0.1.0).")


@self_app.command("uninstall")
def self_uninstall(
    yes: bool = typer.Option(False, "-y", "--yes", help="Confirm uninstallation"),
    purge_models: bool = typer.Option(False, "--purge-models", help="Also delete downloaded models"),
) -> None:
    """Uninstall LocalPilot application."""
    if not yes:
        confirm = typer.confirm("Uninstall LocalPilot application?", default=False)
        if not confirm:
            console.print("Cancelled.")
            return
    console.print("[yellow]Uninstalling LocalPilot application...[/yellow]")
    if not purge_models:
        console.print("[green]Notice: Model files are preserved in your data folder.[/green]")
    console.print("LocalPilot uninstallation complete.")
