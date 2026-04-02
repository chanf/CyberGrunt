"""Code reviewer skill for IronGate (QA)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict

from limbs.hub import limb

log = logging.getLogger("agent")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _resolve_project_path(path: str) -> str:
    if path.startswith("/"):
        return path
    return os.path.join(_project_root(), path)


@limb(
    name="check_file_issues",
    description="Check a Python file for common code quality issues: missing docstrings, print statements, TODOs, empty except blocks, long lines",
    properties={
        "file_path": {
            "type": "string",
            "description": "Path to the Python file to check (relative to project root)"
        }
    }
)
def check_file_issues(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Check a Python file for common issues."""
    _ = ctx
    file_path = str(args.get("file_path", ""))
    if not file_path:
        return {"error": "file_path is required"}

    file_path = _resolve_project_path(file_path)

    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.split("\n")

    issues = {
        "missing_docstrings": [],
        "too_long_lines": [],
        "print_statements": [],
        "todo_comments": [],
        "empty_except": []
    }

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Check for print statements (should use logging)
        if re.search(r'\bprint\s*\(', line) and not stripped.startswith("#"):
            issues["print_statements"].append({"line": i, "code": stripped[:50]})

        # Check for TODO/FIXME comments
        if "TODO" in line or "FIXME" in line:
            issues["todo_comments"].append({"line": i, "code": stripped[:50]})

        # Check for empty except blocks
        if re.search(r'except\s*:\s*pass\s*$', stripped):
            issues["empty_except"].append({"line": i, "code": stripped})

        # Check for too long lines (>100 chars)
        if len(line) > 100:
            issues["too_long_lines"].append({"line": i, "length": len(line)})

    # Check for missing docstrings on functions/classes
    for match in re.finditer(r'^\s*(def|class)\s+(\w+)', content, re.MULTILINE):
        line_num = content[:match.start()].count("\n") + 1
        name = match.group(2)

        # Look ahead for docstring
        next_content = content[match.end():match.end()+200]
        if not re.search(r'""".*?"""', next_content, re.DOTALL):
            issues["missing_docstrings"].append({
                "line": line_num,
                "type": match.group(1),
                "name": name
            })

    # Count issues
    total_issues = sum(len(v) for v in issues.values())

    return {
        "file": file_path,
        "total_issues": total_issues,
        "issues": issues,
        "summary": f"{total_issues} issues found"
    }


@limb(
    name="check_test_coverage",
    description="Basic analysis of test coverage by comparing test file to source file",
    properties={
        "test_file": {
            "type": "string",
            "description": "Path to test file"
        },
        "source_file": {
            "type": "string",
            "description": "Path to source file"
        }
    }
)
def check_test_coverage(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Basic check if test file covers the source file."""
    _ = ctx
    test_file = str(args.get("test_file", ""))
    source_file = str(args.get("source_file", ""))

    if not test_file or not source_file:
        return {"error": "Both test_file and source_file are required"}

    test_file = _resolve_project_path(test_file)
    source_file = _resolve_project_path(source_file)

    if not os.path.exists(test_file):
        return {"error": f"Test file not found: {test_file}"}
    if not os.path.exists(source_file):
        return {"error": f"Source file not found: {source_file}"}

    with open(source_file, "r", encoding="utf-8") as f:
        source_content = f.read()

    with open(test_file, "r", encoding="utf-8") as f:
        test_content = f.read()

    # Extract function names from source
    source_functions = set(re.findall(r'def\s+(\w+)\s*\(', source_content))

    # Extract test function names
    test_functions = set(re.findall(r'def\s+(test_\w+)\s*\(', test_content))

    # Check if functions are tested
    tested_functions = set()
    for func in source_functions:
        if f"test_{func}" in test_functions:
            tested_functions.add(func)

    coverage = len(tested_functions) / len(source_functions) * 100 if source_functions else 0

    return {
        "source_file": source_file,
        "test_file": test_file,
        "source_functions": sorted(source_functions),
        "test_functions": sorted(test_functions),
        "tested_functions": sorted(tested_functions),
        "untested_functions": sorted(source_functions - tested_functions),
        "coverage_percent": round(coverage, 1),
        "summary": f"{round(coverage, 1)}% coverage ({len(tested_functions)}/{len(source_functions)} functions tested)"
    }


log.info("[skills] code_reviewer loaded")
