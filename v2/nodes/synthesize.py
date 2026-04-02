import logging
from v2.state import DeepReviewState

logger = logging.getLogger(__name__)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def synthesize(state: DeepReviewState) -> dict:
    """Synthesize findings from all dynamically spawned agents."""
    findings = state.get("findings", [])
    sub_results = state.get("sub_agent_results", [])
    plan_reasoning = state.get("plan_reasoning", "")

    # sort by severity
    sorted_findings = sorted(
        findings,
        key=lambda f: SEVERITY_ORDER.get(f.get("severity", "low").lower(), 3),
    )

    # group by source agent
    grouped = {}
    for f in sorted_findings:
        source = f.get("source", "unknown")
        if source not in grouped:
            grouped[source] = []
        grouped[source].append(f)

    # build summary
    lines = ["## Deep Review Summary\n"]
    lines.append(f"**Orchestrator reasoning:** {plan_reasoning}\n")
    lines.append(f"**Agents spawned:** {len(sub_results)}")
    lines.append(f"**Total findings:** {len(findings)}\n")

    for source, group in grouped.items():
        lines.append(f"### {source} ({len(group)} issues)")
        for f in group:
            lines.append(f"- [{f.get('severity', '?').upper()}] {f.get('issue', '')}")
        lines.append("")

    # token summary
    total_input = sum(r.get("input_tokens", 0) for r in sub_results)
    total_cache = sum(r.get("cache_tokens", 0) for r in sub_results)
    lines.append(f"**Token usage:** {total_input} input, {total_cache} cache read")

    report = "\n".join(lines)
    logger.info("Synthesized %d findings from %d agents", len(findings), len(sub_results))

    return {
        "final_report": report,
        "current_code": state.get("code_input"),
    }
