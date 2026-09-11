"""Fail when the generated command reference drifts from the implementation.

``docs/COMMANDS.md`` is the document an AI agent reads to learn what flags
exist, and it will execute what it finds there verbatim. A hand-maintained
reference goes stale the first time someone renames a flag -- and this refactor
renamed ``--project`` to ``--looker-project`` and made ``--connection``
required, either of which would have silently invalidated it.

So the reference is generated from the live Typer application, and this test is
the enforcement: it regenerates the document in memory and compares. There is
nothing to remember and nothing to keep in sync by hand.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "gen_docs.py"


def _load_generator():
    """Import ``scripts/gen_docs.py`` as a module.

    Loaded by path rather than imported normally because ``scripts/`` is not a
    package and is deliberately outside the installed distribution -- it is a
    development tool, not part of the shipped CLI.

    Returns:
        The imported ``gen_docs`` module.
    """
    spec = importlib.util.spec_from_file_location("gen_docs", _SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so that dataclasses and typing constructs
    # inside the module resolve against a real entry in sys.modules.
    sys.modules["gen_docs"] = module
    spec.loader.exec_module(module)
    return module


def test_generator_script_exists() -> None:
    """The drift check is worthless if the generator has been moved or deleted.

    Asserted separately so that a missing script reports itself rather than
    surfacing as a confusing import error inside the comparison test.
    """
    assert _SCRIPT.is_file(), f"{_SCRIPT} is missing; the docs cannot be regenerated"


def test_commands_doc_is_checked_in() -> None:
    """The generated reference must be committed, not produced only in CI.

    Readers browse it on GitHub, and agents read it from a checked-out tree, so
    an uncommitted file is the same as no file.
    """
    assert (_REPO_ROOT / "docs" / "COMMANDS.md").is_file(), (
        "docs/COMMANDS.md is missing. Run: python scripts/gen_docs.py"
    )


def test_commands_doc_matches_the_cli() -> None:
    """The checked-in reference must byte-match what the CLI generates today.

    This is the whole point of the phase: the code is the source of truth, and
    the docs cannot drift because CI regenerates them. When this fails, the fix
    is never to edit the markdown -- it is to run the generator.
    """
    gen_docs = _load_generator()
    expected = gen_docs.render()
    actual = gen_docs.OUTPUT_PATH.read_text(encoding="utf-8")

    assert actual == expected, (
        "docs/COMMANDS.md is out of date with the CLI implementation.\n"
        "Run: python scripts/gen_docs.py\n"
        "Do not edit docs/COMMANDS.md by hand -- it is generated."
    )


def test_check_mode_agrees_with_the_comparison() -> None:
    """``--check`` must return 0 exactly when the document is current.

    CI and contributors use the script's exit code rather than this test, so
    the two paths have to agree; otherwise a green local ``--check`` could be
    followed by a red pipeline.
    """
    gen_docs = _load_generator()
    assert gen_docs.main(["--check"]) == 0


def test_generated_doc_documents_every_root_command() -> None:
    """Every registered command must appear, including newly added groups.

    Guards the generator itself: a walk that silently skipped a subtree would
    still produce a byte-stable file, so the equality test above would pass
    while the reference quietly lost a command.
    """
    from typer.main import get_command

    from looker_demo_cli.cli import app

    content = (_REPO_ROOT / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
    root = get_command(app)

    for name in root.commands:  # type: ignore[attr-defined]
        assert f"`demo-create {name}`" in content, f"{name} is registered but missing from docs/COMMANDS.md"


def test_generated_doc_documents_nested_subcommands() -> None:
    """Group children must be documented too, not just the group itself.

    ``demo-create lookml`` alone is not runnable; the leaf commands are what an
    agent actually invokes, so their flags are the part that must not drift.
    """
    from typer.main import get_command

    from looker_demo_cli.cli import app

    content = (_REPO_ROOT / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
    root = get_command(app)

    for name, command in root.commands.items():  # type: ignore[attr-defined]
        for child in getattr(command, "commands", {}):
            assert f"`demo-create {name} {child}`" in content, (
                f"{name} {child} is registered but missing from docs/COMMANDS.md"
            )


def test_generated_doc_carries_a_do_not_edit_banner() -> None:
    """A generated file must say so, or someone will hand-edit it and lose the work.

    The banner is the only warning a contributor gets before their change is
    silently reverted by the next generator run.
    """
    content = (_REPO_ROOT / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
    assert "DO NOT EDIT" in content
    assert "scripts/gen_docs.py" in content
