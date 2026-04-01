"""
Self-Check Tool - System Health Monitoring

This module documents the self-repair pattern used by the agent system.
The actual self_check tool implementation lives in tools.py.

The self-check collects:
1. Today's conversation statistics (sessions, messages, tool calls)
2. Error log summary from journalctl
3. Service uptime
4. Memory and disk usage
5. Scheduled task status and last run times
6. Memory file (MEMORY.md, daily logs) status

This is typically triggered by a daily cron job that runs the self_check tool,
formats the results, and sends the report to the owner via the message tool.

Example cron setup (created by the agent itself via the schedule tool):
  name: "daily-self-check"
  cron_expr: "0 22 * * *"
  message: "Run self_check tool, summarize the results, and send the report to the owner via message tool."

The agent processes this as a normal tool-use conversation:
  1. Scheduler triggers -> sends message to LLM
  2. LLM decides to call self_check tool
  3. Gets raw diagnostics data
  4. LLM formats it into a human-readable report
  5. LLM calls message tool to send report to owner

This creates a self-monitoring loop with zero human intervention.
"""

# The actual tool implementation lives in tools.py as:
#
# @tool("self_check", "System self-check: collect today's conversation stats, "
#       "system health, error logs, scheduled task status, etc.", {})
# def tool_self_check(args, ctx):
#     ...
#
# See tools.py for the full implementation.
#
# This file exists to document the self-repair pattern:
#
# SELF-REPAIR MECHANISM:
# 1. Daily self-check detects issues (high error count, stale sessions, disk full)
# 2. Agent can use diagnose tool for deeper investigation
# 3. Agent can use exec tool to run repair commands
# 4. Agent can use create_tool to build new diagnostic tools on the fly
# 5. Agent reports findings and actions taken to the owner
#
# This makes the system "self-evolving" -- the agent can:
# - Detect its own problems
# - Diagnose root causes
# - Apply fixes (within its tool permissions)
# - Create new tools to handle novel situations
# - Report everything transparently to the owner
