"""Custom tools for the v3 Deep Agents code review system.

The Deep Agents SDK provides built-in filesystem and planning tools.
These custom tools add structured code review operations on top.
"""

import json
from langchain.tools import tool


# ── Shared review state ──────────────────────────────────────────────
# Module-level state shared between tools within a single review session.
# In production, you'd use StoreBackend for cross-session persistence.

_review_state = {
    "findings": [],
    "original_code": "",
    "current_code": "",
    "fixed_issues": [],
}


def reset_review_state(code: str):
    """Reset review state for a new session."""
    _review_state["findings"] = []
    _review_state["original_code"] = code
    _review_state["current_code"] = code
    _review_state["fixed_issues"] = []


def get_review_state() -> dict:
    """Access the current review state (for run.py output)."""
    return _review_state


# ── Custom tools ─────────────────────────────────────────────────────


@tool
def report_finding(issue: str, severity: str, source: str, line: str = "") -> str:
    """Report a code review finding. Call once per distinct issue found.

    Args:
        issue: Clear description of the issue
        severity: One of: critical, high, medium, low
        source: Name of the reviewer that found this (e.g. security_reviewer)
        line: Line number or range where the issue occurs
    """
    finding = {
        "issue": issue,
        "severity": severity.lower(),
        "source": source,
        "line": line,
    }
    _review_state["findings"].append(finding)
    count = len(_review_state["findings"])
    return f"Finding #{count} recorded: [{severity.upper()}] {issue}"


@tool
def get_findings() -> str:
    """Get all recorded findings from the code review so far."""
    if not _review_state["findings"]:
        return "No findings recorded yet."
    return json.dumps(_review_state["findings"], indent=2)


@tool
def apply_fix(fixed_code: str, description: str) -> str:
    """Apply a code fix. Provide the complete updated Python code.

    Args:
        fixed_code: The complete fixed code (replaces current version)
        description: Brief description of what was fixed
    """
    _review_state["current_code"] = fixed_code
    _review_state["fixed_issues"].append(description)
    count = len(_review_state["fixed_issues"])
    return f"Fix #{count} applied: {description}"


@tool
def get_current_code() -> str:
    """Get the current state of the code (with any fixes applied so far)."""
    return _review_state["current_code"] or "No code loaded."


@tool
def get_review_summary() -> str:
    """Get a complete summary of the review: all findings, fixes applied, and current code."""
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(
        _review_state["findings"],
        key=lambda f: severity_order.get(f.get("severity", "low"), 3),
    )
    return json.dumps(
        {
            "total_findings": len(sorted_findings),
            "total_fixes": len(_review_state["fixed_issues"]),
            "findings": sorted_findings,
            "fixes_applied": _review_state["fixed_issues"],
            "current_code": _review_state["current_code"],
        },
        indent=2,
    )
