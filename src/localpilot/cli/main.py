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
from localpilot.application.diagnostic_service import DiagnosticService
from localpilot.application.benchmark_service import BenchmarkService
from localpilot.application.task_service import TaskService
from localpilot.application.search_service import SearchService
from localpilot.application.voice_service import VoiceService
from localpilot.application.hook_service import LifecycleHookService
from localpilot.application.version_service import ModelVersionService
from localpilot.domain.api_contract import HookEvent
from localpilot.gateway.server import GatewayServer
from localpilot.application.web_service import WebServer, get_web_dir
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

favorite_app = typer.Typer(help="Manage favorite models for quick discovery and access.")
app.add_typer(favorite_app, name="favorite")
app.add_typer(favorite_app, name="fav")

bench_app = typer.Typer(help="Benchmark and compare local model latency, throughput, and memory.")
app.add_typer(bench_app, name="bench")

tools_app = typer.Typer(help="Discover and execute task-specific local AI tools.")
app.add_typer(tools_app, name="tools")

search_app = typer.Typer(help="Index local documents and answer questions with citations (offline RAG).")
app.add_typer(search_app, name="search")

voice_app = typer.Typer(help="Speech-to-text, text-to-speech, and voice interaction.")
app.add_typer(voice_app, name="voice")

catalog_app = typer.Typer(help="Manage and inspect initial model databases, sources, providers, and developers.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(catalog_app, name="db")

web_app = typer.Typer(help="Serve, open, and inspect the LocalPilot documentation and showcase website from /web.")
app.add_typer(web_app, name="web")



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
    favorites: bool = False,
    plain: bool = False,
    json_output: bool = False,
    capability: Optional[str] = None,
    provider: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    task_val = _val(task, None)
    query_val = str(_val(query, ""))
    refresh_val = bool(_val(refresh, False))
    all_val = bool(_val(all_models, False))
    fav_val = bool(_val(favorites, False))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))
    cap_val = _val(capability, None)
    prov_val = _val(provider, None)
    src_val = _val(source, None)

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
    caps_list = [cap_val.lower().strip()] if cap_val else None
    results = cat_svc.discover(
        task=task_cat,
        query=query_val,
        only_compatible=not all_val,
        only_favorites=fav_val,
        capabilities=caps_list,
        provider=prov_val,
        source=src_val,
    )

    if not results and not json_val:
        if fav_val:
            console.print("[bold yellow]No favorite models found. Add one with: localpilot favorite add <alias>[/bold yellow]")
        else:
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
            fav_str = " ★" if r.is_favorite else ""
            alias_str = cand.short_alias or cand.display_alias
            caps_str = getattr(cand, "capabilities_table_badge", "General")
            prov_str = cand.publisher_display
            off_str = f"Official ({prov_str})" if cand.is_official else f"Community ({prov_str})"
            rel_str = cand.release_date_display
            print(f"{cand.name}{fav_str} | Alias: {alias_str} | Caps: {caps_str} | Provider: {prov_str} | Released: {rel_str} | Resource: {off_str} | Source: {cand.source} | Size: {sz_mb:.0f}MB | Score: {r.score.total_score} | Fit: {compat_str} | ID: {cand.candidate_id}")
        return

    title = "Favorite Models" if fav_val else ("All Catalog Models" if all_val else "Compatible Models for Your Computer")
    table = Table(title=title, border_style="cyan")
    table.add_column("Score", style="bold green", justify="right")
    table.add_column("Model Name", style="bold")
    table.add_column("Short Alias", style="bold cyan")
    table.add_column("Capabilities", style="bold magenta")
    table.add_column("Provider", style="bold")
    table.add_column("Source")
    table.add_column("Released", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Context", justify="right")
    table.add_column("Fit Status")
    table.add_column("Candidate ID", style="dim")

    for r in results:
        cand = r.candidate
        sz_mb = cand.total_bytes / (1024 * 1024)
        score_styled = f"{r.score.total_score:.0f}" if r.compatibility.is_compatible else f"[dim yellow]{r.score.total_score:.0f}[/dim yellow]"
        name_styled = f"[yellow]★[/yellow] {cand.name}" if r.is_favorite else cand.name
        alias_str = cand.short_alias or cand.display_alias
        caps_str = getattr(cand, "capabilities_table_badge", "General")
        prov_name = cand.publisher_display
        if cand.is_official:
            prov_styled = f"[bold green]✓ {prov_name}[/bold green]"
        else:
            prov_styled = f"[dim]Community ({prov_name})[/dim]"
        rel_str = cand.release_date_display
        table.add_row(
            score_styled,
            name_styled,
            alias_str,
            caps_str,
            prov_styled,
            cand.source.title(),
            rel_str,
            f"{sz_mb:.0f} MB",
            str(cand.usable_context),
            r.compatibility.friendly_explanation,
            cand.candidate_id,
        )

    console.print(table)
    console.print("\nTo set up a model, run: [cyan]localpilot setup <alias>[/cyan] (e.g. [cyan]localpilot setup gemma2:2b[/cyan])")
    console.print("To manage favorites: [cyan]localpilot favorite add <alias>[/cyan] or press [bold]F[/bold] in TUI")
    console.print("[dim]Note: Models from official resources are verified from original creators, marked with [bold green]✓ Official[/bold green], and listed first.[/dim]\n")


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


def _resolve_installation_id(id_or_alias: str) -> str:
    db = get_db()
    conn = db.get_connection()
    # 1. Exact installation_id
    row = conn.execute("SELECT installation_id FROM installations WHERE installation_id = ?", (id_or_alias,)).fetchone()
    if row:
        return row["installation_id"]

    target = id_or_alias.lower().strip()
    # 2. Case-insensitive installation_id, name, or model_id
    rows = conn.execute("SELECT installation_id, model_id, name FROM installations").fetchall()
    for r in rows:
        if r["installation_id"].lower() == target or r["name"].lower() == target or r["model_id"].lower() == target:
            return r["installation_id"]

    # 3. Via model_aliases
    alias_row = conn.execute("SELECT target_version_id, logical_model_id FROM model_aliases WHERE alias_name = ?", (target,)).fetchone()
    if alias_row:
        tgt_ver = alias_row["target_version_id"]
        if tgt_ver:
            ver_row = conn.execute("SELECT installation_id FROM model_versions WHERE version_id = ?", (tgt_ver,)).fetchone()
            if ver_row and ver_row["installation_id"]:
                return ver_row["installation_id"]
        if alias_row["logical_model_id"]:
            row = conn.execute("SELECT installation_id FROM installations WHERE model_id = ?", (alias_row["logical_model_id"],)).fetchone()
            if row:
                return row["installation_id"]

    # 4. Via catalog candidate
    try:
        cand = CatalogService().get_candidate(id_or_alias)
        if cand:
            for r in rows:
                if r["model_id"].lower() == cand.model_id.lower() or r["name"].lower() == cand.name.lower():
                    return r["installation_id"]
    except Exception:
        pass

    return id_or_alias


def _do_update(installation_id: str, yes: bool = False) -> None:
    installation_id = _resolve_installation_id(installation_id)
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
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias to diagnose"),
    connection: Optional[str] = typer.Option(None, "--connection", "-c", help="Target connection ID to diagnose"),
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="Target tool ID to diagnose"),
    repair: bool = typer.Option(False, "--repair", help="Review and apply recommended repairs"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Automatically accept non-destructive repairs"),
    plain: bool = typer.Option(False, "--plain", help="Print in plain text format without colors or borders"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON diagnostic data"),
) -> None:
    """Check computer hardware, available memory budget, GPUs, runtimes, models, and apply repairs.

    Non-technical: Tells you immediately what your computer is capable of running safely without freezing or crashing.
    Technical: Probes OS, CPU architectures/AVX flags, physical/logical cores, unified vs discrete RAM budgets, CUDA/Metal GPUs, and existing runtime processes/binaries.

    Examples:
      localpilot doctor
      localpilot doctor --model llama3
      localpilot doctor --repair
      localpilot doctor --plain
      localpilot doctor --json
    """
    model_alias = _val(model, None)
    conn_id = _val(connection, None)
    tool_id = _val(tool, None)
    repair_flag = bool(_val(repair, False))
    auto_yes = bool(_val(yes, False))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    diag_svc = DiagnosticService()
    findings = []
    category_title = "System & Hardware Health"

    if model_alias:
        category_title = f"Model Diagnostics: {model_alias}"
        findings = diag_svc.check_model_diagnostics(model_alias)
    elif conn_id:
        category_title = f"Connection Diagnostics: {conn_id}"
        findings = diag_svc.check_connection_diagnostics(conn_id)
    elif tool_id:
        category_title = f"Tool Diagnostics: {tool_id}"
        findings = diag_svc.check_tool_diagnostics(tool_id)
    else:
        if not json_val:
            _do_doctor(plain=plain_val, json_output=False)
        findings = diag_svc.run_general_diagnostics()

    repairs_executed = []
    if repair_flag:
        for f in findings:
            if f.can_repair:
                plan = diag_svc.get_repair_plan(f)
                if plan:
                    should_apply = auto_yes
                    if not should_apply and not json_val and not plain_val:
                        should_apply = typer.confirm(f"Apply repair: {plan.description}?", default=True)
                    elif not should_apply:
                        should_apply = True  # In plain/json non-interactive mode with --repair, proceed
                    if should_apply:
                        res = diag_svc.execute_repair(plan)
                        repairs_executed.append(res.model_dump())

    if json_val:
        output_payload = {
            "category": category_title,
            "findings": [f.model_dump() for f in findings],
            "repairs_executed": repairs_executed,
        }
        if not (model_alias or conn_id or tool_id):
            report = SystemInspector().inspect()
            output_payload["hardware"] = json.loads(report.hardware.model_dump_json())
            output_payload["discovered_runtimes"] = [json.loads(r.model_dump_json()) for r in report.discovered_runtimes]
        typer.echo(json.dumps(output_payload, indent=2))
        return

    if plain_val:
        print(f"\n=== {category_title} ===")
        for f in findings:
            print(f"[{f.severity.value.upper()}] {f.title}: {f.message}")
            if f.remediation_suggestion:
                print(f"  Suggestion: {f.remediation_suggestion}")
        if repairs_executed:
            print("\n=== Repairs Applied ===")
            for r in repairs_executed:
                print(f"- {r['message']} (Success: {r['success']})")
        return

    table = Table(title=f"Diagnostic Findings: {category_title}", border_style="cyan")
    table.add_column("Severity", style="bold")
    table.add_column("Finding")
    table.add_column("Details")
    table.add_column("Action / Repair")

    for f in findings:
        sev_style = "green" if f.severity.value == "info" else ("yellow" if f.severity.value == "warning" else "red")
        repair_text = f"[bold green]Available[/bold green] ({f.repair_action})" if f.can_repair else "[dim]None[/dim]"
        table.add_row(
            f"[{sev_style}]{f.severity.value.upper()}[/{sev_style}]",
            f.title,
            f"{f.message}\n[dim]{f.remediation_suggestion or ''}[/dim]",
            repair_text,
        )
    console.print(table)

    if repairs_executed:
        console.print("\n[bold green]Repairs Successfully Applied:[/bold green]")
        for r in repairs_executed:
            console.print(f"  ✓ {r['message']}")
    elif any(f.can_repair for f in findings) and not repair_flag:
        console.print("\n[yellow]💡 Potential repairs detected. Run with [bold cyan]--repair[/bold cyan] to review and apply.[/yellow]")


@app.command("discover")
def discover_models(
    task: Optional[str] = typer.Option(None, "--task", help="Filter by task category: chat, coding"),
    capability: Optional[str] = typer.Option(None, "--capability", "-c", help="Filter by capability: coding, writing, image, audio, video, reasoning"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Filter by model creator / provider (e.g. nvidia, cohere, meta, google, deepseek, mistral, microsoft, allenai, etc.)"),
    source: Optional[str] = typer.Option(None, "--source", "-s", help="Filter by model registry source: huggingface, ollama, modelscope"),
    query: str = typer.Option("", "--query", "-q", help="Search text matching name, family, or tags"),
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Fetch latest models from active sources (Hugging Face Hub, Ollama)"),
    all_models: bool = typer.Option(False, "--all", "-a", help="Show all catalog models, including those requiring more RAM than currently available"),
    favorites: bool = typer.Option(False, "--favorites", "-f", help="Show only favorited models"),
    plain: bool = typer.Option(False, "--plain", help="Print plain tabular text without rich styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON candidate models"),
) -> None:
    """Find and score models that are hardware-compatible with your computer.

    Non-technical: Lists models you can actually run. Red/incompatible models are filtered out so you never waste bandwidth or download something that won't work.
    Technical: Evaluates 3-axis matrix (CPU/GPU/RAM) against catalog candidates, computes memory fit (Exact Fit, Comfortable, Tight, Partial GPU Offload), and scores candidates based on your hardware profile and task.

    Examples:
      localpilot discover
      localpilot discover --provider nvidia
      localpilot discover --provider cohere
      localpilot discover --capability coding
      localpilot discover --source ollama
      localpilot discover --favorites
      localpilot discover --refresh
      localpilot discover --all
      localpilot discover --task coding -r
      localpilot discover -q "qwen"
      localpilot discover -q "deepseek" --all
      localpilot discover --plain
    """
    _do_discover(
        task=task,
        query=query,
        refresh=refresh,
        all_models=all_models,
        favorites=favorites,
        plain=plain,
        json_output=json_output,
        capability=capability,
        provider=provider,
        source=source,
    )


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
    installation_id: str = typer.Argument(..., help="Installation ID, model alias, or name to check/update (e.g. 'gemma2:2b', 'qwen2.5-coder:1.5b')"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt and apply updates immediately"),
) -> None:
    """Check for model updates or upstream weight refreshes.

    Non-technical: Checks if the model author published a fix or newer revision of the weights and updates safely.
    Technical: Queries HuggingFace or Ollama registry for updated manifests/hashes; reports honest status (up-to-date, update-available, or service-managed).

    Examples:
      localpilot update gemma2:2b
      localpilot update qwen2.5-coder:1.5b
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
    candidate_id: Optional[str] = typer.Argument(None, help="Candidate ID or alias to install (e.g. 'gemma2:2b', 'qwen2.5-coder:1.5b', 'llama3.2:3b')"),
    scope: str = typer.Option("isolated", "--scope", help="Installation scope: isolated, user, system"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Accept execution plan without confirmation prompt"),
) -> None:
    """Guided, verified installation of a model onto your computer.

    Non-technical: Downloads the real model weights, verifies the download checksum, starts the engine, and performs an actual test question before declaring success.
    Technical: Generates an execution plan, provisions required runtime binaries into isolated or user scope, pulls GGUF or Ollama models, executes probe inference, and registers verified state.

    Examples:
      localpilot setup
      localpilot setup gemma2:2b
      localpilot setup qwen2.5-coder:1.5b --yes
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
    current = getattr(localpilot, "__version__", "0.2.0")
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
    version_id: str = typer.Argument(..., help="Target model version ID, candidate alias, or installed model name"),
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


@alias_app.command("add")
def alias_add(
    alias_name: str = typer.Argument(..., help="Alias name (e.g. 'writing', 'coding', 'default', 'myfastmodel')"),
    target: str = typer.Argument(..., help="Target model version ID, candidate alias, or installed model name"),
    reason: str = typer.Option("Manual activation", "--reason", "-r", help="Reason for activation"),
) -> None:
    """Add or point a friendly alias to a target model (alias for 'alias set')."""
    alias_set(alias_name=alias_name, version_id=target, reason=reason)


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
    target_app: Optional[str] = typer.Argument(None, help="Application to connect: opencode, continue, cursor, or generic"),
    port: int = typer.Option(8080, "--port", "-p", help="Gateway port"),
    model: str = typer.Option("coding", "--model", "-m", help="Target model alias to connect"),
    write: bool = typer.Option(False, "--write", "-w", help="Automatically write configuration file if supported"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview configuration changes without writing to disk"),
    preview: bool = typer.Option(False, "--preview", help="Preview configuration changes without writing to disk"),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="Reuse existing API key ID instead of creating new key"),
    test: bool = typer.Option(False, "--test", "-t", help="Run 5-stage connection diagnostic test after creation"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text without rich formatting"),
) -> None:
    """Generate configuration and integration instructions for supported local apps."""
    write = bool(_val(write, False))
    dry_run = bool(_val(dry_run, False))
    preview_val = bool(_val(preview, False))
    test = bool(_val(test, False))
    json_output = bool(_val(json_output, False))
    plain = bool(_val(plain, False))
    port = int(_val(port, 8080))
    model = str(_val(model, "coding"))
    key_id = _val(key, None)

    target_app_raw = _val(target_app, None)
    if not target_app_raw:
        if not plain and not json_output:
            console.print("\n[bold cyan]LocalPilot Application Connection Wizard[/bold cyan]")
            console.print("Supported clients: [bold]opencode[/bold], [bold]continue[/bold], [bold]cursor[/bold], [bold]generic[/bold]")
            target_app_raw = typer.prompt("Select application to connect", default="opencode")
        else:
            target_app_raw = "opencode"

    target_app = str(target_app_raw).lower().strip()
    gateway_url = f"http://127.0.0.1:{port}/v1"
    mgr = ConnectionManager()
    is_preview = dry_run or preview_val or not write

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
        console.print(f"  Config Syntax:      {'[green]✓ Valid[/green]' if verification_data.get('config_valid') else '[red]✗ Failed[/red]'}")
        console.print(f"  Gateway Reachable:  {'[green]✓ Reachable[/green]' if verification_data.get('gateway_reachable') else '[red]✗ Unreachable[/red]'}")
        console.print(f"  API Key Valid:      {'[green]✓ Active[/green]' if verification_data.get('key_valid') else '[red]✗ Inactive/Revoked[/red]'}")
        console.print(f"  Model Authorized:   {'[green]✓ Authorized[/green]' if verification_data.get('model_authorized') else '[red]✗ Unauthorized[/red]'}")
        console.print(f"  Diagnostic Probe:   {'[green]✓ Passed[/green]' if verification_data.get('probe_passed') else '[red]✗ Incomplete[/red]'}")
        console.print(f"  [dim]Note: External client acceptance must be verified directly inside {target_app.title()}.[/dim]")
    elif not is_preview:
        console.print("\n[dim]Diagnostic verification was not run. Pass --test to execute verification now.[/dim]")


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


@connections_app.command("restore")
def connections_restore(
    connection_id: str = typer.Argument(..., help="Connection ID to restore"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Restore client configuration from original backup file."""
    json_output = bool(_val(json_output, False))
    mgr = ConnectionManager()
    try:
        res = mgr.restore_connection(connection_id)
        if json_output:
            typer.echo(json.dumps(res, indent=2))
            return
        render_message_box(
            "Configuration Restored",
            f"Connection '{connection_id}' was restored successfully.\nRestored from: {res.get('restored_from')}\nRestored to: {res.get('restored_to')}",
        )
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Restore Failed", str(exc))
        raise typer.Exit(code=1)


@connections_app.command("remove")
def connections_remove(
    connection_id: str = typer.Argument(..., help="Connection ID to remove"),
    revoke_key: bool = typer.Option(False, "--revoke-key", help="Also revoke associated API key"),
    delete_backup: bool = typer.Option(False, "--delete-backup", help="Also delete configuration backup file"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Remove a connection record from LocalPilot."""
    revoke_key = bool(_val(revoke_key, False))
    delete_backup = bool(_val(delete_backup, False))
    json_output = bool(_val(json_output, False))
    mgr = ConnectionManager()
    try:
        res = mgr.remove_connection(connection_id, revoke_key=revoke_key, delete_backup=delete_backup)
        if json_output:
            typer.echo(json.dumps(res, indent=2))
            return
        render_message_box(
            "Connection Removed",
            f"Connection profile '{connection_id}' removed.\nKey revoked: {res['key_revoked']}\nBackup deleted: {res['backup_deleted']}",
        )
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Remove Failed", str(exc))
        raise typer.Exit(code=1)


@connections_app.command("inspect")
def connections_inspect(
    connection_id: str = typer.Argument(..., help="Connection ID to inspect"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Inspect detailed configuration and status of a connection."""
    json_output = bool(_val(json_output, False))
    mgr = ConnectionManager()
    conn = mgr.get_connection(connection_id)
    if not conn:
        if json_output:
            typer.echo(json.dumps({"error": f"Connection '{connection_id}' not found", "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Inspect Failed", f"Connection '{connection_id}' not found.")
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(conn.model_dump(), indent=2))
        return

    console.print(f"\n[bold cyan]Connection Profile: {conn.connection_id}[/bold cyan]")
    console.print(f"  [bold]Name:[/bold]         {conn.name}")
    console.print(f"  [bold]Client Type:[/bold]  {conn.client_type}")
    console.print(f"  [bold]Model Alias:[/bold]  {conn.model_alias}")
    console.print(f"  [bold]Status:[/bold]       {conn.status.value}")
    console.print(f"  [bold]Gateway URL:[/bold]  {conn.gateway_url}")
    console.print(f"  [bold]Key ID:[/bold]       {conn.key_id}")
    console.print(f"  [bold]Config Path:[/bold]  {conn.target_path or 'N/A'}")
    console.print(f"  [bold]Backup Path:[/bold]  {conn.backup_path or 'N/A'}")
    console.print(f"  [bold]Created At:[/bold]   {conn.created_at}")
    console.print(f"  [bold]Updated At:[/bold]   {conn.updated_at}\n")


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


@favorite_app.command("list")
def favorite_list(
    plain: bool = typer.Option(False, "--plain", help="Print plain text without styling"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON favorites list"),
) -> None:
    """List all favorited models."""
    plain = bool(_val(plain, False))
    json_output = bool(_val(json_output, False))
    cat_svc = CatalogService()
    favs = cat_svc.list_favorites_detailed()
    if json_output:
        typer.echo(json.dumps(favs, indent=2))
        return

    if not favs:
        if plain:
            print("No favorite models yet.")
        else:
            console.print("[dim]No favorite models yet. Star a model with [cyan]localpilot favorite add <alias>[/cyan] or press [bold]F[/bold] in TUI.[/dim]")
        return

    if plain:
        for f in favs:
            print(f"{f['model_ref']}\t{f['created_at']}\t{f['notes'] or ''}")
        return

    table = Table(title="Favorite Models", border_style="yellow")
    table.add_column("★", style="bold yellow")
    table.add_column("Model Reference / Alias", style="bold cyan")
    table.add_column("Added At", style="dim")
    table.add_column("Notes", style="dim")

    for f in favs:
        table.add_row("★", f["model_ref"], str(f["created_at"])[:19], f["notes"] or "-")

    console.print(table)


@favorite_app.command("add")
def favorite_add(
    model_ref: str = typer.Argument(..., help="Model alias, candidate ID, or name to add to favorites"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Optional user notes"),
) -> None:
    """Add a model to favorites."""
    cat_svc = CatalogService()
    cand = cat_svc.get_candidate(model_ref)
    ref = cand.short_alias if cand and cand.short_alias else (cand.candidate_id if cand else model_ref)
    cat_svc.add_favorite(ref, notes=notes)
    render_message_box("Favorite Added", f"★ Added '[bold green]{ref}[/bold green]' to your favorites.")


@favorite_app.command("remove")
def favorite_remove(
    model_ref: str = typer.Argument(..., help="Model alias, candidate ID, or name to remove from favorites"),
) -> None:
    """Remove a model from favorites."""
    cat_svc = CatalogService()
    cand = cat_svc.get_candidate(model_ref)
    ref = cand.short_alias if cand and cand.short_alias else (cand.candidate_id if cand else model_ref)
    cat_svc.remove_favorite(ref)
    if ref != model_ref:
        cat_svc.remove_favorite(model_ref)
    render_message_box("Favorite Removed", f"Removed '[yellow]{model_ref}[/yellow]' from your favorites.")


@favorite_app.command("toggle")
def favorite_toggle(
    model_ref: str = typer.Argument(..., help="Model alias or candidate ID to toggle favorite status"),
) -> None:
    """Toggle favorite status for a model."""
    cat_svc = CatalogService()
    cand = cat_svc.get_candidate(model_ref)
    ref = cand.short_alias if cand and cand.short_alias else (cand.candidate_id if cand else model_ref)
    is_fav = cat_svc.toggle_favorite(ref)
    if is_fav:
        render_message_box("Favorite Added", f"★ Added '[bold green]{ref}[/bold green]' to your favorites.")
    else:
        render_message_box("Favorite Removed", f"Removed '[yellow]{ref}[/yellow]' from your favorites.")


# =============================================================================
# Benchmarking CLI
# =============================================================================

@bench_app.command("run")
def bench_run(
    model: str = typer.Argument("default", help="Model alias to benchmark"),
    suite: str = typer.Option("general", "--suite", "-s", help="Benchmark suite: general, coding, speed"),
    rounds: int = typer.Option(1, "--rounds", "-r", help="Number of test rounds"),
    port: int = typer.Option(8080, "--port", "-p", help="Gateway port"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run standardized performance benchmarks measuring TTFT, TPS, and latency."""
    model_alias = str(_val(model, "default"))
    suite_name = str(_val(suite, "general"))
    rounds_val = int(_val(rounds, 1))
    port_val = int(_val(port, 8080))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    gateway_url = f"http://127.0.0.1:{port_val}/v1"
    bench_svc = BenchmarkService()

    if not json_val and not plain_val:
        console.print(f"\n[bold cyan]Starting Benchmark: '{model_alias}' (Suite: {suite_name}, Rounds: {rounds_val})[/bold cyan]")

    try:
        run = bench_svc.run_benchmark(
            model_alias=model_alias,
            suite_name=suite_name,
            rounds=rounds_val,
            gateway_url=gateway_url,
        )
    except Exception as exc:
        if json_val:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Benchmark Failed", str(exc))
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(run.model_dump_json(indent=2))
        return

    if plain_val:
        print(f"Run ID: {run.run_id}")
        print(f"Model: {run.model_alias}")
        print(f"Suite: {run.suite_name}")
        print(f"Tokens: {run.completion_tokens} generated / {run.prompt_tokens} prompt")
        print(f"TTFT: {run.ttft_ms:.1f} ms")
        print(f"TPS: {run.tps:.2f} tokens/sec")
        print(f"Total Duration: {run.total_duration_ms:.1f} ms")
        return

    table = Table(title=f"Benchmark Results: {run.model_alias}", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Measurement")

    table.add_row("Run ID", run.run_id)
    table.add_row("Suite Name", run.suite_name)
    table.add_row("Throughput (TPS)", f"[bold green]{run.tps:.2f}[/bold green] tokens/sec")
    table.add_row("Time to First Token (TTFT)", f"{run.ttft_ms:.1f} ms")
    table.add_row("Total Generation Duration", f"{run.total_duration_ms:.1f} ms")
    table.add_row("Prompt Tokens", str(run.prompt_tokens))
    table.add_row("Completion Tokens", str(run.completion_tokens))
    table.add_row("Recorded At", run.created_at)

    console.print(table)


@bench_app.command("compare")
def bench_compare(
    model1: str = typer.Argument(..., help="First model alias"),
    model2: str = typer.Argument(..., help="Second model alias"),
    suite: str = typer.Option("general", "--suite", "-s", help="Benchmark suite to compare"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Compare performance metrics between two models."""
    m1 = str(_val(model1))
    m2 = str(_val(model2))
    suite_name = str(_val(suite, "general"))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    bench_svc = BenchmarkService()
    comp = bench_svc.compare_benchmarks(m1, m2, suite_name=suite_name)

    if json_val:
        typer.echo(comp.model_dump_json(indent=2))
        return

    if plain_val:
        print(f"Comparison: {m1} vs {m2} (Suite: {suite_name})")
        for r in comp.runs:
            print(f"- {r.model_alias}: {r.tps:.2f} TPS | TTFT {r.ttft_ms:.1f} ms")
        if comp.winner_alias:
            print(f"Winner: {comp.winner_alias} ({comp.speedup_ratio:.2f}x faster)")
        return

    table = Table(title=f"Benchmark Comparison: {m1} vs {m2} ({suite_name})", border_style="cyan")
    table.add_column("Model Alias", style="bold")
    table.add_column("Throughput (TPS)", justify="right")
    table.add_column("TTFT (Latency)", justify="right")
    table.add_column("Prompt Tokens", justify="right")
    table.add_column("Completion Tokens", justify="right")

    for r in comp.runs:
        is_winner = r.model_alias == comp.winner_alias
        style = "bold green" if is_winner else ""
        table.add_row(
            f"[{style}]{r.model_alias}{' ★' if is_winner else ''}[/{style}]",
            f"[{style}]{r.tps:.2f} tok/s[/{style}]",
            f"{r.ttft_ms:.1f} ms",
            str(r.prompt_tokens),
            str(r.completion_tokens),
        )

    console.print(table)
    if comp.winner_alias:
        console.print(f"\n[bold green]★ {comp.winner_alias}[/bold green] is [bold cyan]{comp.speedup_ratio:.2f}x[/bold cyan] faster in throughput.\n")


@bench_app.command("results")
def bench_results(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Filter results by model alias"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results to display"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List historical benchmark runs."""
    m_val = _val(model, None)
    lim_val = int(_val(limit, 20))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    bench_svc = BenchmarkService()
    runs = bench_svc.list_results(model_alias=m_val, limit=lim_val)

    if json_val:
        typer.echo(json.dumps([r.model_dump() for r in runs], indent=2))
        return

    if plain_val:
        for r in runs:
            print(f"{r.run_id}\t{r.model_alias}\t{r.suite_name}\t{r.tps:.2f} TPS\t{r.ttft_ms:.1f} ms\t{r.created_at}")
        return

    table = Table(title="Historical Benchmark Runs", border_style="cyan")
    table.add_column("Run ID", style="dim")
    table.add_column("Model Alias", style="bold")
    table.add_column("Suite")
    table.add_column("TPS", justify="right", style="green")
    table.add_column("TTFT", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Recorded At", style="dim")

    for r in runs:
        table.add_row(
            r.run_id,
            r.model_alias,
            r.suite_name,
            f"{r.tps:.2f} tok/s",
            f"{r.ttft_ms:.1f} ms",
            f"{r.total_duration_ms:.0f} ms",
            r.created_at[:19].replace("T", " "),
        )

    console.print(table)


@bench_app.command("show")
def bench_show(
    run_id: str = typer.Argument(..., help="Run ID to inspect"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Show detailed breakdown of a benchmark run."""
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    bench_svc = BenchmarkService()
    run = bench_svc.get_run(run_id)

    if not run:
        if json_val:
            typer.echo(json.dumps({"error": f"Run '{run_id}' not found", "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Not Found", f"Benchmark run '{run_id}' was not found.")
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(run.model_dump_json(indent=2))
        return

    if plain_val:
        print(f"Run ID: {run.run_id}")
        print(f"Model: {run.model_alias}")
        print(f"Suite: {run.suite_name}")
        print(f"TPS: {run.tps:.2f}")
        print(f"TTFT: {run.ttft_ms:.1f} ms")
        print(f"Raw Metrics: {run.raw_metrics}")
        return

    console.print(f"\n[bold cyan]Benchmark Run Details: {run.run_id}[/bold cyan]")
    console.print(f"  [bold]Model Alias:[/bold]   {run.model_alias}")
    console.print(f"  [bold]Suite:[/bold]         {run.suite_name}")
    console.print(f"  [bold]Throughput:[/bold]    [bold green]{run.tps:.2f}[/bold green] tokens/sec")
    console.print(f"  [bold]Latency (TTFT):[/bold] {run.ttft_ms:.1f} ms")
    console.print(f"  [bold]Prompt Tokens:[/bold] {run.prompt_tokens}")
    console.print(f"  [bold]Comp Tokens:[/bold]   {run.completion_tokens}")
    console.print(f"  [bold]Duration:[/bold]      {run.total_duration_ms:.1f} ms")
    console.print(f"  [bold]Recorded At:[/bold]   {run.created_at}")
    console.print(f"  [bold]Raw Samples:[/bold]   {json.dumps(run.raw_metrics, indent=2)}\n")


# =============================================================================
# Tools & Task Execution CLI
# =============================================================================

def _read_tool_input(target: str) -> str:
    p = Path(target)
    if p.exists() and p.is_file():
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"Failed to read file '{p}': {exc}") from exc
    return target


@tools_app.command("list")
def tools_list(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category: text, code, structured, retrieval, audio, multimodal"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Discover all 18 supported local AI tool interfaces."""
    cat_filter = _val(category, None)
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    task_svc = TaskService()
    tasks = task_svc.list_tasks()

    if cat_filter:
        tasks = [t for t in tasks if t.category.value == cat_filter.lower()]

    if json_val:
        typer.echo(json.dumps([t.model_dump() for t in tasks], indent=2))
        return

    if plain_val:
        for t in tasks:
            print(f"{t.task_id}\t{t.name}\t{t.category.value}\t{t.description}")
        return

    table = Table(title="Available AI Tools & Tasks", border_style="cyan")
    table.add_column("Tool ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Category", style="yellow")
    table.add_column("Description")
    table.add_column("Example Usage", style="dim")

    for t in tasks:
        table.add_row(t.task_id, t.name, t.category.value, t.description, t.example_usage)

    console.print(table)


@tools_app.command("run")
def tools_run(
    task_id: str = typer.Argument(..., help="Task ID: summarize, rewrite, translate, etc."),
    input_val: str = typer.Argument(..., help="Text prompt or path to input file"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run an AI tool interface on text or an input file."""
    t_id = str(_val(task_id))
    raw_in = str(_val(input_val))
    m_val = _val(model, None)
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    task_svc = TaskService()
    text = _read_tool_input(raw_in)

    try:
        run = task_svc.execute_task(task_id=t_id, input_text=text, model_alias=m_val)
    except Exception as exc:
        if json_val:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Task Failed", str(exc))
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(run.model_dump_json(indent=2))
        return

    if plain_val:
        if run.output_text:
            print(run.output_text)
        else:
            print(f"Error: {run.error_message}")
        return

    if run.output_text:
        console.print(f"\n[bold green]Output ({run.task_id} - {run.model_alias} - {run.duration_ms:.0f}ms):[/bold green]")
        console.print(run.output_text)
    else:
        render_error_box("Execution Failed", run.error_message or "Unknown failure")


@tools_app.command("summarize")
def tools_summarize(
    target: str = typer.Argument(..., help="File path or text to summarize"),
    bullets: bool = typer.Option(False, "--bullets", "-b", help="Format as bullet points"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Summarize a document or passage into key points or an executive summary."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="summarize",
        input_text=text,
        parameters={"bullets": bool(_val(bullets, False))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Summary ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("rewrite")
def tools_rewrite(
    target: str = typer.Argument(..., help="File path or text to rewrite"),
    tone: str = typer.Option("clear and professional", "--tone", "-t", help="Target tone"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Rewrite text improving tone, conciseness, and clarity."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="rewrite",
        input_text=text,
        parameters={"tone": str(_val(tone))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Rewritten ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("translate")
def tools_translate(
    target: str = typer.Argument(..., help="File path or text to translate"),
    lang: str = typer.Option("English", "--target", "-l", help="Target language (e.g. Arabic, English, Spanish)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Translate text accurately into target language."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="translate",
        input_text=text,
        parameters={"target_lang": str(_val(lang))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Translation ({lang}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("explain-code")
def tools_explain_code(
    target: str = typer.Argument(..., help="Source file path or code snippet"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Explain source code logic, architecture, and potential bugs."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="explain-code",
        input_text=text,
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Code Explanation ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("generate-code")
def tools_generate_code(
    spec: str = typer.Argument(..., help="Specification or prompt for code generation"),
    language: str = typer.Option("python", "--language", "-l", help="Target programming language"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target coding model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Generate source code, scripts, or functions from a specification."""
    text = _read_tool_input(str(_val(spec)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="generate-code",
        input_text=text,
        parameters={"language": str(_val(language))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Generated Code ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("refactor-code")
def tools_refactor_code(
    target: str = typer.Argument(..., help="Source code file or snippet to refactor"),
    issue: str = typer.Option("optimize and clean up bugs", "--issue", "-i", help="Goal, bug description, or refactoring guideline"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target coding model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Refactor code, fix bugs, and optimize performance."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="refactor-code",
        input_text=text,
        parameters={"issue": str(_val(issue))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Refactored Code ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("compare")
def tools_compare(
    target1: str = typer.Argument(..., help="First file or text"),
    target2: str = typer.Argument(..., help="Second file or text"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Compare two documents, configurations, or texts."""
    t1 = _read_tool_input(str(_val(target1)))
    t2 = _read_tool_input(str(_val(target2)))
    combined = f"--- Item 1 ---\n{t1}\n\n--- Item 2 ---\n{t2}"
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="compare",
        input_text=combined,
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Comparison ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("draft")
def tools_draft(
    outline: str = typer.Argument(..., help="Outline or prompt for draft"),
    doc_type: str = typer.Option("document", "--type", "-t", help="Type: email, memo, readme, announcement"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Draft emails, READMEs, announcements, or memos."""
    text = _read_tool_input(str(_val(outline)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="draft",
        input_text=text,
        parameters={"type": str(_val(doc_type))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Drafted {doc_type.title()} ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("extract")
def tools_extract(
    target: str = typer.Argument(..., help="File path or text to extract from"),
    schema: str = typer.Option("JSON object", "--schema", "-s", help="Fields or JSON schema to extract"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Extract structured data or JSON fields from unstructured text."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="extract",
        input_text=text,
        parameters={"schema": str(_val(schema))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Extracted Data ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("classify")
def tools_classify(
    target: str = typer.Argument(..., help="File path or text to classify"),
    labels: str = typer.Option("positive,negative,neutral", "--labels", "-l", help="Comma-separated candidate labels"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Classify text according to user-defined categories."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="classify",
        input_text=text,
        parameters={"labels": str(_val(labels))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Classification ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("review-table")
def tools_review_table(
    target: str = typer.Argument(..., help="CSV or Markdown table file"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Review tabular data and generate summary insights."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="review-table",
        input_text=text,
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Table Review ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("meeting-notes")
def tools_meeting_notes(
    target: str = typer.Argument(..., help="Transcript text file"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Extract action items, decisions, and summaries from meeting transcripts."""
    text = _read_tool_input(str(_val(target)))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="meeting-notes",
        input_text=text,
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Meeting Notes ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("describe-image")
def tools_describe_image(
    image_path: str = typer.Argument(..., help="Path to image file (.png, .jpg, etc.)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target vision model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Analyze and describe the contents of an image using a multimodal model."""
    img_str = str(_val(image_path))
    task_svc = TaskService()
    try:
        run = task_svc.execute_task(
            task_id="describe-image",
            input_text=img_str,
            model_alias=_val(model, None),
        )
    except Exception as exc:
        if _val(json_output, False):
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Vision Processing Failed", str(exc))
        raise typer.Exit(code=1)

    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Image Description ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("read-image")
def tools_read_image(
    image_path: str = typer.Argument(..., help="Path to image file containing text or diagrams"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target vision model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Extract visible text and diagram labels from an image (visual OCR)."""
    img_str = str(_val(image_path))
    task_svc = TaskService()
    try:
        run = task_svc.execute_task(
            task_id="read-image",
            input_text=img_str,
            model_alias=_val(model, None),
        )
    except Exception as exc:
        if _val(json_output, False):
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Vision OCR Failed", str(exc))
        raise typer.Exit(code=1)

    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Extracted Text ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("generate-image")
def tools_generate_image(
    prompt: str = typer.Argument(..., help="Visual scene description or prompt for image generation"),
    style: str = typer.Option("photorealistic", "--style", "-s", help="Visual style (e.g. cinematic, anime, oil painting)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Generate local diffusion prompts, camera lighting, and negative prompts for image synthesis."""
    text = str(_val(prompt))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="generate-image",
        input_text=text,
        parameters={"style": str(_val(style))},
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Image Synthesis Specification ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("generate-video")
def tools_generate_video(
    prompt: str = typer.Argument(..., help="Scene description and motion narrative for video generation"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Generate shot progressions, camera motion vectors, and direction for video diffusion."""
    text = str(_val(prompt))
    task_svc = TaskService()
    run = task_svc.execute_task(
        task_id="generate-video",
        input_text=text,
        model_alias=_val(model, None),
    )
    if _val(json_output, False):
        typer.echo(run.model_dump_json(indent=2))
    elif _val(plain, False):
        print(run.output_text or run.error_message)
    else:
        console.print(f"\n[bold green]Video Direction Specification ({run.model_alias}):[/bold green]\n{run.output_text or run.error_message}")


@tools_app.command("history")
def tools_history(
    task: Optional[str] = typer.Option(None, "--task", "-t", help="Filter by task ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max records to display"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List historical tool executions."""
    t_val = _val(task, None)
    lim_val = int(_val(limit, 20))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    db = get_db()
    runs = db.list_task_runs(task_id=t_val, limit=lim_val)

    if json_val:
        typer.echo(json.dumps(runs, indent=2))
        return

    if plain_val:
        for r in runs:
            print(f"{r['run_id']}\t{r['task_id']}\t{r['model_alias']}\t{r['status']}\t{r['duration_ms']:.0f}ms\t{r['created_at']}")
        return

    table = Table(title="Tool Execution History", border_style="cyan")
    table.add_column("Run ID", style="dim")
    table.add_column("Task ID", style="bold cyan")
    table.add_column("Model Alias")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Input Summary")
    table.add_column("Executed At", style="dim")

    for r in runs:
        status_style = "green" if r["status"] == "success" else "red"
        table.add_row(
            r["run_id"],
            r["task_id"],
            r["model_alias"],
            f"[{status_style}]{r['status']}[/{status_style}]",
            f"{r['duration_ms']:.0f} ms",
            r["input_summary"][:40] + ("..." if len(r["input_summary"]) > 40 else ""),
            r["created_at"][:19].replace("T", " "),
        )

    console.print(table)


@tools_app.command("ask-files")
def tools_ask_files(
    query: str = typer.Argument(..., help="Question to ask over local indexed files"),
    collection: Optional[str] = typer.Option(None, "--collection", "-c", help="Target collection name or ID"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Answer questions grounded in local indexed files with source citations."""
    q = str(_val(query))
    col = _val(collection, None)
    m = _val(model, None)
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    res = search_svc.ask(query=q, collection_name_or_id=col, model_alias=m)

    if json_val:
        typer.echo(res.model_dump_json(indent=2))
        return

    if plain_val:
        print(res.answer)
        print("\nSources:")
        for s in res.sources:
            print(f"- {s.relative_path} (Lines {s.line_start}-{s.line_end})")
        return

    console.print(f"\n[bold green]Grounded Answer:[/bold green]\n{res.answer}\n")
    if res.sources:
        console.print("[bold cyan]Source Citations:[/bold cyan]")
        for i, s in enumerate(res.sources, 1):
            console.print(f"  [{i}] [bold]{s.relative_path}[/bold] (Lines {s.line_start}-{s.line_end}, Relevance: {s.score:.2f})")
    console.print()


@tools_app.command("find-passages")
def tools_find_passages(
    query: str = typer.Argument(..., help="Search query to find matching passages"),
    collection: Optional[str] = typer.Option(None, "--collection", "-c", help="Target collection name or ID"),
    top: int = typer.Option(5, "--top", "-k", help="Max passages to return"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Retrieve top-matching document passages from indexed collections."""
    q = str(_val(query))
    col = _val(collection, None)
    k = int(_val(top, 5))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    passages = search_svc.find_passages(query=q, collection_name_or_id=col, top_k=k)

    if json_val:
        typer.echo(json.dumps([p.model_dump() for p in passages], indent=2))
        return

    if plain_val:
        for p in passages:
            print(f"{p.relative_path}:{p.line_start}-{p.line_end}\tScore: {p.score}\n{p.content}\n---")
        return

    table = Table(title=f"Retrieved Passages: '{q}'", border_style="cyan")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Location", style="bold cyan")
    table.add_column("Content Excerpt")

    for p in passages:
        excerpt = p.content[:150].replace("\n", " ") + ("..." if len(p.content) > 150 else "")
        table.add_row(f"{p.score:.2f}", f"{p.relative_path}:{p.line_start}", excerpt)

    console.print(table)


# =============================================================================
# Document Search & Offline RAG CLI
# =============================================================================

@search_app.command("add")
def search_add(
    path: str = typer.Argument(..., help="Path to file or directory to index"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Collection name"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Index a directory or document into a local searchable collection."""
    p_val = str(_val(path))
    n_val = _val(name, None)
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    try:
        col = search_svc.add_collection(path=p_val, name=n_val)
    except Exception as exc:
        if json_val:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Indexing Failed", str(exc))
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(col.model_dump_json(indent=2))
        return

    if plain_val:
        print(f"Indexed collection '{col.name}': {col.file_count} files, {col.passage_count} passages.")
        return

    render_message_box(
        "Collection Indexed",
        f"Collection: [bold cyan]{col.name}[/bold cyan]\n"
        f"Files: {col.file_count}\n"
        f"Passages: {col.passage_count}\n"
        f"Path: {col.path}",
    )


@search_app.command("list")
def search_list(
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List all indexed local document collections."""
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    cols = search_svc.list_collections()

    if json_val:
        typer.echo(json.dumps([c.model_dump() for c in cols], indent=2))
        return

    if plain_val:
        for c in cols:
            print(f"{c.collection_id}\t{c.name}\t{c.file_count} files\t{c.passage_count} passages\t{c.path}")
        return

    table = Table(title="Indexed Document Collections", border_style="cyan")
    table.add_column("Collection ID", style="dim")
    table.add_column("Name", style="bold cyan")
    table.add_column("Files", justify="right")
    table.add_column("Passages", justify="right")
    table.add_column("Source Path")
    table.add_column("Last Indexed", style="dim")

    for c in cols:
        table.add_row(
            c.collection_id,
            c.name,
            str(c.file_count),
            str(c.passage_count),
            c.path,
            c.last_indexed_at[:19].replace("T", " "),
        )

    console.print(table)


@search_app.command("status")
def search_status(
    name_or_id: str = typer.Argument(..., help="Collection name or ID"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Show details and indexing status of a collection."""
    nid = str(_val(name_or_id))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    col = search_svc.get_collection(nid)

    if not col:
        if json_val:
            typer.echo(json.dumps({"error": f"Collection '{nid}' not found", "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Not Found", f"Collection '{nid}' was not found.")
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(col.model_dump_json(indent=2))
        return

    if plain_val:
        print(f"Collection: {col.name} ({col.collection_id})")
        print(f"Files: {col.file_count} | Passages: {col.passage_count}")
        print(f"Path: {col.path}")
        print(f"Last Indexed: {col.last_indexed_at}")
        return

    console.print(f"\n[bold cyan]Collection Status: {col.name}[/bold cyan]")
    console.print(f"  [bold]ID:[/bold]           {col.collection_id}")
    console.print(f"  [bold]Path:[/bold]         {col.path}")
    console.print(f"  [bold]Files:[/bold]        {col.file_count}")
    console.print(f"  [bold]Passages:[/bold]     {col.passage_count}")
    console.print(f"  [bold]Last Indexed:[/bold] {col.last_indexed_at}\n")


@search_app.command("update")
def search_update(
    name_or_id: str = typer.Argument(..., help="Collection name or ID to re-index"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Re-index documents in a collection to pick up modified files."""
    nid = str(_val(name_or_id))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    try:
        col = search_svc.update_collection(nid)
    except Exception as exc:
        if json_val:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Update Failed", str(exc))
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(col.model_dump_json(indent=2))
        return

    render_message_box(
        "Collection Updated",
        f"Re-indexed '[bold cyan]{col.name}[/bold cyan]'.\nFiles: {col.file_count} | Passages: {col.passage_count}",
    )


@search_app.command("remove")
def search_remove(
    name_or_id: str = typer.Argument(..., help="Collection name or ID to remove"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Remove a collection and its indexed passages."""
    nid = str(_val(name_or_id))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    search_svc = SearchService()
    removed = search_svc.remove_collection(nid)

    if json_val:
        typer.echo(json.dumps({"success": removed, "collection": nid}, indent=2))
        return

    if removed:
        render_message_box("Collection Removed", f"Successfully removed collection '{nid}'.")
    else:
        render_error_box("Remove Failed", f"Collection '{nid}' was not found.")


@search_app.command("find")
def search_find(
    query: str = typer.Argument(..., help="Search query"),
    collection: Optional[str] = typer.Option(None, "--collection", "-c", help="Collection to search"),
    top: int = typer.Option(5, "--top", "-k", help="Max results to return"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Find relevant passages across indexed collections."""
    tools_find_passages(query=query, collection=collection, top=top, plain=plain, json_output=json_output)


@search_app.command("ask")
def search_ask(
    query: str = typer.Argument(..., help="Question to ask over local files"),
    collection: Optional[str] = typer.Option(None, "--collection", "-c", help="Target collection"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Answer questions grounded in local documents with citations."""
    tools_ask_files(query=query, collection=collection, model=model, plain=plain, json_output=json_output)


# =============================================================================
# Voice & Audio Interaction CLI
# =============================================================================

@voice_app.command("status")
def voice_status(
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Check microphone, audio hardware, and speech engine availability."""
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    voice_svc = VoiceService()
    deps = voice_svc.check_audio_dependencies()

    if json_val:
        typer.echo(json.dumps(deps, indent=2))
        return

    if plain_val:
        print(f"Can Record: {deps['can_record']}")
        print(f"sounddevice: {deps['has_sounddevice']} | numpy: {deps['has_numpy']}")
        print(f"TTS Engine: {deps['tts_engine'] or 'None (console fallback)'}")
        print(f"STT Engine: {deps['stt_engine'] or 'None'}")
        return

    table = Table(title="Audio & Voice Subsystem Status", border_style="cyan")
    table.add_column("Component", style="bold")
    table.add_column("Status / Engine")

    table.add_row(
        "Microphone Recording",
        "[green]Ready[/green]" if deps["can_record"] else "[yellow]Missing libraries (pip install sounddevice numpy)[/yellow]",
    )
    table.add_row(
        "Text-to-Speech (TTS)",
        f"[green]{deps['tts_engine']}[/green]" if deps["tts_engine"] else "[yellow]Console simulation (install piper or espeak-ng)[/yellow]",
    )
    table.add_row(
        "Speech-to-Text (STT)",
        f"[green]{deps['stt_engine']}[/green]" if deps["stt_engine"] else "[yellow]Optional (install whisper for local STT)[/yellow]",
    )

    console.print(table)


@voice_app.command("transcribe")
def voice_transcribe(
    audio_file: str = typer.Argument(..., help="Path to audio file (.wav, .mp3, etc.)"),
    model: str = typer.Option("whisper-base", "--model", "-m", help="Target speech model"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Transcribe an audio file to text."""
    af = str(_val(audio_file))
    m = str(_val(model, "whisper-base"))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    voice_svc = VoiceService()
    try:
        run = voice_svc.transcribe_audio_file(audio_path=af, model=m)
    except Exception as exc:
        if json_val:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Transcription Failed", str(exc))
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(run.model_dump_json(indent=2))
        return

    if plain_val:
        print(run.transcript or run.error_message)
        return

    console.print(f"\n[bold green]Transcription ({run.audio_seconds:.1f}s):[/bold green]\n{run.transcript or run.error_message}\n")


@voice_app.command("speak")
def voice_speak(
    text: str = typer.Argument(..., help="Text to speak out loud"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Speak text out loud using local text-to-speech engine."""
    t_val = str(_val(text))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    voice_svc = VoiceService()
    run = voice_svc.speak_text(t_val)

    if json_val:
        typer.echo(run.model_dump_json(indent=2))
        return

    if plain_val:
        print(f"Spoken: {t_val}")
        return

    console.print(f"\n[bold cyan]🗣 Spoken ({run.model}):[/bold cyan] {t_val}\n")


@voice_app.command("chat")
def voice_chat(
    prompt: Optional[str] = typer.Option(None, "--message", "-m", help="Single message to voice chat"),
    model: Optional[str] = typer.Option(None, "--model", help="Target model alias"),
    plain: bool = typer.Option(False, "--plain", help="Output plain text"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Voice conversation turn: text/mic input -> LLM -> spoken audio response."""
    msg = _val(prompt, None)
    m = str(_val(model, "default"))
    plain_val = bool(_val(plain, False))
    json_val = bool(_val(json_output, False))

    voice_svc = VoiceService()

    if not msg:
        if not plain_val and not json_val:
            console.print("\n[bold cyan]LocalPilot Voice Chat (Type 'exit' to quit)[/bold cyan]")
            msg = typer.prompt("You")
        else:
            msg = "Hello LocalPilot"

    if msg.lower() in ("exit", "quit", "q"):
        return

    try:
        reply, run = voice_svc.run_conversation_turn(user_input=msg, model_alias=m)
    except Exception as exc:
        if json_val:
            typer.echo(json.dumps({"error": str(exc), "success": False}, indent=2))
            raise typer.Exit(code=1)
        render_error_box("Voice Chat Failed", str(exc))
        raise typer.Exit(code=1)

    if json_val:
        typer.echo(json.dumps({"user": msg, "assistant": reply, "run": run.model_dump()}, indent=2))
        return

    if plain_val:
        print(f"Assistant: {reply}")
        return

    console.print(f"\n[bold green]LocalPilot Voice ({m}):[/bold green] {reply}\n")


@voice_app.callback(invoke_without_command=True)
def voice_callback(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Message for voice chat"),
    model: Optional[str] = typer.Option(None, "--model", help="Model alias"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Interactive voice chat and speech tools."""
    if ctx.invoked_subcommand is None:
        voice_chat(prompt=message, model=model, plain=plain, json_output=json_output)


# Tool shortcuts for voice
@tools_app.command("transcribe")
def tools_transcribe(
    audio_file: str = typer.Argument(..., help="Path to audio file"),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Transcribe audio to text."""
    voice_transcribe(audio_file=audio_file, plain=plain, json_output=json_output)


@tools_app.command("speak")
def tools_speak(
    text: str = typer.Argument(..., help="Text to speak"),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Speak text using text-to-speech."""
    voice_speak(text=text, plain=plain, json_output=json_output)


@tools_app.command("voice")
def tools_voice(
    message: Optional[str] = typer.Option(None, "--message", "-m"),
    plain: bool = typer.Option(False, "--plain"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Interactive voice conversation."""
    voice_chat(prompt=message, plain=plain, json_output=json_output)


@catalog_app.command("sources")
def catalog_sources(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON format"),
) -> None:
    """List all supported model sources and registries."""
    from localpilot.data import get_initial_sources
    sources = get_initial_sources()
    if json_output:
        console.print(json.dumps(sources, indent=2))
        return

    table = Table(title="Model Registries & Download Sources", border_style="cyan")
    table.add_column("Source ID", style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Auth Type", style="yellow")
    table.add_column("Formats Supported", style="green")
    table.add_column("Description")
    for s in sources:
        formats = ", ".join(s.get("formats_supported", []))
        table.add_row(
            s.get("source_id", ""),
            s.get("name", ""),
            s.get("auth_type", "None"),
            formats,
            s.get("description", "")
        )
    console.print(table)


@catalog_app.command("providers")
def catalog_providers(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON format"),
) -> None:
    """List foundational AI research laboratories and model providers."""
    from localpilot.data import get_initial_providers
    providers = get_initial_providers()
    if json_output:
        console.print(json.dumps(providers, indent=2))
        return

    table = Table(title="Foundational Model Providers & AI Labs", border_style="cyan")
    table.add_column("Provider ID", style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Flagship Families", style="green")
    table.add_column("License Type", style="yellow")
    table.add_column("Website", style="blue")
    for p in providers:
        fams = ", ".join(p.get("flagship_families", []))
        table.add_row(
            p.get("provider_id", ""),
            p.get("name", ""),
            fams,
            p.get("license_type", ""),
            p.get("website", "")
        )
    console.print(table)


@catalog_app.command("developers")
def catalog_developers(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON format"),
) -> None:
    """List key community quantizers, developers, and maintainers."""
    from localpilot.data import get_initial_developers
    devs = get_initial_developers()
    if json_output:
        console.print(json.dumps(devs, indent=2))
        return

    table = Table(title="Community Developers & Quantizers", border_style="cyan")
    table.add_column("Developer ID", style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Role", style="green")
    table.add_column("Specialties", style="yellow")
    table.add_column("URL", style="blue")
    for d in devs:
        specs = ", ".join(d.get("specialties", []))
        table.add_row(
            d.get("developer_id", ""),
            d.get("name", ""),
            d.get("role", ""),
            specs,
            d.get("website", "") or d.get("huggingface_url", "")
        )
    console.print(table)


@catalog_app.command("export")
def catalog_export(
    output_file: Path = typer.Option(Path("data/models.json"), "--output", "-o", help="Destination path for exported JSON"),
) -> None:
    """Export current cached models from local SQLite database to a JSON file."""
    from datetime import datetime, timezone
    db = get_db()
    rows = db.get_connection().execute("SELECT payload_json FROM catalog_models").fetchall()
    models = []
    for r in rows:
        try:
            models.append(json.loads(r["payload_json"]))
        except Exception:
            pass

    export_data = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "version": "1.0.0",
        "database": "models",
        "name": "LocalPilot Exported Models Database",
        "description": "Models exported from local SQLite database.",
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_count": len(models),
        "models": models,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(export_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    console.print(f"[bold green]✓ Exported {len(models)} models to {output_file}[/bold green]")


@catalog_app.command("reset")
def catalog_reset(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Reset local SQLite catalog cache back to initial bundled seed database."""
    if not confirm and not typer.confirm("Are you sure you want to reset your local model catalog cache to initial bundled seeds?"):
        console.print("[dim]Aborted.[/dim]")
        return
    db = get_db()
    conn = db.get_connection()
    with conn:
        conn.execute("DELETE FROM catalog_models")
        conn.execute("DELETE FROM catalog_sources")
    # Re-seed
    cat_svc = CatalogService(db)
    cat_svc._ensure_seed_cached()
    count = conn.execute("SELECT COUNT(*) as c FROM catalog_models").fetchone()["c"]
    console.print(f"[bold green]✓ Reset complete: restored {count} initial models from bundled seed database.[/bold green]")


@catalog_app.command("stats")
def catalog_stats() -> None:
    """Display statistics about catalog databases, sources, providers, and installed models."""
    from localpilot.data import get_initial_sources, get_initial_providers, get_initial_developers, get_initial_models
    db = get_db()
    conn = db.get_connection()
    cached_models = conn.execute("SELECT COUNT(*) as c FROM catalog_models").fetchone()["c"]
    fav_count = conn.execute("SELECT COUNT(*) as c FROM model_favorites").fetchone()["c"]
    installed_count = conn.execute("SELECT COUNT(*) as c FROM installations").fetchone()["c"]
    sources_count = len(get_initial_sources())
    providers_count = len(get_initial_providers())
    devs_count = len(get_initial_developers())
    bundled_models = len(get_initial_models())

    table = Table(title="LocalPilot Catalog & Database Statistics", border_style="cyan")
    table.add_column("Category", style="bold cyan")
    table.add_column("Count", style="bold green")
    table.add_column("Notes", style="dim")
    table.add_row("Initial Bundled Models", str(bundled_models), "Packaged in localpilot/data/models.json")
    table.add_row("Local Cached Models", str(cached_models), "Stored in local SQLite state.db")
    table.add_row("Model Registries / Sources", str(sources_count), "Packaged in sources.json")
    table.add_row("AI Labs / Providers", str(providers_count), "Packaged in providers.json")
    table.add_row("Community Developers", str(devs_count), "Packaged in developers.json")
    table.add_row("Favorited Models", str(fav_count), "User-selected favorites")
    table.add_row("Installed Models", str(installed_count), "Installed on this computer")
    console.print(table)


# =====================================================================
# Web Commands (Serving the website from /web directory)
# =====================================================================

@web_app.command("serve")
@app.command("serve-web")
def web_serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind website server to (e.g. 8000)"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host interface to bind to"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Automatically open browser on start"),
) -> None:
    """Serve the documentation, guides, and model showcase website from the /web directory."""
    port_val = int(_val(port, 8000))
    host_val = str(_val(host, "127.0.0.1"))
    open_val = bool(_val(open_browser, True))

    web_dir = get_web_dir()
    if not web_dir.exists() or not (web_dir / "index.html").exists():
        render_error_box("Website Missing", f"Could not locate the /web directory at {web_dir}.")
        raise typer.Exit(code=1)

    url = f"http://{host_val}:{port_val}/"
    console.print(f"\n[bold cyan]LocalPilot Website Server[/bold cyan]")
    console.print(f"  • Source Directory: [dim]{web_dir}[/dim]")
    console.print(f"  • Local URL:        [bold green]{url}[/bold green]")
    console.print(f"  • Press [bold yellow]Ctrl+C[/bold yellow] to stop the server.\n")

    server = WebServer(host=host_val, port=port_val, web_dir=web_dir)
    try:
        server.start(blocking=True, open_browser=open_val)
    except KeyboardInterrupt:
        console.print("\n[dim]Web server stopped.[/dim]")
    except Exception as exc:
        render_error_box("Server Error", str(exc))
        raise typer.Exit(code=1)


@web_app.command("open")
def web_open(
    port: int = typer.Option(8000, "--port", "-p", help="Port of running local website server"),
    online: bool = typer.Option(False, "--online", help="Open official GitHub Pages website instead of localhost"),
) -> None:
    """Open the LocalPilot website in the default system web browser."""
    import webbrowser
    port_val = int(_val(port, 8000))
    online_val = bool(_val(online, False))
    if online_val:
        url = "https://fakhruldeen.github.io/LocalPilot/"
    else:
        url = f"http://127.0.0.1:{port_val}/"
    console.print(f"Opening [cyan]{url}[/cyan] in browser...")
    webbrowser.open(url)


@web_app.command("path")
def web_path() -> None:
    """Print the absolute filesystem path of the /web directory."""
    web_dir = get_web_dir()
    print(str(web_dir))


if __name__ == "__main__":
    app()



