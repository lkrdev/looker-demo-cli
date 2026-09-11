"""Console helpers for human-readable output.

.. important::
   **Everything in this module writes to stderr, never stdout.**

   stdout is reserved exclusively for the machine-readable JSON envelope
   produced by :func:`looker_demo_cli.output.emit`. Keeping the streams
   separate is what guarantees ``demo-create <command> --json | jq`` always
   works, regardless of what progress narration a command emits along the way.

   Before this split, Rich banners were interleaved with JSON payloads on
   stdout and ``json.loads()`` failed on most commands.

Writing human output to stderr is standard for CLIs whose stdout is a data
stream (``curl``, ``jq``, ``gh --json``). A user running interactively sees no
difference, because a terminal displays both streams.
"""

from rich.console import Console
from rich.panel import Panel

#: The shared console. ``stderr=True`` is the load-bearing part of this module.
console = Console(stderr=True)


def print_banner(title: str = "LOOKER DEMO CREATOR (demo-create)", subtitle: str | None = None) -> None:
    """Print a stylized section banner."""
    content = f"[bold cyan]{title}[/bold cyan]"
    if subtitle:
        content += f"\n[dim]{subtitle}[/dim]"
    console.print(Panel(content, border_style="bright_blue", expand=False))


def print_success(msg: str) -> None:
    """Report a completed action."""
    console.print(f"[bold green]✓[/bold green] {msg}")


def print_info(msg: str) -> None:
    """Report neutral progress information."""
    console.print(f"[bold blue]ℹ[/bold blue] {msg}")


def print_warning(msg: str) -> None:
    """Report a recoverable problem or a caveat about the result."""
    console.print(f"[bold yellow]⚠[/bold yellow] {msg}")


def print_error(msg: str) -> None:
    """Report a failure."""
    console.print(f"[bold red]✗[/bold red] {msg}")
