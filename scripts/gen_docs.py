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

#: Written relative to the repository root so the script works from anywhere.
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "COMMANDS.md"

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


def main(argv: list[str] | None = None) -> int:
    """Write or verify ``docs/COMMANDS.md``.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` when ``--check`` finds the file out of date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the checked-in file differs from what would be generated.",
    )
    args = parser.parse_args(argv)

    content = render()

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH} does not exist. Run: python scripts/gen_docs.py", file=sys.stderr)
            return 1
        if OUTPUT_PATH.read_text(encoding="utf-8") != content:
            print(
                f"{OUTPUT_PATH} is out of date with the CLI implementation.\nRun: python scripts/gen_docs.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT_PATH} is up to date.")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
