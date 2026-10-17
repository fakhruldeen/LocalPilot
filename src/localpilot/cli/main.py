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
from localpilot.sources.catalog_service import CatalogService
from localpilot.application.accounting_service import ApiAccountingService
from localpilot.application.api_key_service import ApiKeyService
from localpilot.application.integration_service import IntegrationTemplateService
from localpilot.application.version_service import ModelVersionService
from localpilot.gateway.server import GatewayServer
from localpilot.storage.db import get_db, get_default_data_dir, get_default_log_dir
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

versions_app = typer.Typer(help="Manage immutable model versions, pins, comparison, and safe removal.")
app.add_typer(versions_app, name="versions")

alias_app = typer.Typer(help="Manage stable model aliases, activation, and rollback.")
app.add_typer(alias_app, name="alias")

gateway_app = typer.Typer(help="Manage local OpenAI-compatible inference API gateway.")
app.add_typer(gateway_app, name="gateway")
app.add_typer(gateway_app, name="api")

keys_app = typer.Typer(help="Manage local API keys and application permissions.")
app.add_typer(keys_app, name="keys")


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
            "  [bold cyan]•[/bold cyan] [bold]novice[/bold]      - Step-by-step beginner walkthrough from zero to chat\n"
            "  [bold cyan]•[/bold cyan] [bold]concepts[/bold]    - Essential concepts: memory, context, models & runtimes\n"
            "  [bold cyan]•[/bold cyan] [bold]terminology[/bold] - Plain-language dictionary of terms and distinctions\n"
            "  [bold cyan]•[/bold cyan] [bold]technical[/bold]   - 3-Axis execution, memory offload math & process safety\n"
            "  [bold cyan]•[/bold cyan] [bold]faq[/bold]         - Answers to common questions for all steps\n"
            "  [bold cyan]•[/bold cyan] [bold]commands[/bold]    - Full CLI command and flag reference\n"
            "  [bold cyan]•[/bold cyan] [bold]overview[/bold]    - Privacy principles, local-only architecture\n"
            "  [bold cyan]•[/bold cyan] [bold]all[/bold]         - Read the complete documentation manual\n\n"
            "[dim]Usage: localpilot guide <topic> (e.g. localpilot guide concepts)[/dim]",
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
    topic: Optional[str] = typer.Argument(None, help="Documentation topic: novice, technical, faq, commands, concepts, terminology, overview, all"),
) -> None:
    """Read comprehensive guides, architecture manuals, and FAQ.

    Non-technical: Step-by-step beginner instructions from hardware check to chat.
    Technical: 3-Axis execution planning, memory budgets, process isolation, and security.

    Examples:
      localpilot guide
      localpilot guide novice
      localpilot guide technical
      localpilot guide concepts
      localpilot guide terminology
      localpilot guide faq
      localpilot guide commands
    """
    _do_guide(topic)


@app.command("docs")
def docs_command(
    topic: Optional[str] = typer.Argument(None, help="Documentation topic: novice, technical, faq, commands, concepts, terminology, overview, all"),
) -> None:
    """Read comprehensive guides, architecture manuals, and FAQ (alias for guide).

    Non-technical: Step-by-step beginner instructions from hardware check to chat.
    Technical: 3-Axis execution planning, memory budgets, process isolation, and security.

    Examples:
      localpilot docs
      localpilot docs novice
      localpilot docs technical
      localpilot docs terminology
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
    current = getattr(localpilot, "__version__", "0.1.8")
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


# =============================================================================
# Model Versions CLI Subcommands
# =============================================================================

@versions_app.command("list")
def versions_list(
    model_id: Optional[str] = typer.Argument(None, help="Optional model family ID to filter by"),
    all: bool = typer.Option(False, "--all", "-a", help="Include deleted versions"),
) -> None:
    """List installed model versions with pinning, size, and variant details."""
    service = ModelVersionService()
    versions = service.list_versions(model_id=model_id, include_deleted=all)
    if not versions:
        console.print("[yellow]No model versions found.[/yellow]")
        return

    table = Table(title="Installed Model Versions", show_header=True, header_style="bold cyan")
    table.add_column("Version ID", style="bold")
    table.add_column("Logical Model")
    table.add_column("Tag")
    table.add_column("Quantization")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Pinned?", justify="center")
    table.add_column("Created At", style="dim")

    for v in versions:
        size_mb = f"{v.artifact_size_bytes / (1024*1024):.1f}" if v.artifact_size_bytes else "-"
        pinned_badge = "[bold green]YES[/bold green]" if v.is_pinned else "[dim]no[/dim]"
        table.add_row(
            v.version_id,
            v.logical_model_id,
            v.version_label,
            v.quantization or "n/a",
            size_mb,
            pinned_badge,
            v.created_at[:19].replace("T", " ") if v.created_at else "-",
        )
    console.print(table)


@versions_app.command("info")
def versions_info(
    version_id: str = typer.Argument(..., help="Model version ID to inspect"),
) -> None:
    """Inspect an immutable model version, execution profiles, and verification status."""
    service = ModelVersionService()
    v = service.get_version(version_id)
    if not v:
        render_error_box("Version Not Found", f"No version found with ID '{version_id}'.")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Model Version:[/bold cyan] {v.version_id}")
    console.print(f"  [bold]Logical Model:[/bold]   {v.logical_model_id}")
    console.print(f"  [bold]Version Tag:[/bold]     {v.version_label}")
    console.print(f"  [bold]Quantization:[/bold]    {v.quantization or 'n/a'}")
    console.print(f"  [bold]Artifact Digest:[/bold] {v.artifact_digest or 'unknown'}")
    console.print(f"  [bold]Installed Path:[/bold]  {v.installed_path or 'n/a'}")
    console.print(f"  [bold]Pinned:[/bold]          {'YES (' + (v.pinned_reason or 'protected') + ')' if v.is_pinned else 'no'}")
    console.print(f"  [bold]Created At:[/bold]      {v.created_at}")

    profiles = service.list_execution_profiles(v.version_id)
    if profiles:
        console.print("\n[bold]Execution Profiles:[/bold]")
        ptable = Table(show_header=True, header_style="bold magenta")
        ptable.add_column("Profile ID")
        ptable.add_column("Name")
        ptable.add_column("Backend")
        ptable.add_column("Context")
        ptable.add_column("Temp")
        for p in profiles:
            ptable.add_row(p.profile_id, p.profile_name, p.inference_backend, str(p.context_tokens), str(p.temperature))
        console.print(ptable)

    verifs = service.get_verifications(v.version_id)
    if verifs:
        console.print("\n[bold]Verification Records:[/bold]")
        vtable = Table(show_header=True, header_style="bold green")
        vtable.add_column("Verification ID")
        vtable.add_column("Test Type")
        vtable.add_column("Outcome")
        vtable.add_column("Verified At")
        for vr in verifs:
            outcome_style = "[green]PASSED[/green]" if vr.passed else "[red]FAILED[/red]"
            vtable.add_row(vr.verification_id, vr.test_type, outcome_style, vr.verified_at[:19].replace("T", " "))
        console.print(vtable)


@versions_app.command("pin")
def versions_pin(
    version_id: str = typer.Argument(..., help="Model version ID to pin"),
    reason: str = typer.Option("User pinned", "--reason", "-r", help="Reason for pinning"),
) -> None:
    """Pin a model version to protect it from automatic cleanup or pruning."""
    service = ModelVersionService()
    try:
        service.pin_version(version_id, reason=reason)
        render_message_box("Version Pinned", f"Version '{version_id}' is now pinned and protected.\nReason: {reason}")
    except Exception as exc:
        render_error_box("Pin Failed", str(exc))
        raise typer.Exit(code=1)


@versions_app.command("unpin")
def versions_unpin(
    version_id: str = typer.Argument(..., help="Model version ID to unpin"),
) -> None:
    """Unpin a model version, allowing safe removal or pruning."""
    service = ModelVersionService()
    try:
        res = service.unpin_version(version_id)
        if res.is_pinned:
            console.print(f"[yellow]Version '{version_id}' pin count decreased (remaining pins: {res.pin_count}).[/yellow]")
        else:
            render_message_box("Version Unpinned", f"Version '{version_id}' is no longer pinned.")
    except Exception as exc:
        render_error_box("Unpin Failed", str(exc))
        raise typer.Exit(code=1)


@versions_app.command("compare")
def versions_compare(
    version_a: str = typer.Argument(..., help="First version ID"),
    version_b: str = typer.Argument(..., help="Second version ID"),
) -> None:
    """Compare two installed model versions side-by-side."""
    service = ModelVersionService()
    try:
        cmp = service.compare_versions(version_a, version_b)
        va = cmp["version_a"]
        vb = cmp["version_b"]

        table = Table(title=f"Comparison: {version_a} vs {version_b}", show_header=True, header_style="bold cyan")
        table.add_column("Attribute", style="bold")
        table.add_column(f"Version A ({va['version_id']})")
        table.add_column(f"Version B ({vb['version_id']})")

        table.add_row("Logical Model", va["logical_model_id"], vb["logical_model_id"])
        table.add_row("Version Tag", va["version_label"], vb["version_label"])
        table.add_row("Quantization", va["quantization"] or "-", vb["quantization"] or "-")
        table.add_row("Size (MB)", f"{va['artifact_size_bytes'] / (1024*1024):.1f}", f"{vb['artifact_size_bytes'] / (1024*1024):.1f}")
        table.add_row("Context Length", str(va["context_length"]), str(vb["context_length"]))
        table.add_row("Pinned?", str(va["is_pinned"]), str(vb["is_pinned"]))
        table.add_row("Profiles Count", str(len(va["profiles"])), str(len(vb["profiles"])))
        console.print(table)
    except Exception as exc:
        render_error_box("Comparison Failed", str(exc))
        raise typer.Exit(code=1)


@versions_app.command("remove")
def versions_remove(
    version_id: str = typer.Argument(..., help="Model version ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Force removal even if pinned or active under aliases"),
) -> None:
    """Safely remove a model version with pin protection and shared file preservation."""
    service = ModelVersionService()
    try:
        result = service.safe_remove_version(version_id, force=force)
        render_message_box(
            "Version Removed",
            f"Version '{version_id}' removed from active inventory.\n"
            f"Physical file deleted: {result['file_deleted']}\n"
            f"Note: {result['reason']}",
        )
    except Exception as exc:
        render_error_box("Removal Refused", str(exc))
        raise typer.Exit(code=1)


# =============================================================================
# Model Aliases CLI Subcommands
# =============================================================================

@alias_app.command("list")
def alias_list() -> None:
    """List stable model aliases, target versions, and logical models."""
    service = ModelVersionService()
    aliases = service.list_aliases()
    if not aliases:
        console.print("[yellow]No aliases found.[/yellow]")
        return

    table = Table(title="Model Aliases", show_header=True, header_style="bold cyan")
    table.add_column("Alias Name", style="bold green")
    table.add_column("Target Version")
    table.add_column("Logical Model")
    table.add_column("System Alias?", justify="center")
    table.add_column("Last Updated", style="dim")

    for a in aliases:
        is_sys = "[cyan]yes[/cyan]" if a.is_system_alias else "[dim]no[/dim]"
        table.add_row(
            a.alias_name,
            a.version_id,
            a.logical_model_id or "-",
            is_sys,
            a.updated_at[:19].replace("T", " ") if a.updated_at else "-",
        )
    console.print(table)


@alias_app.command("set")
def alias_set(
    alias_name: str = typer.Argument(..., help="Alias name (e.g. 'writing', 'coding', 'default')"),
    version_id: str = typer.Argument(..., help="Target model version ID to map"),
    reason: str = typer.Option("Manual activation", "--reason", "-r", help="Reason for activation"),
) -> None:
    """Point or switch an alias to a target model version atomically."""
    service = ModelVersionService()
    try:
        alias = service.activate_alias(alias_name, version_id, reason=reason)
        render_message_box(
            "Alias Updated",
            f"Alias '[bold green]{alias.alias_name}[/bold green]' is now active and pointing to version '{alias.version_id}'.",
        )
    except Exception as exc:
        render_error_box("Alias Update Failed", str(exc))
        raise typer.Exit(code=1)


@alias_app.command("rollback")
def alias_rollback(
    alias_name: str = typer.Argument(..., help="Alias name to rollback to its prior version"),
) -> None:
    """Roll back an alias to its immediately preceding version."""
    service = ModelVersionService()
    try:
        alias = service.rollback_alias(alias_name)
        if alias:
            render_message_box(
                "Alias Rolled Back",
                f"Alias '[bold green]{alias.alias_name}[/bold green]' successfully rolled back to version '{alias.version_id}'.",
            )
    except Exception as exc:
        render_error_box("Rollback Failed", str(exc))
        raise typer.Exit(code=1)


@alias_app.command("history")
def alias_history(
    alias_name: Optional[str] = typer.Argument(None, help="Optional alias name to inspect"),
) -> None:
    """Show the audit history of alias activations, switches, and rollbacks."""
    service = ModelVersionService()
    records = service.get_alias_history(alias_name=alias_name)
    if not records:
        console.print("[yellow]No alias history found.[/yellow]")
        return

    table = Table(title="Alias Redirection History", show_header=True, header_style="bold cyan")
    table.add_column("Alias Name", style="bold")
    table.add_column("Previous Version", style="dim")
    table.add_column("New Version", style="green")
    table.add_column("Reason")
    table.add_column("Changed At", style="dim")

    for r in records:
        table.add_row(
            r.alias_name,
            r.previous_version_id or "-",
            r.new_version_id,
            r.reason or "-",
            r.changed_at[:19].replace("T", " ") if r.changed_at else "-",
        )
    console.print(table)


# =============================================================================
# Local OpenAI-Compatible API Gateway Subcommands
# =============================================================================

@gateway_app.command("start")
def gateway_start(
    port: int = typer.Option(8080, "--port", "-p", help="Port to bind gateway server to (loopback only)"),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Run gateway server in the background"),
) -> None:
    """Start local OpenAI-compatible inference gateway on loopback (127.0.0.1)."""
    running = GatewayServer.get_running_state()
    if running:
        console.print(f"[yellow]Gateway is already running (PID: {running['pid']}, Port: {running['port']}).[/yellow]")
        return

    if daemon:
        import subprocess
        log_file = get_default_log_dir() / "gateway.log"
        cmd = [sys.executable, "-m", "localpilot.cli.main", "gateway", "start", "--port", str(port)]
        with open(log_file, "a") as f_out:
            proc = subprocess.Popen(cmd, stdout=f_out, stderr=f_out, start_new_session=True)
        console.print(f"[bold green]LocalPilot Gateway started in background (PID: {proc.pid}, Port: {port}).[/bold green]")
        console.print(f"Log output: [cyan]{log_file}[/cyan]")
        return

    server = GatewayServer(host="127.0.0.1", port=port)
    server.write_state()
    render_banner()
    console.print(f"[bold green]LocalPilot Inference Gateway running at http://127.0.0.1:{port}/v1[/bold green]")
    console.print("[dim]Strict loopback only (127.0.0.1). Endpoints: /v1/models, /v1/chat/completions[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down LocalPilot Gateway...[/yellow]")
    finally:
        server.shutdown()
        server.server_close()
        server.clear_state()
        console.print("[green]Gateway stopped safely.[/green]")


@gateway_app.command("status")
def gateway_status() -> None:
    """Check running state, port, and health of the local inference gateway."""
    running = GatewayServer.get_running_state()
    if not running:
        console.print("[yellow]LocalPilot Gateway is not running.[/yellow]")
        console.print("Run [bold cyan]localpilot gateway start[/bold cyan] to launch.")
        return

    table = Table(title="LocalPilot Gateway Status", show_header=True, header_style="bold green")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Status", "[bold green]RUNNING[/bold green]")
    table.add_row("Process ID (PID)", str(running["pid"]))
    table.add_row("Address", f"http://{running['host']}:{running['port']}/v1")
    table.add_row("Started At", running.get("started_at", "-"))
    console.print(table)


@gateway_app.command("stop")
def gateway_stop() -> None:
    """Stop the running local inference gateway server."""
    running = GatewayServer.get_running_state()
    if not running:
        console.print("[yellow]LocalPilot Gateway is not running.[/yellow]")
        return

    pid = running["pid"]
    try:
        import signal
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        console.print(f"[bold green]Sent stop signal to LocalPilot Gateway (PID: {pid}).[/bold green]")
    except ProcessLookupError:
        console.print(f"[dim]Process {pid} was not running; clearing state.[/dim]")

    # Clear state file
    lf = get_default_data_dir() / "gateway.json"
    if lf.exists():
        try:
            lf.unlink()
        except Exception:
            pass


@gateway_app.command("test")
def gateway_test(
    port: int = typer.Option(8080, "--port", "-p", help="Gateway port to test"),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="API key token for authenticated routes"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model alias to test with a completion probe"),
) -> None:
    """Test connectivity, health, readiness, and sample completions against the running gateway."""
    import httpx

    base_url = f"http://127.0.0.1:{port}"
    console.print(f"[bold cyan]Testing LocalPilot Gateway at {base_url}...[/bold cyan]")

    # 1. Test Liveness Probe
    try:
        r_live = httpx.get(f"{base_url}/v1/health/live", timeout=3.0)
        if r_live.status_code == 200:
            console.print("  [bold green]✓[/bold green] Liveness check passed (/v1/health/live: alive)")
        else:
            console.print(f"  [bold red]✗[/bold red] Liveness check returned {r_live.status_code}")
    except Exception as e:
        console.print(f"  [bold red]✗[/bold red] Cannot connect to gateway at {base_url}: {e}")
        console.print("    Start it with [bold cyan]localpilot gateway start[/bold cyan]")
        return

    # 2. Test Readiness Probe
    try:
        r_ready = httpx.get(f"{base_url}/v1/health/ready", timeout=3.0)
        data = r_ready.json()
        if r_ready.status_code == 200 and data.get("ready"):
            console.print(f"  [bold green]✓[/bold green] Readiness check passed ({data.get('available_models', 0)} models ready)")
        else:
            console.print(f"  [yellow]![/yellow] Gateway alive, but models not ready ({data.get('reason', 'no models registered')})")
    except Exception as e:
        console.print(f"  [bold red]✗[/bold red] Readiness probe failed: {e}")

    # 3. Test Models Discovery
    api_key = key
    if not api_key:
        db = DatabaseService()
        key_svc = ApiKeyService(db)
        keys = [k for k in key_svc.list_keys() if k.status == ApiKeyStatus.ACTIVE]
        if keys:
            console.print(f"  [dim]Found local API key '{keys[0].display_name}' ({keys[0].secret_prefix}...); supply --key <token> for full auth test[/dim]")

    if api_key:
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            r_models = httpx.get(f"{base_url}/v1/models", headers=headers, timeout=4.0)
            if r_models.status_code == 200:
                m_list = r_models.json().get("data", [])
                console.print(f"  [bold green]✓[/bold green] Authentication & model discovery passed ({len(m_list)} models accessible)")
                target_model = model or (m_list[0]["id"] if m_list else None)
                if target_model:
                    console.print(f"  Testing chat completion probe on model '[bold]{target_model}[/bold]'...")
                    t0 = time.time()
                    r_chat = httpx.post(
                        f"{base_url}/v1/chat/completions",
                        headers=headers,
                        json={
                            "model": target_model,
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 5,
                        },
                        timeout=10.0,
                    )
                    latency = (time.time() - t0) * 1000
                    if r_chat.status_code == 200:
                        console.print(f"  [bold green]✓[/bold green] Chat completion test succeeded ({latency:.1f} ms)")
                    else:
                        console.print(f"  [yellow]![/yellow] Chat completion returned status {r_chat.status_code}: {r_chat.text}")
            else:
                console.print(f"  [bold red]✗[/bold red] Model list returned status {r_models.status_code}: {r_models.text}")
        except Exception as e:
            console.print(f"  [bold red]✗[/bold red] Auth test failed: {e}")


# =============================================================================
# Local API Key Management Subcommands
# =============================================================================

@keys_app.command("list")
def keys_list() -> None:
    """List all registered local API keys and applications."""
    service = ApiKeyService()
    keys = service.list_keys()
    if not keys:
        console.print("[yellow]No API keys found.[/yellow]")
        console.print("Create one using [bold cyan]localpilot keys create <name>[/bold cyan].")
        return

    table = Table(title="Local API Keys", show_header=True, header_style="bold cyan")
    table.add_column("Key ID", style="bold")
    table.add_column("Display Name")
    table.add_column("App Name")
    table.add_column("Prefix", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Created At", style="dim")

    for k in keys:
        status_badge = "[bold green]ACTIVE[/bold green]" if k.is_active else "[red]REVOKED[/red]"
        table.add_row(
            k.key_id,
            k.display_name,
            k.app_name or "-",
            k.secret_prefix,
            status_badge,
            k.created_at[:19].replace("T", " ") if k.created_at else "-",
        )
    console.print(table)


@keys_app.command("create")
def keys_create(
    name: str = typer.Argument(..., help="Friendly label for this key (e.g., 'OpenCode', 'VS Code')"),
    app: Optional[str] = typer.Option(None, "--app", "-a", help="Application name identifier"),
    expires_days: Optional[int] = typer.Option(None, "--expires-days", help="Optional expiry in days"),
) -> None:
    """Create a new local API key with secure token generation."""
    service = ApiKeyService()
    expires_at = None
    if expires_days:
        from datetime import timedelta
        exp_dt = datetime.now(timezone.utc) + timedelta(days=expires_days)
        expires_at = exp_dt.isoformat().replace("+00:00", "Z")

    raw_token, record = service.create_key(
        name=name,
        app_name=app,
        expires_at=expires_at,
    )

    console.print(f"\n[bold green]✓ Local API Key Created Successfully![/bold green]")
    console.print(f"  [bold]Key ID:[/bold]       {record.key_id}")
    console.print(f"  [bold]Name:[/bold]         {record.display_name}")
    console.print(f"  [bold]App Name:[/bold]     {record.app_name or 'generic'}")
    console.print(f"  [bold]Safe Prefix:[/bold]  {record.secret_prefix}")
    console.print(f"\n[bold yellow]Plaintext API Token (copy now, will not be shown again):[/bold yellow]")
    console.print(f"  [bold cyan]{raw_token}[/bold cyan]\n")


@keys_app.command("revoke")
def keys_revoke(
    key_id: str = typer.Argument(..., help="API key ID to revoke"),
) -> None:
    """Revoke an API key immediately, blocking future access."""
    service = ApiKeyService()
    try:
        service.revoke_key(key_id)
        render_message_box("Key Revoked", f"API Key '{key_id}' has been permanently revoked.")
    except Exception as exc:
        render_error_box("Revoke Failed", str(exc))
        raise typer.Exit(code=1)


@keys_app.command("rotate")
def keys_rotate(
    key_id: str = typer.Argument(..., help="API key ID to rotate"),
) -> None:
    """Rotate secret token for an existing key, preserving its identity and policy."""
    service = ApiKeyService()
    try:
        raw_token, record = service.rotate_key(key_id)
        render_message_box("Key Rotated", f"API Key '{record.display_name}' ({key_id}) rotated successfully.")
        console.print(f"  [bold]Key ID:[/bold]          {record.key_id}")
        console.print(f"  [bold]New Safe Prefix:[/bold] {record.secret_prefix}")
        console.print(f"\n[bold yellow]New Plaintext API Token (copy now, will not be shown again):[/bold yellow]")
        console.print(f"  [bold cyan]{raw_token}[/bold cyan]\n")
    except Exception as exc:
        render_error_box("Rotation Failed", str(exc))
        raise typer.Exit(code=1)


@keys_app.command("delete")
def keys_delete(
    key_id: str = typer.Argument(..., help="API key ID to delete"),
) -> None:
    """Permanently delete an API key record."""
    service = ApiKeyService()
    service.delete_key(key_id)
    render_message_box("Key Deleted", f"API Key '{key_id}' removed from database.")


# =============================================================================
# Operational Statistics & Dashboards
# =============================================================================

@app.command("stats")
def app_stats(
    key_id: Optional[str] = typer.Option(None, "--key", "-k", help="Filter statistics by API key ID"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Filter statistics by model alias"),
) -> None:
    """Display real-time operational metrics, token usage, and latency summary."""
    acct = ApiAccountingService()
    summary = acct.get_usage_summary(key_id=key_id, model_alias=model)

    table = Table(title="Inference Gateway Operational Metrics", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Requests", str(summary["total_requests"]))
    table.add_row("Successful Requests (200 OK)", f"[green]{summary['successful_requests']}[/green]")
    table.add_row("Error Requests", f"[red]{summary['error_requests']}[/red]" if summary["error_requests"] > 0 else "0")
    table.add_row("Prompt Tokens", f"{summary['total_prompt_tokens']:,}")
    table.add_row("Completion Tokens", f"{summary['total_completion_tokens']:,}")
    table.add_row("Total Tokens", f"[bold]{summary['total_tokens']:,}[/bold]")
    table.add_row("Average Latency (ms)", f"{summary['avg_latency_ms']:.1f}")
    table.add_row("Time to First Token (TTFT ms)", f"{summary['avg_first_token_latency_ms']:.1f}")
    table.add_row("Estimated Generation Speed (TPS)", f"{summary['tokens_per_second']:.1f} tokens/s")
    console.print(table)

    # Per-Model breakdown
    model_breakdown = acct.get_per_model_breakdown()
    if model_breakdown:
        console.print("\n[bold]Usage by Model Alias:[/bold]")
        mt = Table(show_header=True, header_style="bold magenta")
        mt.add_column("Model Alias", style="bold")
        mt.add_column("Requests", justify="right")
        mt.add_column("Prompt Tokens", justify="right")
        mt.add_column("Completion Tokens", justify="right")
        mt.add_column("Total Tokens", justify="right")
        mt.add_column("Avg Latency (ms)", justify="right")
        for m in model_breakdown:
            mt.add_row(
                m["model_alias"],
                str(m["request_count"]),
                f"{m['prompt_tokens']:,}",
                f"{m['completion_tokens']:,}",
                f"{m['total_tokens']:,}",
                f"{m['avg_latency_ms']:.1f}",
            )
        console.print(mt)

    # Per-Key breakdown
    key_breakdown = acct.get_per_key_breakdown()
    if key_breakdown:
        console.print("\n[bold]Usage by Application / Key:[/bold]")
        kt = Table(show_header=True, header_style="bold blue")
        kt.add_column("Key Name", style="bold")
        kt.add_column("App", style="dim")
        kt.add_column("Requests", justify="right")
        kt.add_column("Total Tokens", justify="right")
        kt.add_column("Avg Latency (ms)", justify="right")
        for k in key_breakdown:
            kt.add_row(
                k["key_name"],
                k["app_name"],
                str(k["request_count"]),
                f"{k['total_tokens']:,}",
                f"{k['avg_latency_ms']:.1f}",
            )
        console.print(kt)


# =============================================================================
# Application Integration Generator
# =============================================================================

@app.command("connect")
def app_connect(
    target_app: str = typer.Argument("opencode", help="Application to connect: opencode, continue, cursor, or generic"),
    port: int = typer.Option(8080, "--port", "-p", help="Gateway port"),
    model: str = typer.Option("coding", "--model", "-m", help="Target model alias to connect"),
    write: bool = typer.Option(False, "--write", "-w", help="Automatically write configuration file if supported"),
) -> None:
    """Generate configuration and integration instructions for supported local apps."""
    target_app = target_app.lower().strip()
    key_service = ApiKeyService()
    raw_token, key_record = key_service.create_key(
        name=f"{target_app.title()} Integration",
        app_name=target_app,
    )

    base_url = f"http://127.0.0.1:{port}/v1"

    if target_app == "opencode":
        cfg = IntegrationTemplateService.get_opencode_template(base_url, raw_token, model_alias=model)
        default_path = Path(os.path.expanduser("~/.config/opencode/opencode.json"))

        console.print(f"[bold cyan]OpenCode Integration Setup[/bold cyan]")
        console.print(f"Generated configuration for [bold]{model}[/bold] alias:\n")
        console.print(json.dumps(cfg, indent=2))

        if write:
            res = IntegrationTemplateService.apply_opencode_config(default_path, base_url, raw_token, model_alias=model)
            console.print(f"\n[bold green]✓ Configuration written to: {res['target_path']}[/bold green]")
            if res.get("backup_path"):
                console.print(f"[dim]Backup created at: {res['backup_path']}[/dim]")
        else:
            console.print(f"\nTo save automatically, re-run with: [bold cyan]localpilot connect opencode --write[/bold cyan]")
            console.print(f"Or copy above JSON into: [dim]{default_path}[/dim]")

    elif target_app in ("continue", "vscode"):
        cfg = IntegrationTemplateService.get_continue_template(base_url, raw_token, model_alias=model)
        default_path = Path(os.path.expanduser("~/.continue/config.json"))

        console.print(f"[bold cyan]VS Code Continue Integration Setup[/bold cyan]")
        console.print(f"Generated model entry for [bold]{model}[/bold] alias:\n")
        console.print(json.dumps(cfg, indent=2))

        if write:
            res = IntegrationTemplateService.apply_continue_config(default_path, base_url, raw_token, model_alias=model)
            console.print(f"\n[bold green]✓ Configuration updated in: {res['target_path']}[/bold green]")
            if res.get("backup_path"):
                console.print(f"[dim]Backup created at: {res['backup_path']}[/dim]")
        else:
            console.print(f"\nTo save automatically, re-run with: [bold cyan]localpilot connect continue --write[/bold cyan]")
            console.print(f"Or add to 'models' array in: [dim]{default_path}[/dim]")

    elif target_app == "cursor":
        cursor_info = IntegrationTemplateService.get_cursor_instructions(base_url, raw_token, model_alias=model)
        console.print(f"[bold cyan]Cursor Integration Instructions[/bold cyan]")
        for idx, step in enumerate(cursor_info["steps"], 1):
            console.print(f"  {idx}. {step}")
        console.print(f"\n[dim]Base URL:[/dim]  [bold]{base_url}[/bold]")
        console.print(f"[dim]API Key:[/dim]   [bold]{raw_token}[/bold]")
        console.print(f"[dim]Model:[/dim]     [bold]{model}[/bold]")

    else:
        snippets = IntegrationTemplateService.get_client_snippets(base_url, raw_token, model_alias=model)
        console.print(f"[bold cyan]Generic OpenAI Client Snippets[/bold cyan]\n")
        console.print("[bold]cURL:[/bold]")
        console.print(snippets["curl"])
        console.print("\n[bold]Python OpenAI SDK:[/bold]")
        console.print(snippets["python"])
        console.print("\n[bold]Node.js OpenAI SDK:[/bold]")
        console.print(snippets["nodejs"])

