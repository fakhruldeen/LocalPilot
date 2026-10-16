"""Friendly vocabulary, standard message structures, and ASCII branding (Sections 4 & 7)."""

from __future__ import annotations
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

HOME_ASCII_BANNER = r"""
  _                    _ ____  _ _       _   
 | |    ___   ___ __ _ | |  _ \(_) | ___ | |_ 
 | |   / _ \ / __/ _` || | |_) | | |/ _ \| __|
 | |__| (_) | (_| (_| || |  __/| | | (_) | |_ 
 |_____\___/ \___\__,_||_|_|   |_|_|\___/ \__|
"""


def render_banner() -> None:
    console.print(f"[bold cyan]{HOME_ASCII_BANNER}[/bold cyan]")
    console.print("[dim]Local AI, made simple.[/dim]\n")


def render_message_box(
    what_is_happening: str,
    why_needed: str,
    what_will_change: str,
    what_remains_untouched: str,
    next_action: str,
    title: str = "LocalPilot",
) -> None:
    """Renders the standard 5-part message structure from Section 4.3."""
    body = (
        f"[bold]{what_is_happening}[/bold]\n\n"
        f"[dim]Why it is needed:[/dim] {why_needed}\n"
        f"[dim]What will change:[/dim] {what_will_change}\n"
        f"[dim]What remains untouched:[/dim] {what_remains_untouched}\n\n"
        f"[cyan]Next step:[/cyan] {next_action}"
    )
    console.print(Panel(body, title=title, border_style="cyan"))


def render_error_box(
    message: str,
    details: str = "",
    next_action: str = "Try again or find a smaller model.",
) -> None:
    """Renders calm, helpful error panel from Section 4.4."""
    body = (
        f"[bold red]{message}[/bold red]\n\n"
        "Your downloaded files and existing configuration are safe.\n\n"
        f"[cyan]Suggested action:[/cyan] {next_action}"
    )
    if details:
        body += f"\n\n[dim]Details:[/dim] {details}"
    console.print(Panel(body, title="Attention", border_style="red"))
