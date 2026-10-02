"""Verify that docs/COMMANDS.md matches the live CLI implementation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from typer.main import get_command

from looker_demo_cli.cli import app

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "gen_docs.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_docs", _SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_docs"] = module
    spec.loader.exec_module(module)
    return module


def test_commands_doc_matches_cli_and_documents_all_commands() -> None:
    """The checked-in docs/COMMANDS.md must match gen_docs.py output and cover every command."""
    gen_docs = _load_generator()
    assert gen_docs.main(["--check"]) == 0

    content = gen_docs.OUTPUT_PATH.read_text(encoding="utf-8")
    assert "DO NOT EDIT" in content

    root = get_command(app)
    for name, command in getattr(root, "commands", {}).items():
        assert f"`demo-create {name}`" in content
        for child in getattr(command, "commands", {}):
            assert f"`demo-create {name} {child}`" in content
