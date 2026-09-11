"""Host-dependent integration tests.

Everything in this module is marked ``integration`` and is **deselected by
default** (see ``addopts`` in ``pyproject.toml``). Run it explicitly with::

    pytest -m integration

These tests deliberately touch the real machine: they assert that ``uv`` is
installed, that dependency pins are satisfied in the live environment, and that
the installed console entrypoints work when spawned as real subprocesses.

That makes them useful as a **provisioning smoke test** and useless as a
regression suite -- they fail in a clean container for reasons unrelated to the
code under test. Separating them is what allows the default ``pytest`` run, and
therefore CI, to be hermetic.

Each test additionally skips itself when its required binary is absent, so
``pytest -m integration`` degrades gracefully rather than erroring.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from looker_demo_cli.precheck.env_checker import (
    RuntimeEnvironmentStatus,
    check_runtime_environment,
)

pytestmark = pytest.mark.integration

# Defined locally rather than imported from conftest: `tests/` is intentionally
# not a package, so relative imports are unavailable. Skipping (rather than
# failing) keeps `pytest -m integration` useful on a partially provisioned host.
requires_uv = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="requires the `uv` binary on PATH",
)


# The pins the CLI treats as load-bearing. A violation here means a demo run
# will fail somewhere deep in MCP or LookML deployment rather than up front.
CRITICAL_PINS = ["mcp", "pydantic-monty", "requests", "pyyaml", "lkr-dev-cli"]


@requires_uv
def test_runtime_environment_reports_all_critical_pins() -> None:
    """Every load-bearing dependency is inspected and reported."""
    status = check_runtime_environment()

    assert isinstance(status, RuntimeEnvironmentStatus)
    assert status.python_executable
    assert status.uv_installed

    reported = {dep.package_name for dep in status.dependency_checks}
    missing = set(CRITICAL_PINS) - reported
    assert not missing, f"check_runtime_environment() stopped reporting on: {sorted(missing)}"


@requires_uv
def test_critical_pins_are_satisfied_in_this_environment() -> None:
    """The developer's environment actually satisfies the pins.

    This asserts on the *host*, not on the code. It is the reason this module is
    quarantined behind the ``integration`` marker.
    """
    status = check_runtime_environment()
    by_name = {dep.package_name: dep for dep in status.dependency_checks}

    violations = [
        f"{name} (installed={by_name[name].installed_version}, required={by_name[name].expected_constraint})"
        for name in CRITICAL_PINS
        if name in by_name and not by_name[name].is_satisfied
    ]
    assert not violations, "Dependency pin violations: " + "; ".join(violations)


def _run_cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke the CLI as a real subprocess via ``python -m``."""
    return subprocess.run(
        [sys.executable, "-m", "looker_demo_cli.cli", *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def test_run_script_executes_with_bundled_dependencies(tmp_path: Path, package_env: dict[str, str]) -> None:
    """``demo-create run-script`` can import the full bundled dependency set.

    This is the guarantee the command exists to provide: an agent's scratch
    script gets pandas, pyarrow, and the BigQuery client without managing its
    own environment.
    """
    script = tmp_path / "probe.py"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "import pandas",
                "import pyarrow",
                "from google.cloud import bigquery",
                "import looker_demo_cli",
                "print('RUNNER_TEST_SUCCESS')",
                "sys.exit(0)",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_cli(["run-script", str(script)], package_env)

    assert "RUNNER_TEST_SUCCESS" in result.stdout


def test_python_subcommand_runs_inline_code(package_env: dict[str, str]) -> None:
    """``demo-create python -c ...`` executes inside the CLI's environment."""
    result = _run_cli(
        ["python", "-c", "import looker_demo_cli; print('PYTHON_CMD_SUCCESS')"],
        package_env,
    )

    assert "PYTHON_CMD_SUCCESS" in result.stdout


@requires_uv
def test_env_info_renders_dependency_health_table(package_env: dict[str, str]) -> None:
    """``demo-create env info`` renders the runtime health table end to end.

    The hermetic equivalent lives in ``tests/test_cli_core.py``; this variant
    additionally proves the console entrypoint is wired up correctly when the
    package is executed as a module.

    The table is asserted on **stderr**, not stdout. Phase 2 moved all Rich
    output to stderr so that stdout carries the JSON envelope and nothing else;
    a real subprocess is the only place that split can be verified for true,
    OS-level file descriptors rather than Click's in-process capture. The empty
    stdout assertion is the load-bearing half: it proves a human-mode command
    emits nothing an agent could mistake for machine-readable output.
    """
    result = _run_cli(["env", "info"], package_env)

    assert "Python Runtime" in result.stderr
    assert "mcp" in result.stderr
    assert "pydantic-monty" in result.stderr
    assert result.stdout == ""
