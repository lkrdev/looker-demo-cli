"""``demo-create env`` -- local workspace virtual environment and runtime health."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import ConfigError
from looker_demo_cli.precheck.env_checker import (
    RuntimeEnvironmentStatus,
    check_runtime_environment,
    init_workspace_venv,
)
from looker_demo_cli.utils.console import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

env_app = typer.Typer(
    name="env",
    help="Manage local demo workspace virtual environment and runtime health.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


def render_env_tables(env_status: RuntimeEnvironmentStatus) -> None:
    """Render the runtime dependency health report as Rich tables.

    Prints two tables: the interpreter itself (executable, version, whether it
    is an isolated virtualenv, and the ``uv`` version) followed by one row per
    pinned critical dependency showing installed version against the required
    constraint. A bare system Python additionally emits a warning and the
    remediation command, since every dependency pin below it is then being
    reported against an environment the CLI does not control.

    Shared by ``demo-create env info`` and ``demo-create pre-check``: both
    present the same section, and the two rendered it identically only by
    virtue of calling one function -- which is why it lives here rather than
    being inlined into either.

    Args:
        env_status: The inspected runtime environment to render.
    """
    console.print("\n[bold cyan]1. Python Runtime & Dependency Health[/bold cyan]")
    t_env = Table(show_header=True, header_style="bold blue")
    t_env.add_column("Property", style="dim")
    t_env.add_column("Value", style="bold")
    t_env.add_row("Python Executable", env_status.python_executable)
    t_env.add_row("Python Version", env_status.python_version)
    venv_str = (
        f"[green]ACTIVE[/green] ({env_status.active_venv_path})"
        if env_status.is_virtualenv
        else "[red]NO (Bare System Python)[/red]"
    )
    t_env.add_row("Virtual Environment", venv_str)
    t_env.add_row(
        "uv Package Manager",
        f"[green]{env_status.uv_version}[/green]" if env_status.uv_installed else "[yellow]Not Found[/yellow]",
    )
    console.print(t_env)

    t_deps = Table(show_header=True, header_style="bold blue")
    t_deps.add_column("Package")
    t_deps.add_column("Installed Version")
    t_deps.add_column("Required Pin")
    t_deps.add_column("Status")

    for dep in env_status.dependency_checks:
        status_label = "[green]VALID[/green]" if dep.is_satisfied else "[red]VIOLATION[/red]"
        inst_label = dep.installed_version or "[dim]Missing[/dim]"
        t_deps.add_row(dep.package_name, inst_label, dep.expected_constraint, status_label)
    console.print(t_deps)

    if not env_status.is_virtualenv:
        print_warning("Execution is running on bare system Python without an isolated virtual environment.")
        print_info(
            "Run `demo-create env init` to bootstrap a local `.venv` or re-run with `demo-create pre-check --fix`."
        )


@env_app.command(name="init")
def env_init(
    ctx: typer.Context,
    target_dir: Annotated[
        Path | None,
        typer.Option("--dir", help="Directory where .venv will be created. Defaults to the current directory."),
    ] = None,
    state_file: StateFileOption = None,
):
    """Initialize a dedicated .venv in the target directory with all pinned tools.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        target_dir: Directory the ``.venv`` is created in. Defaults to the
            current working directory.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        typer.Exit: With code 1 when the environment could not be bootstrapped.
    """
    # This command reads no state, but every command in the CLI accepts
    # --state-file: a caller scripting the gated flow should never have to
    # remember which subset honours it.
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)

    # Resolved here rather than as `= Path.cwd()` in the signature: a default
    # expression is evaluated once at import, freezing whichever directory the
    # interpreter happened to start in. It also baked an absolute,
    # machine-specific path into the generated command reference.
    resolved_dir = target_dir or Path.cwd()

    success, msg = init_workspace_venv(target_dir=resolved_dir)
    if success:
        print_success(f"Workspace environment ready! Activate it with:\n  $ {msg}")
    else:
        print_error(f"Failed to initialize environment: {msg}")
        raise typer.Exit(code=1)


@env_app.command(name="info")
def env_info(ctx: typer.Context, state_file: StateFileOption = None):
    """Display runtime environment details and critical dependency pin health.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`. Unused --
            this command reads nothing but the live interpreter -- but declared
            so that every command in the CLI has the same first parameter and
            can be introspected uniformly.
        state_file: Optional explicit path to ``.demo-state.json``.
    """
    # Accepted for the same uniformity reason as `env init`, above.
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)

    env_status = check_runtime_environment()
    render_env_tables(env_status)


# ---------------------------------------------------------------------------
# Root-level runtime escape hatches
# ---------------------------------------------------------------------------
#
# `run-script` and `python` are not part of the `env` group -- they are
# top-level commands -- but they live in this module because they solve the
# same problem it does: reaching the interpreter that has the CLI's pinned
# dependencies installed. An agent that needs pandas or the BigQuery SDK gets
# them here without provisioning anything.
#
# These two are also the *only* commands in the CLI that do not accept
# --state-file, and that is deliberate. Both declare `ignore_unknown_options`
# and forward `ctx.args` verbatim to a child process, so declaring the option
# would consume it here instead: `demo-create run-script load.py --state-file
# demo.json` would stop passing `--state-file demo.json` to `load.py`. Neither
# reads flow state, so there is nothing to point at a different file anyway.
# Do not "fix" this for uniformity.


def run_script(
    script_path: Annotated[Path, typer.Argument(help="Path to Python script to execute with CLI environment")],
    ctx: typer.Context,
):
    """Execute a Python script using the CLI's bundled runtime and dependencies.

    Note:
        Takes no ``--state-file``: every argument after the script path belongs
        to the script. See the section comment above.

    Args:
        script_path: Script to execute.
        ctx: Typer context; any extra arguments are forwarded to the script.

    Raises:
        ConfigError: The script does not exist.
        typer.Exit: Always, carrying the script's own exit code so that the
            wrapper is transparent to a shell chaining on ``&&``.
    """
    if not script_path.exists():
        raise ConfigError(
            f"Script file not found: {script_path}",
            remediation="Pass a path that exists, relative to the current working directory.",
            details={"script_path": str(script_path)},
        )

    cmd = [sys.executable, str(script_path), *ctx.args]
    res = subprocess.run(cmd)
    raise typer.Exit(code=res.returncode)


def run_python(ctx: typer.Context):
    """Execute Python within the CLI's environment (e.g. ``demo-create python -c '...'``).

    Note:
        Takes no ``--state-file``: every argument belongs to the interpreter.
        See the section comment above.

    Args:
        ctx: Typer context; every extra argument is forwarded to the
            interpreter untouched.

    Raises:
        typer.Exit: Always, carrying the interpreter's own exit code.
    """
    cmd = [sys.executable, *ctx.args]
    res = subprocess.run(cmd)
    raise typer.Exit(code=res.returncode)


#: Passing arbitrary flags through to a child process requires Click to stop
#: interpreting them. Without this, `demo-create python -c "..."` is a usage
#: error because Click claims `-c` for itself.
_PASSTHROUGH_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}


def register_root(app: typer.Typer) -> None:
    """Register this module's root-level (ungrouped) commands.

    Args:
        app: The root Typer application to attach the commands to.
    """
    app.command(name="run-script", context_settings=_PASSTHROUGH_SETTINGS)(run_script)
    app.command(name="python", context_settings=_PASSTHROUGH_SETTINGS)(run_python)
