"""Dependency declaration guardrails via deptry."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_deptry_clean():
    """Project dependency declarations should match imported modules."""
    repo_root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "-m",
        "deptry",
        "skitur",
        "--config",
        str(repo_root / "pyproject.toml"),
        "--known-first-party",
        "skitur",
        "--pep621-dev-dependency-groups",
        "dev",
        "--ignore-notebooks",
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "deptry found dependency declaration issues.\n\n"
        f"STDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
