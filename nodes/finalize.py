import logging
from state import CodeReviewState

logger = logging.getLogger(__name__)


def finalize(state: CodeReviewState) -> dict:
    """Compile findings, fixes, and final code into a report."""

    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues", [])
    current_code = state.get("current_code", "")
    test_results = state.get("test_results", {})

    total = len(findings)
    fixed = len(fixed_issues)
    logger.info("Finalizing report — %d total, %d fixed, %d remaining", total, fixed, total - fixed)
    unfixed = total - fixed

    final_report = f"""
## Code Review Report

### Summary
- Total issues found: {total}
- Issues fixed: {fixed}
- Issues remaining: {unfixed}
- Tests passed: {test_results.get("passed", False)}

### Issues Found
{findings}

### Issues Fixed
{fixed_issues}

### Final Code
{current_code}
    """

    return {"final_report": final_report}