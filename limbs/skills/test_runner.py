"""Test Runner skill for IronGate (QA)."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict

from limbs.hub import limb

log = logging.getLogger("agent")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@limb(
    name="run_tests",
    description="Run pytest tests for the project. Returns test results with pass/fail status.",
    properties={
        "test_path": {
            "type": "string",
            "description": "Specific test file or module to run (e.g., 'tests/test_forum_store.py'). If empty, runs all tests."
        },
        "verbose": {
            "type": "boolean",
            "description": "Whether to show verbose output (-vv flag)"
        }
    }
)
def run_tests(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Run tests and return results."""
    _ = ctx
    test_path = str(args.get("test_path", ""))
    verbose = bool(args.get("verbose", False))

    base_dir = _project_root()

    cmd = ["python", "-m", "pytest", "-v"]
    if test_path:
        if not test_path.startswith("/"):
            test_path = os.path.join(base_dir, test_path)
        cmd.append(test_path)
    if verbose:
        cmd.append("-vv")

    try:
        result = subprocess.run(
            cmd,
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=300
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout + result.stderr,
            "returncode": result.returncode,
            "summary": "PASSED" if result.returncode == 0 else "FAILED"
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "Tests timed out after 300 seconds",
            "returncode": -1,
            "summary": "TIMEOUT"
        }
    except Exception as e:
        return {
            "success": False,
            "output": f"Error running tests: {e}",
            "returncode": -1,
            "summary": "ERROR"
        }


@limb(
    name="list_test_modules",
    description="List all test modules in the tests/ directory",
    properties={}
)
def list_test_modules(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """List all test modules in the project."""
    _ = args
    _ = ctx
    base_dir = _project_root()
    tests_dir = os.path.join(base_dir, "tests")

    if not os.path.exists(tests_dir):
        return {"modules": []}

    modules = []
    for file in os.listdir(tests_dir):
        if file.startswith("test_") and file.endswith(".py"):
            modules.append(f"tests/{file}")

    modules.sort()
    return {"modules": modules, "count": len(modules)}


log.info("[skills] test_runner loaded")
