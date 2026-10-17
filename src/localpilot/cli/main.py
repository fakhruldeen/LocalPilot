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

from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import ApprovalRecord, SetupWorkflow
from localpilot.application.update_workflow import UpdateWorkflow
from localpilot.cli.formatting import (
    console,
    format_json_error,
    render_app_error_box,
    render_banner,
    render_error_box,
    render_message_box,
)
from localpilot.domain.errors import (
    AppError,
    ErrorCode,
    OutcomeCertainty,
    RecoveryAction,
    create_app_error,
    map_exception_to_app_error,
)
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
    help="""Local AI, Made Simple.

Discover, install, run, monitor, update, and safely manage local AI models on your own computer.
Private, offline-first, hardware-aware, and novice-friendly.

Run 'localpilot guide' for step-by-step documentation for beginners and technical users.
Run 'localpilot tui' to launch the visual full-screen dashboard.""",
    no_args_is_help=False,
)

self_app = typer.Typer(help="Manage the LocalPilot application itself.")
app.add_typer(self_app, name="self")


def _val(arg: Any, default: Any = None) -> Any:
    """Unwraps Typer OptionInfo/ArgumentInfo default objects when commands are invoked directly in Python."""
    if hasattr(arg, "default"):
        return arg.default if arg.default is not ... else default
    return arg


def _do_doctor(plain: bool = False, json_output: bool = False) -> None:
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))
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
        print(f"System RAM: {ram_gb:.1f} GB Total, {avail_ram_gb:.1f} GB Available, {safe_ram_gb:.1f} GB Safe Budget (reserves RAM for OS & context)")
        print(f"Memory Model: {hw.memory_model.value}")
        print(f"Disk Free: {disk_free_gb:.1f} GB")
        print(f"Discovered Runtimes: {', '.join(r.name for r in report.discovered_runtimes) or 'None'}")
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


def _do_discover(
    task: Optional[str] = None,
    query: str = "",
    plain: bool = False,
    json_output: bool = False,
) -> None:
    task_val = _val(task, None)
    query_val = str(_val(query, ""))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    cat_svc = CatalogService()
    task_cat = TaskCategory(task_val.lower()) if task_val and isinstance(task_val, str) else None
    results = cat_svc.discover(task=task_cat, query=query_val)

    if not results and not json_val:
        console.print("[bold yellow]No models in the catalog fit within your computer's safe memory budget (RAM limit exceeded).[/bold yellow]")
        return

    if json_val:
        typer.echo(json.dumps([r.model_dump() for r in results], indent=2))
        return

    if plain_val:
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


def _do_installed(plain: bool = False, json_output: bool = False) -> None:
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))
    db = get_db()
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()

    if json_val:
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


def _do_running(plain: bool = False, json_output: bool = False) -> None:
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))
    svc = RuntimeLifecycleService()
    rows = svc.reconcile_running_instances()

    if json_val:
        typer.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        console.print("[dim]No models are currently running.[/dim]")
        return

    table = Table(title="Running Models (Reconciled)", border_style="cyan")
    table.add_column("Model ID", style="bold")
    table.add_column("PID")
    table.add_column("Endpoint")
    table.add_column("Instance ID", style="dim")

    for r in rows:
        table.add_row(r["model_id"], str(r["pid"]), r["endpoint_url"], r["instance_id"])

    console.print(table)


def _do_storage(plain: bool = False, json_output: bool = False) -> None:
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))
    mgr = StorageManager()
    summary = mgr.inspect_storage_categories()

    if json_val:
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


def _do_run(target: str) -> None:
    svc = RuntimeLifecycleService()
    conn = get_db().get_connection()
    inst_row = conn.execute(
        "SELECT * FROM installations WHERE installation_id = ? OR model_id = ? OR name = ? OR name LIKE ?",
        (target, target, target, f"%{target}%"),
    ).fetchone()
    installation_id = inst_row["installation_id"] if inst_row else target

    async def _async_run():
        return await svc.start_installed_model(installation_id)

    try:
        with console.status(f"Starting model instance for {inst_row['name'] if inst_row else installation_id}..."):
            res = asyncio.run(_async_run())
        console.print(f"[bold green]Model started successfully![/bold green]")
        console.print(f"Model: [cyan]{inst_row['name'] if inst_row else res['model_id']}[/cyan]")
        console.print(f"Endpoint: [cyan]{res['endpoint_url']}[/cyan] (PID: {res['pid']})")
        console.print(f"To chat: [cyan]localpilot chat {inst_row['name'] if inst_row else installation_id}[/cyan]")
        console.print(f"To stop: [cyan]localpilot stop {res['instance_id']}[/cyan]")
    except Exception as e:
        app_err = map_exception_to_app_error(e, operation="start_model", stage="starting")
        render_app_error_box(app_err)


def _do_stop(instance_id: str, force: bool = False) -> None:
    svc = RuntimeLifecycleService()

    async def _async_stop():
        return await svc.stop_running_instance(instance_id, force=force)

    try:
        res = asyncio.run(_async_stop())
        if res.request_succeeded:
            console.print(f"[bold green]Instance {instance_id} stopped ({res.observed_outcome.value}).[/bold green]")
        else:
            if getattr(res, "observed_outcome", None) and res.observed_outcome.value == "already_stopped":
                console.print(f"[yellow]Instance {instance_id} was not active or already stopped.[/yellow]")
            else:
                outcome_val = res.observed_outcome.value if getattr(res, "observed_outcome", None) else "failed"
                code = ErrorCode.STOP_UNKNOWN if outcome_val == "stop_outcome_unknown" else ErrorCode.STOP_REJECTED
                app_err = create_app_error(
                    code,
                    operation="stop_instance",
                    stage="stopping",
                    explanation=res.reason or "Failed to stop instance safely.",
                    known_impact="The process was not terminated or its outcome is unconfirmed. The instance is still listed.",
                    recommended_action=RecoveryAction.CHECK_AGAIN,
                    technical_details=f"Target: {instance_id} | Outcome: {outcome_val}",
                )
                render_app_error_box(app_err)
    except Exception as e:
        app_err = map_exception_to_app_error(e, operation="stop_instance", stage="stopping")
        render_app_error_box(app_err)


def _do_chat(target: str) -> None:
    from localpilot.application.chat_service import ChatService
    svc = RuntimeLifecycleService()
    conn = get_db().get_connection()

    run_row = conn.execute(
        "SELECT * FROM running_instances WHERE instance_id = ? OR installation_id = ? OR model_id = ?",
        (target, target, target),
    ).fetchone()

    if not run_row:
        inst_row = conn.execute(
            "SELECT * FROM installations WHERE installation_id = ? OR model_id = ? OR name = ? OR name LIKE ?",
            (target, target, target, f"%{target}%"),
        ).fetchone()
        if not inst_row:
            render_error_box(f"No running instance or installation found matching '{target}'.")
            return
        console.print(f"Model is not running yet. Starting {inst_row['name']}...")
        _do_run(inst_row["installation_id"])
        run_row = conn.execute(
            "SELECT * FROM running_instances WHERE installation_id = ?",
            (inst_row["installation_id"],),
        ).fetchone()
        if not run_row:
            render_error_box("Failed to find running instance after startup.")
            return

    endpoint = run_row["endpoint_url"]
    model_id = run_row["model_id"]
    runtime = "llamacpp"
    if run_row["installation_id"]:
        inst_info = conn.execute(
            "SELECT runtime_name FROM installations WHERE installation_id = ?",
            (run_row["installation_id"],),
        ).fetchone()
        if inst_info and inst_info["runtime_name"]:
            runtime = inst_info["runtime_name"]
    if runtime == "llamacpp" and "ollama" in (run_row["executable_path"] or "").lower():
        runtime = "ollama"

    console.print(f"[bold green]Connected to {model_id}[/bold green] at {endpoint}. Type 'exit' to quit.\n")
    messages: List[Dict[str, str]] = []

    while True:
        try:
            user_input = typer.prompt("You").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                console.print("Exiting chat.")
                break
            messages.append({"role": "user", "content": user_input})

            console.print("[bold cyan]Assistant:[/bold cyan] ", end="")
            full_reply: List[str] = []

            async def _stream():
                async for token in ChatService.stream_message(endpoint, runtime, model_id, messages):
                    console.print(token, end="")
                    full_reply.append(token)

            try:
                asyncio.run(_stream())
                console.print("\n")
                reply = "".join(full_reply)
            except Exception:
                # Fallback to send_message if streaming fails or is unsupported
                reply = asyncio.run(ChatService.send_message(endpoint, runtime, model_id, messages))
                console.print(f"{reply}\n")

            messages.append({"role": "assistant", "content": reply})
        except (KeyboardInterrupt, EOFError):
            console.print("\nChat ended.")
            break
        except Exception as e:
            app_err = map_exception_to_app_error(e, operation="chat", stage="chat_request")
            render_app_error_box(app_err)
            break


def _do_update(installation_id: str, yes: bool = False) -> None:
    workflow = UpdateWorkflow()

    try:
        with console.status("Checking for model updates..."):
            status = workflow.check_updates(installation_id)
    except Exception as e:
        app_err = map_exception_to_app_error(e, operation="update", stage="check_updates")
        render_app_error_box(app_err)
        return

    if not status.has_update:
        console.print(f"Installation {installation_id}: {status.impact_summary}")
        return

    console.print(f"[bold green]Update available![/bold green] {status.impact_summary} (target: {status.available_version or 'latest'})")
    if not yes:
        confirm = typer.confirm("Apply update now?", default=True)
        if not confirm:
            console.print("Update cancelled.")
            return

    try:
        with console.status("Applying update..."):
            res = workflow.apply_update(installation_id, target_version=status.available_version or "latest")
    except Exception as e:
        app_err = map_exception_to_app_error(e, operation="update", stage="apply_update")
        render_app_error_box(app_err)
        return

    if res.get("updated"):
        console.print(f"[bold green]Update applied successfully![/bold green]")
    else:
        status = res.get("rollback_status")
        if status == "restored":
            err = create_app_error(
                ErrorCode.UPDATE_VERIFY_FAILED,
                operation="update",
                stage="verification",
                known_impact="The update was not applied. Your previous installed version was restored safely.",
                recommended_action=RecoveryAction.USE_PREVIOUS_VERSION,
                technical_details=res.get("error", ""),
            )
        elif status == "restoration_failed":
            err = create_app_error(
                ErrorCode.RESTORATION_FAILED,
                operation="update",
                stage="restoration",
                known_impact="The previous version could not be restored completely. This model needs repair before it can run.",
                recommended_action=RecoveryAction.REVIEW_REPAIR_OPTIONS,
                technical_details=res.get("error", ""),
            )
        else:
            err = create_app_error(
                ErrorCode.UPDATE_VERIFY_FAILED,
                operation="update",
                stage="verification",
                known_impact="The update could not be verified. No changes were made to your installed model files.",
                recommended_action=RecoveryAction.DISMISS,
                technical_details=res.get("error", ""),
            )
        render_app_error_box(err)


def _do_guide(topic: Optional[str] = None) -> None:
    from rich.markdown import Markdown
    from rich.panel import Panel
    from localpilot.domain.documentation import DOCUMENTATION_TOPICS, get_documentation_markdown

    if not topic:
        console.print(Panel(
            "[bold cyan]LocalPilot: Comprehensive Guide & Documentation[/bold cyan]\n\n"
            "Learn how to use local AI safely, whether you are a beginner or a power user.\n\n"
            "[bold]Available Guides & Topics:[/bold]\n"
            "  [bold cyan]•[/bold cyan] [bold]novice[/bold]     - Step-by-step beginner walkthrough from zero to chat\n"
            "  [bold cyan]•[/bold cyan] [bold]technical[/bold]  - 3-Axis execution, memory offload math & process safety\n"
            "  [bold cyan]•[/bold cyan] [bold]faq[/bold]        - Answers to common questions for all steps\n"
            "  [bold cyan]•[/bold cyan] [bold]commands[/bold]   - Full CLI command and flag reference\n"
            "  [bold cyan]•[/bold cyan] [bold]overview[/bold]   - Privacy principles, local-only architecture\n"
            "  [bold cyan]•[/bold cyan] [bold]all[/bold]        - Read the complete documentation manual\n\n"
            "[dim]Usage: localpilot guide <topic> (e.g. localpilot guide novice)[/dim]",
            title="LocalPilot Guide",
            border_style="cyan",
        ))
        return

    md_text = get_documentation_markdown(topic)
    console.print(Markdown(md_text))


@app.callback(invoke_without_command=True)
def main_menu(ctx: typer.Context) -> None:
    """Default entry point: opens interactive home menu if no subcommand provided."""
    if ctx.invoked_subcommand is None:
        render_banner()
        if sys.stdin.isatty() and sys.stdout.isatty():
            console.print("[bold]Select an option to begin:[/bold]")
            console.print("  [bold cyan]1[/bold cyan]) Help me get started (Guided Setup Wizard)")
            console.print("  [bold cyan]2[/bold cyan]) Open Visual Dashboard (TUI)")
            console.print("  [bold cyan]3[/bold cyan]) Chat with an installed model")
            console.print("  [bold cyan]4[/bold cyan]) View installed models")
            console.print("  [bold cyan]5[/bold cyan]) Discover & browse compatible models")
            console.print("  [bold cyan]6[/bold cyan]) Run computer diagnostics (Doctor)")
            console.print("  [bold cyan]7[/bold cyan]) View storage breakdown")
            console.print("  [bold cyan]8[/bold cyan]) Read user guide & documentation")
            console.print("  [bold cyan]q[/bold cyan]) Exit\n")
            try:
                choice = typer.prompt("Enter choice", default="1").strip().lower()
                if choice in ("1", "setup", "wizard", "start"):
                    from localpilot.tui.app import LocalPilotTUI
                    LocalPilotTUI(start_wizard=True).run()
                elif choice in ("2", "tui", "dashboard"):
                    from localpilot.tui.app import LocalPilotTUI
                    LocalPilotTUI().run()
                elif choice in ("3", "chat"):
                    chat_model()
                elif choice in ("4", "installed", "models"):
                    _do_installed()
                elif choice in ("5", "discover"):
                    _do_discover()
                elif choice in ("6", "doctor"):
                    _do_doctor()
                elif choice in ("7", "storage"):
                    _do_storage()
                elif choice in ("8", "guide", "docs", "help"):
                    _do_guide()
                else:
                    return
            except (KeyboardInterrupt, EOFError):
                return
        else:
            console.print("[bold]LocalPilot: Run AI on this computer. We will find a suitable model and help you set it up.[/bold]\n")
            console.print("Common commands:")
            console.print("  [cyan]localpilot[/cyan]             Launch interactive guided menu")
            console.print("  [cyan]localpilot tui[/cyan]         Open full visual dashboard")
            console.print("  [cyan]localpilot chat[/cyan]        Chat directly with your model")
            console.print("  [cyan]localpilot discover[/cyan]    Find compatible models")
            console.print("  [cyan]localpilot installed[/cyan]   View your installed models")
            console.print("  [cyan]localpilot doctor[/cyan]      Check computer capabilities")
            console.print("  [cyan]localpilot stop[/cyan]        Stop an active model instance")
            console.print("  [cyan]localpilot guide[/cyan]       Read step-by-step documentation\n")
            console.print("Run [cyan]localpilot --help[/cyan] to see all available commands.")


@app.command("guide")
def guide_command(
    topic: Optional[str] = typer.Argument(None, help="Documentation topic: novice, technical, faq, commands, overview, all"),
) -> None:
    """Read comprehensive guides, architecture manuals, and FAQ.

    Non-technical: Step-by-step beginner instructions from hardware check to chat.
    Technical: 3-Axis execution planning, memory budgets, process isolation, and security.

    Examples:
      localpilot guide
      localpilot guide novice
      localpilot guide technical
      localpilot guide faq
      localpilot guide commands
    """
    _do_guide(topic)


@app.command("docs")
def docs_command(
    topic: Optional[str] = typer.Argument(None, help="Documentation topic: novice, technical, faq, commands, overview, all"),
) -> None:
    """Read comprehensive guides, architecture manuals, and FAQ (alias for guide).

    Non-technical: Step-by-step beginner instructions from hardware check to chat.
    Technical: 3-Axis execution planning, memory budgets, process isolation, and security.

    Examples:
      localpilot docs
      localpilot docs novice
      localpilot docs technical
    """
    _do_guide(topic)


@app.command("tui")
def launch_tui() -> None:
    """Open the visual full-screen terminal dashboard (Textual TUI).

    Non-technical: Complete visual mouse- and keyboard-friendly dashboard with real-time hardware gauges, tabs, logs, and interactive chat.
    Technical: Built with Textual; runs fully locally inside your current terminal session. Press '?' or 'h' anywhere for inline help tabs.

    Examples:
      localpilot tui
    """
    from localpilot.tui.app import LocalPilotTUI
    tui_app = LocalPilotTUI()
    tui_app.run()


@app.command("doctor")
def run_doctor(
    plain: bool = typer.Option(False, "--plain", help="Print in plain text format without colors or borders"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON diagnostic data"),
) -> None:
    """Check computer hardware, available memory budget, GPUs, and runtimes.

    Non-technical: Tells you immediately what your computer is capable of running safely without freezing or crashing.
    Technical: Probes OS, CPU architectures/AVX flags, physical/logical cores, unified vs discrete RAM budgets, CUDA/Metal GPUs, and existing runtime processes/binaries.

    Examples:
      localpilot doctor
      localpilot doctor --plain
      localpilot doctor --json
    """
    _do_doctor(plain=plain, json_output=json_output)


@app.command("discover")
def discover_models(
    task: Optional[str] = typer.Option(None, "--task", help="Filter by task category: chat, coding"),
    query: str = typer.Option("", "--query", "-q", help="Search text matching name, family, or tags"),
    plain: bool = typer.Option(False, "--plain", help="Print plain tabular text without rich styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON candidate models"),
) -> None:
    """Find and score models that are 100% compatible with your computer.

    Non-technical: Lists models you can actually run. Red/incompatible models are filtered out so you never waste bandwidth or download something that won't work.
    Technical: Evaluates 3-axis matrix (CPU/GPU/RAM) against catalog candidates, computes memory fit (Exact Fit, Comfortable, Tight, Partial GPU Offload), and scores candidates based on your hardware profile and task.

    Examples:
      localpilot discover
      localpilot discover --task coding
      localpilot discover -q "qwen"
      localpilot discover --plain
    """
    _do_discover(task=task, query=query, plain=plain, json_output=json_output)


@app.command("installed")
def list_installed(
    plain: bool = typer.Option(False, "--plain", help="Print plain text without styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON installations"),
) -> None:
    """View models already downloaded and registered on your computer.

    Non-technical: Shows your personal library of ready-to-run models, their file sizes, and current status.
    Technical: Queries the local SQLite installation registry for readiness stage (untested, verified_tested, active), runtime adapters, and installation IDs.

    Examples:
      localpilot installed
      localpilot installed --json
    """
    _do_installed(plain=plain, json_output=json_output)


@app.command("running")
def list_running(
    plain: bool = typer.Option(False, "--plain", help="Print plain text without styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON running instances"),
) -> None:
    """View active running models and reconciled local processes.

    Non-technical: See which models are currently consuming memory and active in the background.
    Technical: Reconciles live OS process IDs (PIDs) with SQLite instance records, verifies HTTP health endpoints, and cleans up dead/zombie references safely.

    Examples:
      localpilot running
      localpilot running --json
    """
    _do_running(plain=plain, json_output=json_output)


@app.command("run")
def run_model(
    target: Optional[str] = typer.Argument(None, help="Installation ID or Model Name to start"),
) -> None:
    """Start an installed model and make it ready for local inference.

    Non-technical: Wakes up the model so you can talk to it via the chat command or web API.
    Technical: Executes the selected runtime adapter (Ollama or llama.cpp), assigns an isolated port, tracks PID ownership, and verifies real readiness via inference probe before returning.

    Examples:
      localpilot run
      localpilot run "Qwen 2.5 Coder 1.5B"
      localpilot run inst_llama3_8b
    """
    target_val = _val(target, None)
    if not target_val:
        db = get_db()
        rows = db.get_connection().execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()
        if not rows:
            console.print("[dim]No models installed yet. Run [cyan]localpilot[/cyan] to get started.[/dim]")
            return
        if len(rows) == 1:
            target_val = rows[0]["name"]
        else:
            console.print("[bold]Select a model to start:[/bold]")
            for idx, r in enumerate(rows, 1):
                console.print(f"  [bold cyan]{idx}[/bold cyan]) {r['name']} ({r['runtime_name']})")
            choice = typer.prompt("Enter number", default="1").strip()
            try:
                sel = int(choice) - 1
                target_val = rows[sel]["name"] if 0 <= sel < len(rows) else rows[0]["name"]
            except ValueError:
                target_val = choice
    _do_run(target_val)


@app.command("stop")
def stop_model(
    target: Optional[str] = typer.Argument(None, help="Instance ID, Installation ID, or Model Name to stop"),
    force: bool = typer.Option(False, "--force", "-f", help="Force stop immediately (SIGKILL) if graceful stop times out"),
) -> None:
    """Stop an active model instance and free up system memory.

    Non-technical: Closes the model process cleanly so your RAM and GPU are fully freed for other applications.
    Technical: Sends SIGTERM for graceful shutdown, verifies child process termination, updates SQLite status to 'stopped', and protects external/system-managed processes from accidental termination.

    Examples:
      localpilot stop
      localpilot stop "Qwen 2.5 Coder 1.5B"
      localpilot stop inst_run_abc123
      localpilot stop inst_llama3_8b --force
    """
    target_val = _val(target, None)
    if not target_val:
        svc = RuntimeLifecycleService()
        active = svc.reconcile_running_instances()
        if not active:
            console.print("[dim]No models are currently running.[/dim]")
            return
        if len(active) == 1:
            target_val = active[0].get("instance_id") or active[0].get("model_id")
        else:
            console.print("[bold]Select an active model to stop:[/bold]")
            for idx, a in enumerate(active, 1):
                console.print(f"  [bold cyan]{idx}[/bold cyan]) {a.get('model_id')} (PID: {a.get('pid')})")
            choice = typer.prompt("Enter number", default="1").strip()
            try:
                sel = int(choice) - 1
                target_val = active[sel].get("instance_id") if 0 <= sel < len(active) else active[0].get("instance_id")
            except ValueError:
                target_val = choice
    _do_stop(target_val, force=force)


@app.command("chat")
def chat_model(
    target: Optional[str] = typer.Argument(None, help="Installation ID, Model ID, or recognizable Model Name to chat with"),
) -> None:
    """Open an interactive conversation with a model in your terminal.

    Non-technical: Chat directly with your local AI, just like online chat bots, but 100% private and offline on your computer.
    Technical: Connects to the local HTTP inference endpoint (streaming OpenAI-compatible or Ollama chat endpoint) and streams tokens directly to the terminal. Type '/exit' or '/quit' to leave.

    Examples:
      localpilot chat
      localpilot chat "Qwen 2.5 Coder 1.5B"
      localpilot chat inst_llama3_8b
    """
    target_val = _val(target, None)
    if not target_val:
        db = get_db()
        rows = db.get_connection().execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()
        if not rows:
            console.print("[dim]No models installed yet. Run [cyan]localpilot[/cyan] to launch the guided setup.[/dim]")
            return
        if len(rows) == 1:
            target_val = rows[0]["name"]
        else:
            console.print("[bold]Select a model to chat with:[/bold]")
            for idx, r in enumerate(rows, 1):
                console.print(f"  [bold cyan]{idx}[/bold cyan]) {r['name']} ({r['runtime_name']})")
            choice = typer.prompt("Enter number", default="1").strip()
            try:
                sel = int(choice) - 1
                target_val = rows[sel]["name"] if 0 <= sel < len(rows) else rows[0]["name"]
            except ValueError:
                target_val = choice
    _do_chat(target_val)


@app.command("update")
def update_model(
    installation_id: str = typer.Argument(..., help="Installation ID to check/update"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt and apply updates immediately"),
) -> None:
    """Check for model updates or upstream weight refreshes.

    Non-technical: Checks if the model author published a fix or newer revision of the weights and updates safely.
    Technical: Queries HuggingFace or Ollama registry for updated manifests/hashes; reports honest status (up-to-date, update-available, or service-managed).

    Examples:
      localpilot update inst_qwen25_coder
      localpilot update inst_qwen25_coder -y
    """
    _do_update(installation_id, yes=yes)


@app.command("storage")
def show_storage(
    plain: bool = typer.Option(False, "--plain", help="Print plain text without styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON storage breakdown"),
) -> None:
    """Review disk usage, model weight files, and cache breakdown.

    Non-technical: Clearly shows how much storage LocalPilot and your models are using on your drive.
    Technical: Scans LocalPilot managed directories (~/.local/share/localpilot), categorizes bytes by weights, cache, logs, and database, and reports free disk capacity.

    Examples:
      localpilot storage
      localpilot storage --json
    """
    _do_storage(plain=plain, json_output=json_output)


@app.command("activity")
def show_activity(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of records to show"),
    plain: bool = typer.Option(False, "--plain", help="Print plain text without styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON activity log"),
) -> None:
    """View recent friendly activity history and completed tasks.

    Non-technical: A clear, human-readable timeline of what LocalPilot has done on your computer.
    Technical: Reads from SQLite activity log containing event timestamps, action titles, summaries, and success/failure flags.

    Examples:
      localpilot activity
      localpilot activity -n 50
      localpilot activity --json
    """
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
    level: Optional[str] = typer.Option(None, "--level", help="Filter by severity level: debug, info, warning, error"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max lines to show"),
) -> None:
    """View structured diagnostic event logs for troubleshooting.

    Non-technical: Useful when asking for help or checking what happened behind the scenes.
    Technical: Reads and filters events.jsonl with timestamp, severity level (DEBUG, INFO, WARNING, ERROR), category, and message payload.

    Examples:
      localpilot logs
      localpilot logs --level error
      localpilot logs -n 100
    """
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
    candidate_id: Optional[str] = typer.Argument(None, help="Candidate ID to install (from 'localpilot discover')"),
    scope: str = typer.Option("isolated", "--scope", help="Installation scope: isolated, user, system"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Accept execution plan without confirmation prompt"),
) -> None:
    """Guided, verified installation of a model onto your computer.

    Non-technical: Downloads the real model weights, verifies the download checksum, starts the engine, and performs an actual test question before declaring success.
    Technical: Generates an execution plan, provisions required runtime binaries into isolated or user scope, pulls GGUF or Ollama models, executes probe inference, and registers verified state.

    Examples:
      localpilot setup
      localpilot setup cand_qwen25_coder
      localpilot setup cand_llama32_3b --yes
    """
    cat_svc = CatalogService()
    rec_plan = None
    alt_plans = []
    if not candidate_id:
        # Pick top recommended model via recommendation engine
        shortlist = cat_svc.get_shortlist()
        if not shortlist:
            render_error_box("No compatible models found within this computer's safe memory budget.")
            return
        candidate = shortlist[0].candidate
        rec_plan = shortlist[0].recommended_plan
        alt_plans = shortlist[0].alternative_plans
    else:
        candidate = cat_svc.get_candidate(candidate_id)
        if not candidate:
            render_error_box(f"Candidate '{candidate_id}' not found.")
            return

        # Check for recommended plan from catalog
        matched = [r for r in cat_svc.discover(query=candidate.model_id) if r.candidate.candidate_id == candidate.candidate_id]
        if matched:
            rec_plan = matched[0].recommended_plan
            alt_plans = matched[0].alternative_plans

    # Runtime availability check with honest warning
    if rec_plan:
        from localpilot.domain.recommendation import ModelRecommendationEngine
        hw_report = cat_svc.inspector.inspect()
        is_avail, avail_msg = ModelRecommendationEngine.recheck_runtime_availability(candidate, rec_plan, hw_report.hardware)
        if not is_avail:
            console.print(f"[bold yellow]Warning:[/bold yellow] {avail_msg}\n")

    workflow = SetupWorkflow()
    install_scope = InstallationScope(scope.lower())

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

        approval = ApprovalRecord.create_for_plan(plan, approved_by="user_cli")
        with console.status(f"Setting up {candidate.name}..."):
            res = await workflow.execute_setup(candidate, plan, progress_callback=_progress, approval=approval)
        console.print(f"\n[bold green]Success![/bold green] Model set up with ID: [cyan]{res['installation_id']}[/cyan]")
        console.print(f"To start chatting immediately: [bold cyan]localpilot chat {candidate.name}[/bold cyan]")
        console.print(f"[dim]To run as background service only: localpilot run {res['installation_id']}[/dim]")

    asyncio.run(_run())


@app.command("remove")
def remove_model(
    installation_id: Optional[str] = typer.Argument(None, help="Installation ID or Model Name to remove"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
    delete_files: bool = typer.Option(True, "--delete-files/--keep-files", help="Delete model weight files from disk (default: delete)"),
) -> None:
    """Safely remove an installed model and free disk space.

    Non-technical: Deletes the model from your library and removes downloaded files to free up disk space.
    Technical: Stops any running instance, removes local GGUF weights or invokes model deletion in the runtime, unregisters database entry, and protects shared files.

    Examples:
      localpilot remove "Qwen 2.5 Coder 1.5B"
      localpilot remove inst_qwen25_coder
      localpilot remove inst_qwen25_coder -y
    """
    mgr = StorageManager()
    db = get_db()
    conn = db.get_connection()

    target_val = _val(installation_id, None)
    if not target_val:
        rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()
        if not rows:
            console.print("[dim]No installed models to remove.[/dim]")
            return
        console.print("[bold]Select a model to remove:[/bold]")
        for idx, r in enumerate(rows, 1):
            console.print(f"  [bold cyan]{idx}[/bold cyan]) {r['name']} ({r['runtime_name']})")
        choice = typer.prompt("Enter number", default="1").strip()
        try:
            sel = int(choice) - 1
            target_val = rows[sel]["name"] if 0 <= sel < len(rows) else rows[0]["name"]
        except ValueError:
            target_val = choice

    row = conn.execute(
        "SELECT installation_id, name FROM installations WHERE installation_id = ? OR model_id = ? OR name = ? OR name LIKE ?",
        (target_val, target_val, target_val, f"%{target_val}%"),
    ).fetchone()
    resolved_id = row["installation_id"] if row else target_val
    display_name = row["name"] if row else target_val

    if not yes:
        confirm = typer.confirm(f"Are you sure you want to remove model '{display_name}'?", default=False)
        if not confirm:
            console.print("Removal cancelled.")
            return

    res = mgr.safe_remove_installation(resolved_id, delete_files=delete_files)
    if res.get("success"):
        freed_mb = res.get("bytes_freed", 0) / (1024 * 1024)
        console.print(f"[bold green]Removed model '{display_name}'.[/bold green] Freed: {freed_mb:.1f} MB.")
    else:
        err = create_app_error(
            ErrorCode.DELETION_FAILED,
            operation="remove",
            stage="cleanup",
            explanation=res.get("error", "Failed to remove installation."),
            known_impact=f"Freed {res.get('bytes_freed', 0) / (1024*1024):.1f} MB. Preserved {len(res.get('files_preserved', []))} files on disk.",
            recommended_action=RecoveryAction.REVIEW_STORAGE,
            technical_details=res.get("error", ""),
        )
        render_app_error_box(err)


@app.command("support")
def prepare_support(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Custom output file path (default: localpilot-support.json)"),
) -> None:
    """Prepare a reviewed, redacted diagnostic report for troubleshooting.

    Non-technical: Creates a single file you can share when reporting issues or asking for help.
    Technical: Collects hardware specs, OS info, sanitized logs, installation states, and runtime configs. Strictly redacts sensitive tokens, API keys, paths, and user prompts.

    Examples:
      localpilot support
      localpilot support -o ./debug-report.json
    """
    out_path = output or Path("localpilot-support.json")
    export_support_bundle(out_path)
    console.print(f"[bold green]Diagnostic report exported to:[/bold green] [cyan]{out_path.resolve()}[/cyan]")
    console.print("[dim]All personal tokens, keys, prompts, and private paths have been redacted.[/dim]")


@self_app.command("update")
def self_update() -> None:
    """Check PyPI and update LocalPilot application to latest version.

    Non-technical: Checks if a newer version of LocalPilot exists and gives the exact 1-line command to upgrade.
    Technical: Queries the official PyPI JSON API (https://pypi.org/pypi/localpilot/json) and detects pipx vs virtualenv/pip installation.

    Examples:
      localpilot self update
    """
    import httpx
    import localpilot
    current = getattr(localpilot, "__version__", "0.1.6")
    console.print(f"[bold cyan]Checking for LocalPilot updates (current: v{current})...[/bold cyan]")
    try:
        resp = httpx.get("https://pypi.org/pypi/localpilot/json", timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("info", {}).get("version", current)
            if latest != current:
                console.print(f"\n[bold green]New version available:[/bold green] [bold cyan]v{latest}[/bold cyan] (current: v{current})")
                is_pipx = "pipx" in sys.prefix or "PIPX_HOME" in os.environ
                if is_pipx:
                    console.print("To update, run: [bold cyan]pipx upgrade localpilot[/bold cyan]")
                else:
                    console.print("To update, run: [bold cyan]pip install -U localpilot[/bold cyan]")
                return
            else:
                console.print(f"LocalPilot is up to date (version {current}).")
                return
    except Exception as e:
        console.print(f"[yellow]Could not reach PyPI to verify updates ({e}).[/yellow]")
        console.print("To check manually, visit: https://pypi.org/project/localpilot/")


@self_app.command("uninstall")
def self_uninstall(
    yes: bool = typer.Option(False, "-y", "--yes", help="Confirm uninstallation without prompt"),
    purge_models: bool = typer.Option(False, "--purge-models", help="Also delete downloaded models and database"),
) -> None:
    """Uninstall LocalPilot application and optionally purge stored models.

    Non-technical: Removes LocalPilot from your system with complete transparency.
    Technical: Optionally purges ~/.local/share/localpilot, unlinks ~/.local/bin/localpilot, and runs pipx uninstall or provides pip commands.

    Examples:
      localpilot self uninstall
      localpilot self uninstall --purge-models
    """
    from localpilot.storage.db import get_default_data_dir
    import subprocess
    data_dir = get_default_data_dir()

    console.print("[bold yellow]LocalPilot Application Uninstallation[/bold yellow]")
    if purge_models:
        console.print(f"[bold red]WARNING:[/bold red] --purge-models will delete all models and data in [cyan]{data_dir}[/cyan]")
    else:
        console.print(f"[dim]Model files and configuration in {data_dir} will be preserved.[/dim]")

    if not yes:
        confirm = typer.confirm("Are you sure you want to proceed with uninstallation?", default=False)
        if not confirm:
            console.print("Uninstallation cancelled.")
            return

    # 1. Purge data directory if requested
    if purge_models:
        if data_dir.exists():
            shutil.rmtree(data_dir)
            console.print(f"[green]Purged data directory: {data_dir}[/green]")

    # 2. Check for pipx symlink or executable to clean up
    user_symlink = Path(os.path.expanduser("~/.local/bin/localpilot"))
    if user_symlink.is_symlink() or user_symlink.exists():
        try:
            user_symlink.unlink()
            console.print(f"[green]Removed user symlink: {user_symlink}[/green]")
        except Exception:
            pass

    # 3. Detect package manager and uninstall
    is_pipx = "pipx" in sys.prefix or "PIPX_HOME" in os.environ
    if is_pipx and shutil.which("pipx"):
        console.print("[cyan]Running pipx uninstall localpilot...[/cyan]")
        res = subprocess.run(["pipx", "uninstall", "localpilot"], capture_output=True, text=True)
        if res.returncode == 0:
            console.print("[bold green]LocalPilot application uninstalled successfully via pipx.[/bold green]")
            return

    console.print("\n[bold green]Cleanup finished.[/bold green]")
    console.print("To complete removal of the Python package from your environment, run:")
    console.print("  [bold cyan]pip uninstall localpilot[/bold cyan] (or [bold cyan]pipx uninstall localpilot[/bold cyan])")
