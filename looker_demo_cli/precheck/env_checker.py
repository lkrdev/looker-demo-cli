from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from packaging import version

from looker_demo_cli.utils.console import print_error, print_info, print_success, print_warning


class DependencyCheckResult(BaseModel):
    package_name: str
    installed_version: Optional[str] = None
    expected_constraint: str
    is_satisfied: bool
    notes: str = ""


class RuntimeEnvironmentStatus(BaseModel):
    is_virtualenv: bool
    python_executable: str
    python_version: str
    uv_installed: bool
    uv_version: Optional[str] = None
    dependency_checks: List[DependencyCheckResult] = Field(default_factory=list)
    is_healthy: bool = True
    active_venv_path: Optional[str] = None


CRITICAL_CONSTRAINTS = {
    "mcp": ("<2.0.0", lambda v: version.parse(v) < version.parse("2.0.0")),
    "pydantic-monty": ("<0.0.10", lambda v: version.parse(v) < version.parse("0.0.10")),
    "requests": (">=2.31.0", lambda v: version.parse(v) >= version.parse("2.31.0")),
    "pyyaml": (">=6.0", lambda v: version.parse(v) >= version.parse("6.0")),
    "lkr-dev-cli": (">=0.0.50", lambda v: version.parse(v) >= version.parse("0.0.50")),
    "google-cloud-bigquery": (">=3.20.0", lambda v: version.parse(v) >= version.parse("3.20.0")),
    "pandas": (">=2.2.0", lambda v: version.parse(v) >= version.parse("2.2.0")),
}


def check_runtime_environment() -> RuntimeEnvironmentStatus:
    """Inspect current Python runtime, virtual environment status, and critical dependency pins."""
    is_venv = sys.prefix != sys.base_prefix or bool(os.getenv("VIRTUAL_ENV"))
    venv_path = os.getenv("VIRTUAL_ENV") or (sys.prefix if is_venv else None)

    # Check uv availability
    uv_path = shutil.which("uv")
    uv_ver = None
    if uv_path:
        try:
            res = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
            uv_ver = res.stdout.strip()
        except Exception:
            uv_ver = "installed"

    dep_results: List[DependencyCheckResult] = []
    overall_healthy = True

    for pkg, (constraint_str, validator_fn) in CRITICAL_CONSTRAINTS.items():
        try:
            inst_ver = importlib.metadata.version(pkg)
            is_valid = validator_fn(inst_ver)
            note = "OK" if is_valid else f"Version {inst_ver} violates {constraint_str}"
            if not is_valid:
                overall_healthy = False
            dep_results.append(
                DependencyCheckResult(
                    package_name=pkg,
                    installed_version=inst_ver,
                    expected_constraint=constraint_str,
                    is_satisfied=is_valid,
                    notes=note,
                )
            )
        except importlib.metadata.PackageNotFoundError:
            overall_healthy = False
            dep_results.append(
                DependencyCheckResult(
                    package_name=pkg,
                    installed_version=None,
                    expected_constraint=constraint_str,
                    is_satisfied=False,
                    notes="Not installed",
                )
            )

    return RuntimeEnvironmentStatus(
        is_virtualenv=is_venv,
        python_executable=sys.executable,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        uv_installed=bool(uv_path),
        uv_version=uv_ver,
        dependency_checks=dep_results,
        is_healthy=overall_healthy and is_venv,
        active_venv_path=venv_path,
    )


def init_workspace_venv(target_dir: Path, install_self: bool = True) -> Tuple[bool, str]:
    """Create a dedicated .venv in target_dir using uv (or venv fallback) and install looker-demo-cli."""
    venv_dir = target_dir / ".venv"
    has_uv = bool(shutil.which("uv"))

    try:
        if has_uv:
            print_info(f"Creating virtual environment in `{venv_dir}` using `uv venv`...")
            subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True)
        else:
            print_info(f"Creating virtual environment in `{venv_dir}` using Python `venv`...")
            import venv
            venv.create(str(venv_dir), with_pip=True)

        # Install dependencies
        if install_self:
            print_info("Installing `looker-demo-cli` and pinned dependencies into `.venv`...")
            py_bin = venv_dir / "bin" / "python"
            if not py_bin.exists():
                py_bin = venv_dir / "Scripts" / "python.exe"

            if has_uv:
                cmd = ["uv", "pip", "install", "--python", str(py_bin)]
                # Check if current directory has pyproject.toml
                if (target_dir / "pyproject.toml").exists():
                    cmd.extend(["-e", str(target_dir)])
                else:
                    cmd.append("looker-demo-cli")
                subprocess.run(cmd, check=True, capture_output=True)
            else:
                pip_bin = venv_dir / "bin" / "pip"
                if not pip_bin.exists():
                    pip_bin = venv_dir / "Scripts" / "pip.exe"
                cmd = [str(pip_bin), "install"]
                if (target_dir / "pyproject.toml").exists():
                    cmd.extend(["-e", str(target_dir)])
                else:
                    cmd.append("looker-demo-cli")
                subprocess.run(cmd, check=True, capture_output=True)

        activate_script = venv_dir / "bin" / "activate"
        print_success(f"Virtual environment created successfully at `{venv_dir}`.")
        return True, f"source {activate_script}"
    except Exception as e:
        print_error(f"Failed to create virtual environment: {e}")
        return False, str(e)
