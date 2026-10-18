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
from localpilot.application.integration_service import ConnectionManager, IntegrationTemplateService
from localpilot.application.startup_service import StartupService
from localpilot.application.hook_service import LifecycleHookService
from localpilot.application.version_service import ModelVersionService
from localpilot.domain.api_contract import HookEvent
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

connections_app = typer.Typer(help="Manage connected applications, configurations, and verification.")
app.add_typer(connections_app, name="connections")

startup_app = typer.Typer(help="Manage automatic user-level startup on login.")
app.add_typer(startup_app, name="startup")

hooks_app = typer.Typer(help="Manage isolated lifecycle event hooks and circuit breakers.")
app.add_typer(hooks_app, name="hooks")



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
    refresh: bool = False,
    all_models: bool = False,
    plain: bool = False,
    json_output: bool = False,
) -> None:
    task_val = _val(task, None)
    query_val = str(_val(query, ""))
    refresh_val = bool(_val(refresh, False))
    all_val = bool(_val(all_models, False))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    cat_svc = CatalogService()
    if refresh_val or cat_svc.is_first_run():
        if not plain_val and not json_val:
            msg = "🔄 First run: Downloading and updating model catalog (Hugging Face Hub, Ollama)..." if cat_svc.is_first_run() and not refresh_val else "🔄 Contacting model registries (Hugging Face Hub, Ollama Library)..."
            console.print(f"[bold cyan]{msg}[/bold cyan]")
        try:
            refresh_res = cat_svc.refresh_catalog()
            if not plain_val and not json_val:
                src_details = refresh_res.get("sources", {})
                hf_info = src_details.get("huggingface", {})
                ollama_info = src_details.get("ollama_library", {})
                hf_cnt = hf_info.get("count", 0)
                ollama_cnt = ollama_info.get("count", 0)
                console.print(f"[green]✓ Catalog refreshed successfully.[/green] (Hugging Face: {hf_cnt} models, Ollama: {ollama_cnt} models)\n")
        except Exception as ex:
            if not plain_val and not json_val:
                console.print(f"[yellow]⚠ Notice: Catalog refresh encountered an issue: {ex}. Using existing cached models.[/yellow]\n")

    task_cat = TaskCategory(task_val.lower()) if task_val and isinstance(task_val, str) else None
    results = cat_svc.discover(task=task_cat, query=query_val, only_compatible=not all_val)

    if not results and not json_val:
        console.print("[bold yellow]No models in the catalog match your query and fit within your computer's safe memory budget.[/bold yellow]")
        if not all_val:
            console.print("[dim]Tip: Use 'localpilot discover --all' to see all catalog models including those requiring more RAM.[/dim]")
        return

    if json_val:
        typer.echo(json.dumps([r.model_dump() for r in results], indent=2))
        return

    if plain_val:
        for r in results:
            cand = r.candidate
            sz_mb = cand.total_bytes / (1024 * 1024)
            compat_str = "Compatible" if r.compatibility.is_compatible else "Exceeds RAM"
            print(f"{cand.name} | Source: {cand.source} | Size: {sz_mb:.0f}MB | Score: {r.score.total_score} | Fit: {compat_str} | ID: {cand.candidate_id}")
        return

    title = "All Catalog Models" if all_val else "Compatible Models for Your Computer"
    table = Table(title=title, border_style="cyan")
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
        score_styled = f"{r.score.total_score:.0f}" if r.compatibility.is_compatible else f"[dim yellow]{r.score.total_score:.0f}[/dim yellow]"
        table.add_row(
            score_styled,
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
    """Default entry point: opens visual TUI dashboard if interactive, or CLI guide summary."""
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            cat_svc = CatalogService()
            if cat_svc.is_first_run():
                console.print("[bold cyan]🔄 Welcome to LocalPilot! Downloading and updating models list for the first time...[/bold cyan]")
                try:
                    cat_svc.refresh_catalog()
                    console.print("[bold green]✓ Models catalog successfully initialized.[/bold green]\n")
                except Exception as ex:
                    console.print(f"[yellow]⚠ Notice: Initial catalog update deferred: {ex}[/yellow]\n")
            from localpilot.tui.app import LocalPilotTUI
            LocalPilotTUI().run()
        else:
            render_banner()
            console.print("[bold]LocalPilot: Run AI on this computer. We will find a suitable model and help you set it up.[/bold]\n")
            console.print("Common commands:")
            console.print("  [cyan]localpilot[/cyan]             Launch visual TUI dashboard")
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
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Fetch latest models from active sources (Hugging Face Hub, Ollama)"),
    all_models: bool = typer.Option(False, "--all", "-a", help="Show all catalog models, including those requiring more RAM than currently available"),
    plain: bool = typer.Option(False, "--plain", help="Print plain tabular text without rich styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON candidate models"),
) -> None:
    """Find and score models that are hardware-compatible with your computer.

    Non-technical: Lists models you can actually run. Red/incompatible models are filtered out so you never waste bandwidth or download something that won't work.
    Technical: Evaluates 3-axis matrix (CPU/GPU/RAM) against catalog candidates, computes memory fit (Exact Fit, Comfortable, Tight, Partial GPU Offload), and scores candidates based on your hardware profile and task.

    Examples:
      localpilot discover
      localpilot discover --refresh
      localpilot discover --all
      localpilot discover --task coding -r
      localpilot discover -q "qwen"
      localpilot discover -q "deepseek" --all
      localpilot discover --plain
    """
    _do_discover(task=task, query=query, refresh=refresh, all_models=all_models, plain=plain, json_output=json_output)


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
    current = getattr(localpilot, "__version__", "0.1.9")
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
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List installed model versions with pinning, size, and variant details."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))

    service = ModelVersionService()
    versions = service.list_versions(model_id=model_id, include_deleted=all)

    if json_output:
        data = [
            {
                "version_id": v.version_id,
                "logical_model_id": v.logical_model_id,
                "version_label": v.version_label,
                "quantization": v.quantization,
                "artifact_size_bytes": v.artifact_size_bytes,
                "is_pinned": v.is_pinned,
                "created_at": v.created_at,
                "installed_path": v.installed_path,
            }
            for v in versions
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    if not versions:
        if plain:
            print("No model versions found.")
        else:
            console.print("[yellow]No model versions found.[/yellow]")
        return

    if plain:
        print("VERSION_ID\tLOGICAL_MODEL\tTAG\tQUANT\tSIZE_MB\tPINNED\tCREATED_AT")
        for v in versions:
            size_mb = f"{v.artifact_size_bytes / (1024*1024):.1f}" if v.artifact_size_bytes else "-"
            print(f"{v.version_id}\t{v.logical_model_id}\t{v.version_label}\t{v.quantization or 'n/a'}\t{size_mb}\t{v.is_pinned}\t{v.created_at}")
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
def alias_list(
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List stable model aliases, target versions, and logical models."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))

    service = ModelVersionService()
    aliases = service.list_aliases()

    if json_output:
        data = [
            {
                "alias_name": a.alias_name,
                "version_id": a.version_id,
                "logical_model_id": a.logical_model_id,
                "is_system_alias": a.is_system_alias,
                "updated_at": a.updated_at,
            }
            for a in aliases
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    if not aliases:
        if plain:
            print("No aliases found.")
        else:
            console.print("[yellow]No aliases found.[/yellow]")
        return

    if plain:
        print("ALIAS\tTARGET_VERSION\tLOGICAL_MODEL\tSYSTEM\tUPDATED_AT")
        for a in aliases:
            print(f"{a.alias_name}\t{a.version_id}\t{a.logical_model_id or '-'}\t{a.is_system_alias}\t{a.updated_at}")
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
def keys_list(
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List all registered local API keys and applications."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))

    service = ApiKeyService()
    keys = service.list_keys()

    if json_output:
        data = [
            {
                "key_id": k.key_id,
                "display_name": k.display_name,
                "app_name": k.app_name,
                "prefix": k.secret_prefix,
                "is_active": k.is_active,
                "created_at": k.created_at,
                "expires_at": k.expires_at,
                "revoked_at": k.revoked_at,
                "policy": k.policy.model_dump() if k.policy else None,
            }
            for k in keys
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    if not keys:
        if plain:
            print("No API keys found.")
        else:
            console.print("[yellow]No API keys found.[/yellow]")
            console.print("Create one using [bold cyan]localpilot keys create <name>[/bold cyan].")
        return

    if plain:
        print("KEY_ID\tNAME\tAPP\tPREFIX\tSTATUS")
        for k in keys:
            status = "ACTIVE" if k.is_active else "REVOKED"
            print(f"{k.key_id}\t{k.display_name}\t{k.app_name or '-'}\t{k.secret_prefix}\t{status}")
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
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Create a new local API key with secure token generation."""
    json_output = bool(_val(json_output, False))
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

    if json_output:
        data = {
            "key_id": record.key_id,
            "display_name": record.display_name,
            "app_name": record.app_name,
            "prefix": record.secret_prefix,
            "token": raw_token,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
        }
        typer.echo(json.dumps(data, indent=2))
        return

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
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Revoke an API key immediately, blocking future access."""
    json_output = bool(_val(json_output, False))
    service = ApiKeyService()
    try:
        service.revoke_key(key_id)
        if json_output:
            typer.echo(json.dumps({"key_id": key_id, "status": "revoked", "success": True}, indent=2))
            return
        render_message_box("Key Revoked", f"API Key '{key_id}' has been permanently revoked.")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"key_id": key_id, "error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Revoke Failed", str(exc))
        raise typer.Exit(code=1)


@keys_app.command("rotate")
def keys_rotate(
    key_id: str = typer.Argument(..., help="API key ID to rotate"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Rotate secret token for an existing key, preserving its identity and policy."""
    json_output = bool(_val(json_output, False))
    service = ApiKeyService()
    try:
        raw_token, record = service.rotate_key(key_id)
        if json_output:
            data = {
                "key_id": record.key_id,
                "display_name": record.display_name,
                "prefix": record.secret_prefix,
                "token": raw_token,
                "success": True,
            }
            typer.echo(json.dumps(data, indent=2))
            return
        render_message_box("Key Rotated", f"API Key '{record.display_name}' ({key_id}) rotated successfully.")
        console.print(f"  [bold]Key ID:[/bold]          {record.key_id}")
        console.print(f"  [bold]New Safe Prefix:[/bold] {record.secret_prefix}")
        console.print(f"\n[bold yellow]New Plaintext API Token (copy now, will not be shown again):[/bold yellow]")
        console.print(f"  [bold cyan]{raw_token}[/bold cyan]\n")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"key_id": key_id, "error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Rotation Failed", str(exc))
        raise typer.Exit(code=1)


@keys_app.command("delete")
def keys_delete(
    key_id: str = typer.Argument(..., help="API key ID to delete"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Permanently delete an API key record."""
    json_output = bool(_val(json_output, False))
    service = ApiKeyService()
    service.delete_key(key_id)
    if json_output:
        typer.echo(json.dumps({"key_id": key_id, "deleted": True}, indent=2))
        return
    render_message_box("Key Deleted", f"API Key '{key_id}' removed from database.")


# =============================================================================
# Operational Statistics & Dashboards
# =============================================================================

@app.command("stats")
def app_stats(
    key_id: Optional[str] = typer.Option(None, "--key", "-k", help="Filter statistics by API key ID"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Filter statistics by model alias"),
    time_window: Optional[str] = typer.Option(None, "--time-window", "-w", help="Filter by time window: 1h, 24h, 7d, 30d, all"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text without rich tables"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Display real-time operational metrics, token usage, and latency summary."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))
    time_window = _val(time_window, None)

    acct = ApiAccountingService()
    summary = acct.get_usage_summary(key_id=key_id, model_alias=model, time_window=time_window)
    model_breakdown = acct.get_per_model_breakdown(time_window=time_window)
    key_breakdown = acct.get_per_key_breakdown(time_window=time_window)

    if json_output:
        data = {
            "summary": summary,
            "per_model": model_breakdown,
            "per_key": key_breakdown,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    if plain:
        print(f"Time Window: {summary.get('time_window', 'all')}")
        print(f"Total Requests: {summary['total_requests']}")
        print(f"Successful Requests: {summary['successful_requests']}")
        print(f"Error Requests: {summary['error_requests']}")
        print(f"Prompt Tokens: {summary['total_prompt_tokens']}")
        print(f"Completion Tokens: {summary['total_completion_tokens']}")
        print(f"Total Tokens: {summary['total_tokens']}")
        print(f"Average Latency (ms): {summary['avg_latency_ms']}")
        print(f"P50 Latency (ms): {summary.get('p50_latency_ms', 0.0)}")
        print(f"P95 Latency (ms): {summary.get('p95_latency_ms', 0.0)}")
        print(f"TTFT (ms): {summary['avg_first_token_latency_ms']}")
        print(f"Generation Speed (TPS): {summary['tokens_per_second']}")
        print(f"Token Reporting: {summary.get('token_reporting_status', 'none')}")
        return

    title_suffix = f" ({time_window})" if time_window else ""
    table = Table(title=f"Inference Gateway Operational Metrics{title_suffix}", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Requests", str(summary["total_requests"]))
    table.add_row("Successful Requests (200 OK)", f"[green]{summary['successful_requests']}[/green]")
    table.add_row("Error Requests", f"[red]{summary['error_requests']}[/red]" if summary["error_requests"] > 0 else "0")
    table.add_row("Prompt Tokens", f"{summary['total_prompt_tokens']:,}")
    table.add_row("Completion Tokens", f"{summary['total_completion_tokens']:,}")
    table.add_row("Total Tokens", f"[bold]{summary['total_tokens']:,}[/bold]")
    table.add_row("Average Latency (ms)", f"{summary['avg_latency_ms']:.1f}")
    table.add_row("Median Latency (p50 ms)", f"{summary.get('p50_latency_ms', 0.0):.1f}")
    table.add_row("95th Percentile (p95 ms)", f"{summary.get('p95_latency_ms', 0.0):.1f}")
    table.add_row("Time to First Content (TTFT ms)", f"{summary['avg_first_token_latency_ms']:.1f}")
    table.add_row("Measured Generation Speed (TPS)", f"{summary['tokens_per_second']:.1f} tokens/s")

    if summary.get("token_reporting_status") == "unreported":
        table.add_row("Token Usage Status", "[dim yellow]Unreported by runtime[/dim yellow]")
    elif summary.get("token_reporting_status") == "partial":
        table.add_row("Token Usage Status", f"[yellow]Partial ({summary.get('unreported_token_requests', 0)} requests unobservable)[/yellow]")

    console.print(table)

    # Per-Model breakdown
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
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview configuration changes without writing to disk"),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="Reuse existing API key ID instead of creating new key"),
    test: bool = typer.Option(False, "--test", "-t", help="Run 5-stage connection diagnostic test after creation"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text without rich formatting"),
) -> None:
    """Generate configuration and integration instructions for supported local apps."""
    write = bool(_val(write, False))
    dry_run = bool(_val(dry_run, False))
    test = bool(_val(test, False))
    json_output = bool(_val(json_output, False))
    plain = bool(_val(plain, False))
    target_app = str(_val(target_app, "opencode")).lower().strip()
    port = int(_val(port, 8080))
    model = str(_val(model, "coding"))
    key_id = _val(key, None)

    gateway_url = f"http://127.0.0.1:{port}/v1"
    mgr = ConnectionManager()
    is_preview = dry_run or not write

    try:
        raw_token, profile, diff_text = mgr.create_connection(
            name=f"{target_app.title()} Connection",
            client_type=target_app,
            model_alias=model,
            gateway_url=gateway_url,
            key_id=key_id,
            dry_run=is_preview,
        )
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Connection Setup Failed", str(exc))
        raise typer.Exit(code=1)

    verification_data = None
    if test and not is_preview:
        try:
            ver_res = mgr.test_connection(profile.connection_id)
            verification_data = ver_res.model_dump()
        except Exception:
            pass

    if json_output:
        res_data = {
            "success": True,
            "preview_only": is_preview,
            "connection": profile.model_dump(),
            "diff": diff_text,
            "api_key_secret": raw_token,
            "verification": verification_data,
        }
        typer.echo(json.dumps(res_data, indent=2))
        return

    # Rich / Plain interactive rendering
    console.print(f"\n[bold cyan]LocalPilot Guided Integration: {target_app.title()}[/bold cyan]")
    console.print(f"  [bold]Model Alias:[/bold]   {model}")
    console.print(f"  [bold]Gateway URL:[/bold]   {gateway_url}")
    console.print(f"  [bold]API Key ID:[/bold]    {profile.key_id}")
    if profile.target_path:
        console.print(f"  [bold]Target Config:[/bold] {profile.target_path}")

    if raw_token:
        console.print(f"\n[bold yellow]Plaintext API Token (copy now, will not be shown again):[/bold yellow]")
        console.print(f"  [bold cyan]{raw_token}[/bold cyan]")

    if diff_text:
        console.print(f"\n[bold]Configuration Preview (Redacted):[/bold]")
        console.print(diff_text)

    if is_preview:
        console.print(f"\n[bold yellow]Preview Mode Only[/bold yellow]")
        console.print(f"To apply and write these changes to disk, re-run with: [bold cyan]--write[/bold cyan]")
    else:
        console.print(f"\n[bold green]✓ Configuration successfully applied![/bold green]")
        if profile.backup_path:
            console.print(f"  [dim]Backup saved at: {profile.backup_path}[/dim]")

    if verification_data:
        console.print("\n[bold]Connection Diagnostic Verification:[/bold]")
        console.print(f"  Config Syntax:      {'✓' if verification_data.get('config_valid') else '✗'}")
        console.print(f"  Gateway Reachable:  {'✓' if verification_data.get('gateway_reachable') else '✗'}")
        console.print(f"  API Key Valid:      {'✓' if verification_data.get('key_valid') else '✗'}")
        console.print(f"  Model Authorized:   {'✓' if verification_data.get('model_authorized') else '✗'}")
        console.print(f"  Diagnostic Probe:   {'✓' if verification_data.get('probe_passed') else '✗'}")
        console.print(f"  [dim]Note: External client acceptance must be verified directly inside {target_app.title()}.[/dim]")


# =============================================================================
# Connections Management CLI
# =============================================================================

@connections_app.command("list")
def connections_list(
    plain: bool = typer.Option(False, "--plain", help="Output plain text without rich tables"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List all registered application connections."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))
    mgr = ConnectionManager()
    conns = mgr.list_connections()

    if json_output:
        typer.echo(json.dumps([c.model_dump() for c in conns], indent=2))
        return

    if plain:
        for c in conns:
            typer.echo(f"{c.connection_id}\t{c.name}\t{c.client_type}\t{c.model_alias}\t{c.status.value}\t{c.target_path or 'none'}")
        return

    table = Table(title="Connected Applications", show_lines=True)
    table.add_column("Connection ID", style="cyan")
    table.add_column("Name")
    table.add_column("Client Type")
    table.add_column("Model Alias")
    table.add_column("Key ID", style="dim")
    table.add_column("Status")
    table.add_column("Config Path", style="dim")

    for c in conns:
        status_style = "green" if c.status.value == "active" else "red"
        table.add_row(
            c.connection_id,
            c.name,
            c.client_type,
            c.model_alias,
            c.key_id,
            f"[{status_style}]{c.status.value}[/{status_style}]",
            str(c.target_path or "N/A"),
        )
    console.print(table)


@connections_app.command("test")
def connections_test(
    connection_id: str = typer.Argument(..., help="Connection ID to verify"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run 5-stage diagnostic verification test on an application connection."""
    json_output = bool(_val(json_output, False))
    mgr = ConnectionManager()
    try:
        res = mgr.test_connection(connection_id)
        if json_output:
            typer.echo(json.dumps(res.model_dump(), indent=2))
            return

        console.print(f"\n[bold cyan]Diagnostic Verification: {connection_id}[/bold cyan]")
        console.print(f"  1. Config Schema/Syntax:  {'[green]✓ Valid[/green]' if res.config_valid else '[red]✗ Failed[/red]'}")
        console.print(f"  2. Gateway Reachable:     {'[green]✓ Reachable[/green]' if res.gateway_reachable else '[red]✗ Unreachable[/red]'}")
        console.print(f"  3. API Key Active:        {'[green]✓ Active[/green]' if res.key_valid else '[red]✗ Inactive/Revoked[/red]'}")
        console.print(f"  4. Model Authorized:      {'[green]✓ Authorized[/green]' if res.model_authorized else '[red]✗ Unauthorized[/red]'}")
        console.print(f"  5. Diagnostic Probe:      {'[green]✓ Passed[/green]' if res.probe_passed else '[red]✗ Incomplete[/red]'}")
        console.print(f"\n  [dim]Client Application Acceptance:[/dim] External editor verification required.")
        for k, v in res.details.items():
            console.print(f"    - [dim]{k}:[/dim] {v}")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Test Failed", str(exc))
        raise typer.Exit(code=1)


@connections_app.command("disconnect")
def connections_disconnect(
    connection_id: str = typer.Argument(..., help="Connection ID to disconnect"),
    revoke_key: bool = typer.Option(False, "--revoke-key", help="Also permanently revoke the associated API key"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Safely restore client configuration and disconnect application."""
    revoke_key = bool(_val(revoke_key, False))
    json_output = bool(_val(json_output, False))
    mgr = ConnectionManager()
    try:
        res = mgr.disconnect_connection(connection_id, revoke_key=revoke_key)
        if json_output:
            typer.echo(json.dumps(res, indent=2))
            return
        render_message_box(
            "Application Disconnected",
            f"Connection '{connection_id}' has been disconnected.\nAction taken: {res['action_taken']}\nKey revoked: {res['key_revoked']}",
        )
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Disconnect Failed", str(exc))
        raise typer.Exit(code=1)


# =============================================================================
# Automatic Startup Service CLI
# =============================================================================

@startup_app.command("status")
def startup_status(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Check automatic login startup service status."""
    json_output = bool(_val(json_output, False))
    svc = StartupService()
    status = svc.get_status()

    if json_output:
        typer.echo(json.dumps(status, indent=2))
        return

    console.print("\n[bold cyan]LocalPilot Automatic Startup Status[/bold cyan]")
    console.print(f"  [bold]Platform:[/bold]     {status['platform']}")
    console.print(f"  [bold]Installed:[/bold]    {'[green]Yes[/green]' if status['installed'] else '[yellow]No[/yellow]'}")
    console.print(f"  [bold]Enabled:[/bold]      {'[green]Yes[/green]' if status['enabled'] else '[dim]No[/dim]'}")
    console.print(f"  [bold]Active:[/bold]       {'[green]Yes[/green]' if status['active'] else '[dim]No[/dim]'}")
    console.print(f"  [bold]Target Path:[/bold]  {status['path']}")
    console.print(f"  [dim]{status['message']}[/dim]\n")


@startup_app.command("preview")
def startup_preview(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Preview user-level init service configuration without writing to disk."""
    json_output = bool(_val(json_output, False))
    svc = StartupService()
    prev = svc.preview()

    if json_output:
        typer.echo(json.dumps(prev, indent=2))
        return

    console.print(f"\n[bold cyan]Startup Service Definition Preview ({prev['platform']})[/bold cyan]")
    console.print(f"[dim]Target file: {prev['target_path']}[/dim]\n")
    console.print(prev["content"])


@startup_app.command("enable")
def startup_enable(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Enable automatic gateway startup on user login."""
    json_output = bool(_val(json_output, False))
    svc = StartupService()
    try:
        res = svc.enable()
        if json_output:
            typer.echo(json.dumps(res, indent=2))
            return
        render_message_box(
            "Startup Enabled",
            f"User startup service enabled for {res['platform']}.\nUnit path: {res['path']}\nStatus: {res.get('system_output') or 'Success'}",
        )
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Startup Enable Failed", str(exc))
        raise typer.Exit(code=1)


@startup_app.command("disable")
def startup_disable(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Disable and remove automatic gateway startup."""
    json_output = bool(_val(json_output, False))
    svc = StartupService()
    res = svc.disable()
    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return
    render_message_box("Startup Disabled", f"Automatic startup disabled. Removed {res['path']}.")


# =============================================================================
# Lifecycle Hooks CLI
# =============================================================================

@hooks_app.command("list")
def hooks_list(
    plain: bool = typer.Option(False, "--plain", help="Output plain text without rich tables"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List registered lifecycle event hooks."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))
    svc = LifecycleHookService()
    hooks = svc.list_hooks()

    if json_output:
        typer.echo(json.dumps([h.model_dump() for h in hooks], indent=2))
        return

    if plain:
        for h in hooks:
            typer.echo(f"{h.hook_id}\t{h.name}\t{h.event_type}\t{'enabled' if h.is_enabled else 'disabled'}\t{'tripped' if h.is_tripped else 'ok'}")
        return

    table = Table(title="Lifecycle Hooks", show_lines=True)
    table.add_column("Hook ID", style="cyan")
    table.add_column("Name")
    table.add_column("Event Type")
    table.add_column("Command", style="dim")
    table.add_column("Enabled")
    table.add_column("Approved")
    table.add_column("Failures")
    table.add_column("Circuit Breaker")

    for h in hooks:
        tripped_style = "[red]TRIPPED[/red]" if h.is_tripped else "[green]NORMAL[/green]"
        table.add_row(
            h.hook_id,
            h.name,
            h.event_type,
            " ".join(h.command),
            "[green]Yes[/green]" if h.is_enabled else "[dim]No[/dim]",
            "[green]Yes[/green]" if h.approved_by_user else "[yellow]No[/yellow]",
            str(h.consecutive_failures),
            tripped_style,
        )
    console.print(table)


@hooks_app.command("inspect")
def hooks_inspect(
    hook_id: str = typer.Argument(..., help="Hook ID to inspect"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Inspect hook configuration, circuit breaker state, and recent execution runs."""
    json_output = bool(_val(json_output, False))
    svc = LifecycleHookService()
    hook = svc.get_hook(hook_id)
    if not hook:
        if json_output:
            typer.echo(json.dumps({"error": f"Hook '{hook_id}' not found", "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Hook Not Found", f"No hook with ID '{hook_id}'.")
        raise typer.Exit(code=1)

    runs = svc.list_runs(hook_id=hook_id, limit=10)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "hook": hook.model_dump(),
                    "recent_runs": [r.model_dump() for r in runs],
                },
                indent=2,
            )
        )
        return

    console.print(f"\n[bold cyan]Hook Inspection: {hook.name} ({hook.hook_id})[/bold cyan]")
    console.print(f"  [bold]Event Type:[/bold]      {hook.event_type}")
    console.print(f"  [bold]Command:[/bold]         {' '.join(hook.command)}")
    console.print(f"  [bold]Enabled:[/bold]         {'Yes' if hook.is_enabled else 'No'}")
    console.print(f"  [bold]Approved:[/bold]        {'Yes' if hook.approved_by_user else 'No'}")
    console.print(f"  [bold]Failures:[/bold]        {hook.consecutive_failures}")
    console.print(f"  [bold]Circuit Breaker:[/bold] {'[red]TRIPPED[/red]' if hook.is_tripped else '[green]NORMAL[/green]'}")

    console.print("\n[bold]Recent Invocations (last 10):[/bold]")
    if not runs:
        console.print("  [dim]No recorded runs yet.[/dim]")
    else:
        for r in runs:
            status_color = "green" if r.status == "success" else "red"
            console.print(f"  - [{status_color}]{r.status}[/{status_color}] at {r.triggered_at} ({r.duration_ms:.1f}ms): {r.output_summary}")


@hooks_app.command("enable")
def hooks_enable(
    hook_id: str = typer.Argument(..., help="Hook ID to enable"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Enable a lifecycle hook (requires user confirmation/approval)."""
    json_output = bool(_val(json_output, False))
    svc = LifecycleHookService()
    try:
        hook = svc.enable_hook(hook_id, approved=True)
        if json_output:
            typer.echo(json.dumps(hook.model_dump(), indent=2))
            return
        render_message_box("Hook Enabled", f"Lifecycle hook '{hook.name}' ({hook_id}) is now enabled.")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Enable Failed", str(exc))
        raise typer.Exit(code=1)


@hooks_app.command("disable")
def hooks_disable(
    hook_id: str = typer.Argument(..., help="Hook ID to disable"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Disable a lifecycle hook."""
    json_output = bool(_val(json_output, False))
    svc = LifecycleHookService()
    try:
        hook = svc.disable_hook(hook_id)
        if json_output:
            typer.echo(json.dumps(hook.model_dump(), indent=2))
            return
        render_message_box("Hook Disabled", f"Lifecycle hook '{hook.name}' ({hook_id}) disabled.")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Disable Failed", str(exc))
        raise typer.Exit(code=1)


@hooks_app.command("test")
def hooks_test(
    hook_id: str = typer.Argument(..., help="Hook ID to test with simulated event"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Test hook execution with a synthetic event payload."""
    json_output = bool(_val(json_output, False))
    svc = LifecycleHookService()
    hook = svc.get_hook(hook_id)
    if not hook:
        if json_output:
            typer.echo(json.dumps({"error": f"Hook '{hook_id}' not found", "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Hook Not Found", f"No hook with ID '{hook_id}'.")
        raise typer.Exit(code=1)

    event = HookEvent(
        event_type=hook.event_type if hook.event_type != "all" else "gateway.started",
        data={"test": True, "source": "cli_test"},
    )
    res = svc.execute_hook(hook, event)

    if json_output:
        typer.echo(json.dumps(res.model_dump(), indent=2))
        return

    status_color = "green" if res.status == "success" else "red"
    console.print(f"\n[bold cyan]Hook Test Result: {hook.name}[/bold cyan]")
    console.print(f"  [bold]Status:[/bold]   [{status_color}]{res.status}[/{status_color}]")
    console.print(f"  [bold]Exit Code:[/bold] {res.exit_code}")
    console.print(f"  [bold]Duration:[/bold]  {res.duration_ms:.1f}ms")
    console.print(f"  [bold]Output:[/bold]    {res.output_summary}\n")


@hooks_app.command("reset-circuit")
def hooks_reset_circuit(
    hook_id: str = typer.Argument(..., help="Hook ID to reset circuit breaker"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Reset tripped circuit breaker for a hook."""
    json_output = bool(_val(json_output, False))
    svc = LifecycleHookService()
    try:
        hook = svc.reset_circuit_breaker(hook_id)
        if json_output:
            typer.echo(json.dumps(hook.model_dump(), indent=2))
            return
        render_message_box("Circuit Breaker Reset", f"Circuit breaker reset for hook '{hook.name}' ({hook_id}).")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Reset Failed", str(exc))
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()



