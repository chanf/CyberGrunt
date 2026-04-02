"""
QA-Sniffer: IronGate's custom auditing tool for deep system inspection.
"""

import os
import re
import ast
from limbs.hub import limb

@limb("check_code_complexity", "Audit code quality: check cyclomatic complexity and docstrings.",
      {"file_path": {"type": "string", "description": "Source file to audit"}},
      ["file_path"])
def tool_check_code_complexity(args, ctx):
    fpath = args["file_path"]
    if not os.path.exists(fpath):
        return f"[error] file not found: {fpath}"
    
    with open(fpath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    
    report = [f"--- Audit Report for {fpath} ---"]
    issues = 0
    
    for node in ast.walk(tree):
        # 1. Check for missing docstrings in functions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node)
            if not docstring:
                report.append(f"  [ISSUE] Missing docstring in function '{node.name}'")
                issues += 1
            
            # 2. Check for overly long functions (primitive complexity)
            line_count = node.end_lineno - node.lineno
            if line_count > 50:
                report.append(f"  [ISSUE] Function '{node.name}' is too long ({line_count} lines)")
                issues += 1

        # 3. Check for forbidden patterns (print instead of log)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'print':
                report.append(f"  [ISSUE] Use of 'print' instead of 'log' at line {node.lineno}")
                issues += 1

    if issues == 0:
        return f"Audit PASS: {fpath} is clean."
    else:
        return "\n".join(report) + f"\n\nTotal issues found: {issues}"

@limb("log_anomaly_detector", "Scan logs for hidden patterns or unhandled errors.", {})
def tool_log_anomaly_detector(args, ctx):
    log_dir = "test_reports"
    anomalies = []
    if not os.path.isdir(log_dir):
        return "No log directory found."
    
    for fname in os.listdir(log_dir):
        if fname.endswith(".txt") or fname.endswith(".log"):
            with open(os.path.join(log_dir, fname), "r") as f:
                content = f.read()
                # Find Python tracebacks that might be buried
                if "Traceback" in content:
                    anomalies.append(f"Found hidden traceback in {fname}")
    
    return "\n".join(anomalies) or "No hidden log anomalies found."
