"""Generate ``docs/COMMANDS.md`` from the live Typer application.

The command reference is the document most likely to drift: every flag rename
silently invalidates it, and the CLI's primary reader is an AI agent that will
execute what it finds there verbatim. So the reference is not written by hand --
it is *derived* from the same Click objects the CLI dispatches on, and a CI test
(``tests/test_docs_drift.py``) regenerates it and fails if the checked-in copy
differs.

Usage::

    python scripts/gen_docs.py            # write docs/COMMANDS.md
    python scripts/gen_docs.py --check    # exit 1 if the file is out of date

The ``--check`` mode is what CI runs; it is the same comparison the drift test
performs, exposed for humans who want the answer without reading a pytest trace.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from typer.main import get_command

from looker_demo_cli.cli import app
from looker_demo_cli.gates import _COMMAND_TO_GATE_ID, GATES
from looker_demo_cli.state import FlowState

#: Written relative to the repository root so the script works from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "docs" / "COMMANDS.md"

#: Markdown files containing a synchronized `<!-- BEGIN GENERATED GATES TABLE -->` block.
MARKER_FILES = [
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "skills" / "looker-demo-orchestrator" / "SKILL.md",
]

_BEGIN_GATES_MARKER = "<!-- BEGIN GENERATED GATES TABLE -->"
_END_GATES_MARKER = "<!-- END GENERATED GATES TABLE -->"

_GATE_COMMAND_SUFFIXES: dict[str, str] = {
    "gate_0b_confirm_targets": r" *([--mode full\|bq_only])*",
    "gate_1d_catalog": " *(or `catalog inspect` / `catalog sync` / `--skip`)*",
    "gate_3a_optimize": " *(or `--skip`)*",
    "gate_4_agent": " *(or `--skip`)*",
    "gate_5_publish": " *(or `--skip`)*",
    "gate_6_embed": " *(or `--skip`)*",
    "gate_7_stream": " *(or `stream deploy` / `stream delete` / `--skip`)*",
}

#: Google-style docstring section headers, which mark the end of user-facing prose.
_SECTION_HEADER = re.compile(r"^\s*(Args|Arguments|Returns|Yields|Raises|Note|Notes|Attributes|Example|Examples):\s*$")

#: Sphinx cross-reference roles such as :class:`AppContext`, which markdown cannot render.
_SPHINX_ROLE = re.compile(r":[a-z:]+:`~?([^`]+)`")

_HEADER = """<!--
  GENERATED FILE -- DO NOT EDIT BY HAND.

  Produced by `python scripts/gen_docs.py` from the live Typer application.
  `tests/test_docs_drift.py` regenerates this file in CI and fails if it
  differs, so an edit here will be reverted by the next run. To change this
  document, change the command's `help=` text or its signature.
-->

# Command Reference

Every command, option, and default below is read directly from the
implementation. The CLI is designed to be driven by an AI agent, so two
conventions hold everywhere:

- **`--json` emits a machine-readable envelope on stdout**, and *only* that.
  All human-facing output goes to stderr, so `demo-create ... --json | jq`
  is always safe.
- **`--state-file` points at an explicit `.demo-state.json`** instead of the
  discovered one, so concurrent builds cannot read each other's progress.

Failures are reported by exit code, so a caller never has to parse prose:

| Exit | Meaning |
| ---: | :--- |
| `0` | Success |
| `2` | Usage error (unknown flag, missing argument) -- raised by Click |
| `3` | `AuthError` -- credentials missing, expired, or insufficient |
| `4` | `ConfigError` -- a required value was not supplied and could not be resolved |
| `5` | `RemoteApiError` -- Looker, BigQuery, or Google Cloud rejected the call |
| `6` | `ValidationError` -- generated artifacts failed validation |
| `7` | `StateError` -- `.demo-state.json` is unreadable, corrupt, or a newer schema |

> Start with [`demo-create status`](#demo-create-status). It reports which gates
> are complete and prints the exact next command to run.

"""


def _render_gates_table() -> list[str]:
    """Render a canonical Markdown table of all pipeline gates in ``GATES``.

    Returns:
        Markdown lines forming the 15-stage gate reference table.
    """
    empty_state = FlowState(gcp_project_id="")
    rows: list[str] = [
        "| Gate | ID | Stage | Human Pause? | Canonical CLI Command |",
        "| ---: | :--- | :--- | :---: | :--- |",
    ]
    for gate in GATES:
        number = f"**{gate.number}**"
        gate_id = f"`{gate.id}`"
        title = gate.title
        human = "**`true`**" if gate.requires_human_confirmation else "`false`"
        suffix = _GATE_COMMAND_SUFFIXES.get(gate.id, "")
        cmd = f"`{gate.command(empty_state)}`{suffix}"
        rows.append(f"| {number} | {gate_id} | {title} | {human} | {cmd} |")
    return rows


def _md(text: str) -> str:
    """Convert reStructuredText inline literals to markdown code spans.

    Docstrings in this codebase are Google-style with RST ``double backtick``
    literals, which markdown renders as an empty code span followed by bare
    text. Normalising here keeps the docstrings idiomatic for Python tooling
    while still producing correct markdown.

    Args:
        text: Raw help text taken from a docstring or ``help=`` string.

    Returns:
        The same text with ``x`` rewritten to `x`.
    """
    text = _SPHINX_ROLE.sub(r"`\1`", text)
    return text.replace("``", "`")


def _summary(raw: str | None) -> str:
    """Extract the user-facing part of a command docstring.

    Everything from the first Google-style section header onward
    (``Args:``, ``Returns:``, ``Raises:``, ``Note:``) is written for a
    developer reading the source, and the ``Args:`` block in particular
    duplicates the generated options table. Only the prose above it belongs
    in a command reference.

    Args:
        raw: The command's help text, or None.

    Returns:
        The leading prose, normalised for markdown.
    """
    if not raw:
        return ""
    lines: list[str] = []
    for line in raw.strip().splitlines():
        if _SECTION_HEADER.match(line):
            break
        lines.append(line)
    return _md("\n".join(lines).strip())


def _format_default(param: Any) -> str:
    """Render a parameter's default for the options table.

    Args:
        param: A Click ``Parameter``.

    Returns:
        A markdown cell: an empty string when there is nothing useful to show,
        so the table does not fill with noise.
    """
    default = param.default
    # Flags default to False; printing "False" for every one of them adds a
    # column of noise that tells the reader nothing.
    if default is None or default is False or default == "":
        return ""
    if default is True:
        return "`true`"
    # A default derived from the environment (`Path.cwd()`, `$HOME`, ...) would
    # bake the generating machine's absolute path into a committed file, so the
    # document would differ per contributor and the drift test could never
    # pass. Caught here rather than silently rendered: the fix belongs in the
    # command, which should default to None and resolve at call time.
    if isinstance(default, Path) and default.is_absolute():
        raise ValueError(
            f"Option {param.opts} has an environment-dependent default ({default!r}). "
            "Default it to None and resolve it inside the command instead."
        )
    return f"`{default}`"


def _render_options(command: Any) -> list[str]:
    """Render the options table for one command.

    Args:
        command: A Click ``Command``.

    Returns:
        Markdown lines, or an empty list when the command takes no options
        worth documenting.
    """
    rows: list[str] = []
    for param in command.params:
        # `--help` is on every command; documenting it 18 times is noise.
        if param.name == "help":
            continue
        names = ", ".join(f"`{opt}`" for opt in (*param.opts, *param.secondary_opts))
        if not names:
            continue
        required = " **(required)**" if getattr(param, "required", False) else ""
        help_text = _md((getattr(param, "help", "") or "").replace("\n", " ").strip())
        rows.append(f"| {names} | {help_text}{required} | {_format_default(param)} |")

    if not rows:
        return []
    return ["| Option | Description | Default |", "| :--- | :--- | :--- |", *rows, ""]


def _anchor(path: str) -> str:
    """Build the GitHub heading anchor for a command path.

    Args:
        path: The full command path, e.g. ``demo-create lookml model``.

    Returns:
        The anchor fragment GitHub generates for that heading.
    """
    return path.replace(" ", "-").lower()


def _walk(command: Any, path: str, depth: int, lines: list[str]) -> None:
    """Append markdown for ``command`` and, recursively, its subcommands.

    Args:
        command: A Click ``Command`` or ``Group``.
        path: The full invocation path, e.g. ``demo-create lookml model``.
        depth: Heading depth, so groups nest under the root.
        lines: Accumulator, mutated in place.
    """
    children = getattr(command, "commands", None)

    lines.append(f"{'#' * depth} `{path}`")
    lines.append("")
    summary = _summary(command.help or command.short_help)
    if summary:
        lines.append(summary)
        lines.append("")

    if not children:
        cmd_key = path.removeprefix("demo-create ")
        gate_id = _COMMAND_TO_GATE_ID.get(cmd_key)
        if gate_id is not None:
            gates_by_id = {g.id: g for g in GATES}
            gate = gates_by_id.get(gate_id)
            if gate is not None:
                lines.append(f"**Gate:** Gate {gate.number} (`{gate.id}`) — {gate.title}")
                lines.append("")
        lines.append("```bash")
        lines.append(f"{path} [OPTIONS]")
        lines.append("```")
        lines.append("")
        lines.extend(_render_options(command))
    else:
        # A group's own options are inherited by its children, so listing the
        # children first is what a reader actually wants from a group heading.
        lines.append("| Subcommand | Description |")
        lines.append("| :--- | :--- |")
        for name, child in sorted(children.items()):
            child_summary = _summary(child.short_help or child.help).split("\n")[0]
            lines.append(f"| [`{name}`](#{_anchor(f'{path} {name}')}) | {child_summary} |")
        lines.append("")
        for name, child in sorted(children.items()):
            _walk(child, f"{path} {name}", depth + 1, lines)


def render() -> str:
    """Render the full command reference.

    Returns:
        The complete markdown document, newline-terminated.
    """
    root = get_command(app)
    lines: list[str] = []

    lines.append("## The 15 Granular Gates")
    lines.append("")
    lines.extend(_render_gates_table())
    lines.append("")
    lines.append("---")
    lines.append("")

    # Registration order is meaningful (it follows the gate sequence), so the
    # root listing preserves it rather than sorting alphabetically.
    commands = getattr(root, "commands", {})
    lines.append("## Commands at a glance")
    lines.append("")
    lines.append("| Command | Description |")
    lines.append("| :--- | :--- |")
    for name, child in commands.items():
        summary = _summary(child.short_help or child.help).split("\n")[0]
        lines.append(f"| [`{name}`](#{_anchor(f'demo-create {name}')}) | {summary} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    for name, child in commands.items():
        _walk(child, f"demo-create {name}", 2, lines)
        lines.append("---")
        lines.append("")

    # Drop the trailing separator so the file does not end on a rule.
    while lines and lines[-1] in ("", "---"):
        lines.pop()

    return _HEADER + "\n".join(lines) + "\n"


def sync_marker_files(*, check: bool = False) -> list[Path]:
    """Synchronize or verify the generated gates table in ``MARKER_FILES``.

    Args:
        check: When True, only check whether files match without writing.

    Returns:
        List of paths in ``MARKER_FILES`` that were out of date (or updated).
    """
    replacement_block = "\n".join([_BEGIN_GATES_MARKER, *_render_gates_table(), _END_GATES_MARKER])
    dirty: list[Path] = []

    for path in MARKER_FILES:
        if not path.exists():
            dirty.append(path)
            continue
        original = path.read_text(encoding="utf-8")
        begin_idx = original.find(_BEGIN_GATES_MARKER)
        end_idx = original.find(_END_GATES_MARKER)
        if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
            dirty.append(path)
            continue
        end_pos = end_idx + len(_END_GATES_MARKER)
        updated = original[:begin_idx] + replacement_block + original[end_pos:]
        if updated != original:
            dirty.append(path)
            if not check:
                path.write_text(updated, encoding="utf-8")

    return dirty


def main(argv: list[str] | None = None) -> int:
    """Write or verify ``docs/COMMANDS.md`` and ``MARKER_FILES``.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` when ``--check`` finds any file out of date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the checked-in files differ from what would be generated.",
    )
    args = parser.parse_args(argv)

    content = render()

    if args.check:
        rc = 0
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH} does not exist. Run: python scripts/gen_docs.py", file=sys.stderr)
            rc = 1
        elif OUTPUT_PATH.read_text(encoding="utf-8") != content:
            print(
                f"{OUTPUT_PATH} is out of date with the CLI implementation.\nRun: python scripts/gen_docs.py",
                file=sys.stderr,
            )
            rc = 1

        stale_markers = sync_marker_files(check=True)
        for path in stale_markers:
            print(
                f"{path} gate table is out of date with GATES.\nRun: python scripts/gen_docs.py",
                file=sys.stderr,
            )
            rc = 1

        if rc == 0:
            print(f"{OUTPUT_PATH} and marker files are up to date.")
        return rc

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    updated_markers = sync_marker_files(check=False)
    for path in updated_markers:
        print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
