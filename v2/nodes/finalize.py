import logging
from v2.state import DeepReviewState

logger = logging.getLogger(__name__)


def finalize(state: DeepReviewState) -> dict:
    """Compile the final report with findings, fixes, and agent execution summary."""
    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues") or []
    current_code = state.get("current_code", "")
    sub_results = state.get("sub_agent_results", [])
    plan_reasoning = state.get("plan_reasoning", "")
    preliminary = state.get("final_report", "")

    lines = [
        "# Deep Agent Code Review Report\n",
        preliminary,
        "\n---\n",
        "## Fix Summary\n",
        f"- **Issues found:** {len(findings)}",
        f"- **Issues fixed:** {len(fixed_issues)}",
        f"- **Fix iterations:** {state.get('iteration', 0)}",
        f"- **Agents used:** {len(sub_results)}",
    ]

    if fixed_issues:
        lines.append("\n### Fixed Issues")
        for f in fixed_issues:
            lines.append(f"- [{f.get('severity', '?').upper()}] {f.get('issue', '')}")

    unfixed = [f for f in findings if f not in fixed_issues]
    if unfixed:
        lines.append("\n### Remaining Issues")
        for f in unfixed:
            lines.append(f"- [{f.get('severity', '?').upper()}] {f.get('issue', '')}")

    lines.append("\n## Final Code\n")
    lines.append(f"```python\n{current_code}\n```")

    report = "\n".join(lines)
    logger.info("Finalize: %d fixed, %d remaining", len(fixed_issues), len(unfixed))

    return {"final_report": report}
