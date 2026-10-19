"""Friendly vocabulary, standard message structures, truthful error formatting, and ASCII branding."""

from __future__ import annotations
import json
from typing import Any, Dict, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from localpilot.domain.errors import AppError, ErrorCode, RecoveryAction, create_app_error

console = Console()

HOME_ASCII_BANNER = """\t██      ████    ████   ████  ██      █████  █████ ██      ████  ██████
██     ██  ██  ██     ██  ██ ██      ██  ██   █   ██     ██  ██   ██
██     ██  ██  ██     ██████ ██      █████    █   ██     ██  ██   ██
██     ██  ██  ██     ██  ██ ██      ██       █   ██     ██  ██   ██
██████  ████    ████  ██  ██ ██████  ██     █████ ██████  ████    ██"""


def render_banner() -> None:
    console.print(f"[bold cyan]{HOME_ASCII_BANNER}[/bold cyan]")
    console.print("[dim]Local AI, made simple.[/dim]\n")


def render_message_box(
    what_is_happening: str,
    why_needed: str = "",
    what_will_change: str = "",
    what_remains_untouched: str = "",
    next_action: str = "",
    title: str = "LocalPilot",
) -> None:
    """Renders the standard 5-part message structure."""
    parts = [f"[bold]{what_is_happening}[/bold]"]
    if why_needed:
        parts.append(f"\n[dim]Why it is needed:[/dim] {why_needed}")
    if what_will_change:
        parts.append(f"[dim]What will change:[/dim] {what_will_change}")
    if what_remains_untouched:
        parts.append(f"[dim]What remains untouched:[/dim] {what_remains_untouched}")
    if next_action:
        parts.append(f"\n[cyan]Next step:[/cyan] {next_action}")
    body = "\n".join(parts)
    console.print(Panel(body, title=title, border_style="cyan"))


_ACTION_LABELS: Dict[RecoveryAction, str] = {
    RecoveryAction.RETRY: "Try again",
    RecoveryAction.RESUME: "Resume download",
    RecoveryAction.CHECK_AGAIN: "Check status again",
    RecoveryAction.RECONNECT_SOURCE: "Provide or update access key",
    RecoveryAction.OPEN_PUBLISHER_PAGE: "Visit publisher website to accept terms",
    RecoveryAction.CHOOSE_ANOTHER_MODEL: "Choose a smaller or alternative model",
    RecoveryAction.REVIEW_STORAGE: "Review disk storage and remove unused models",
    RecoveryAction.USE_PREVIOUS_VERSION: "Continue with the previous installed version",
    RecoveryAction.REVIEW_REPAIR_OPTIONS: "Review repair options or reinstall model",
    RecoveryAction.EXPORT_DIAGNOSTICS: "View or export technical diagnostics",
    RecoveryAction.BACK: "Go back to previous menu",
    RecoveryAction.DISMISS: "Dismiss and return",
}


def render_app_error_box(error: AppError) -> None:
    """Renders truthful, calm, structured error panel adhering to Section 4 & 7."""
    rec_text = _ACTION_LABELS.get(error.recommended_action, error.recommended_action.value)
    
    body = (
        f"[bold red]{error.title}[/bold red]\n\n"
        f"{error.explanation}\n\n"
        f"[bold]Impact on files & settings:[/bold]\n{error.known_impact}\n\n"
        f"[bold cyan]Recommended action:[/bold cyan] {rec_text}"
    )

    alt_actions = [a for a in error.supported_actions if a != error.recommended_action]
    if alt_actions:
        alt_texts = [_ACTION_LABELS.get(a, a.value) for a in alt_actions]
        body += f"\n[dim]Other options:[/dim] {', '.join(alt_texts)}"

    if error.technical_details:
        body += f"\n\n[dim]Technical Details:[/dim]\n[dim]{error.technical_details}[/dim]"

    console.print(Panel(body, title=f"Attention (ID: {error.support_id})", border_style="red"))


def render_error_box(
    message: str,
    details: str = "",
    next_action: str = "Try again or find a smaller model.",
    app_error: Optional[AppError] = None,
) -> None:
    """Backward-compatible error box. When AppError is available, delegates to render_app_error_box."""
    if app_error is not None:
        render_app_error_box(app_error)
        return

    # If message is already a formatted string, build a truthful fallback without unconditional guarantees
    body = f"[bold red]{message}[/bold red]"
    if next_action:
        body += f"\n\n[cyan]Suggested action:[/cyan] {next_action}"
    if details:
        body += f"\n\n[dim]Details:[/dim] {details}"
    console.print(Panel(body, title="Attention", border_style="red"))


def format_json_error(error: AppError) -> str:
    """Serializes structured AppError to formatted JSON for CLI --json support."""
    return json.dumps(error.to_user_dict(), indent=2)
