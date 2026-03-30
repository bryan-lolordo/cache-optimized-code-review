import logging
from state import CodeReviewState

logger = logging.getLogger(__name__)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def supervisor(state: CodeReviewState) -> dict:
    """Orchestrate the code review — fan out on first pass, synthesize on second pass."""

    # first pass — findings empty, routing handled by route_supervisor
    if not state.get("findings"):
        logger.info("Supervisor: first pass — dispatching reviewers")
        return {}

    logger.info("Supervisor: second pass — synthesizing %d findings", len(state.get("findings", [])))

    # second pass — findings populated, synthesize before fixer
    findings = state.get("findings", [])

    # sort by severity — critical first
    sorted_findings = sorted(
        findings,
        key=lambda f: SEVERITY_ORDER.get(f.get("severity", "low").lower(), 3)
    )

    # group by type
    grouped = {}
    for finding in sorted_findings:
        finding_type = finding.get("type", "unknown")
        if finding_type not in grouped:
            grouped[finding_type] = []
        grouped[finding_type].append(finding)

    # build preliminary summary
    summary_lines = ["## Preliminary Review Summary\n"]
    for group_type, group_findings in grouped.items():
        summary_lines.append(f"### {group_type.capitalize()} ({len(group_findings)} issues)")
        for f in group_findings:
            summary_lines.append(f"- [{f.get('severity', 'unknown').upper()}] {f.get('issue', '')}")
        summary_lines.append("")

    preliminary_report = "\n".join(summary_lines)

    return {
        "final_report": preliminary_report,
        "current_code": state.get("code_input")  # initialize current_code for fixer
    }