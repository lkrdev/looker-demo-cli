# SPDX-FileCopyrightText: Copyright (c) 2026 lkr.dev. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


def print_banner(title: str = "LOOKER DEMO CREATOR (demo-create)", subtitle: str | None = None) -> None:
    """Print an executive stylized banner."""
    content = f"[bold cyan]{title}[/bold cyan]"
    if subtitle:
        content += f"\n[dim]{subtitle}[/dim]"
    console.print(Panel(content, border_style="bright_blue", expand=False))


def print_success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[bold blue]ℹ[/bold blue] {msg}")


def print_warning(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] {msg}")


def print_error(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")


def print_step_header(step_num: int, total_steps: int, title: str) -> None:
    console.print(f"\n[bold magenta]─── [{step_num}/{total_steps}] {title} ───[/bold magenta]")
