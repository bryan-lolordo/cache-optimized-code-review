import logging
from state import CodeReviewState
from nodes.cache_validator import CORRECT_ORDER

logger = logging.getLogger(__name__)

def cache_corrector(state: CodeReviewState) -> dict:
    """Correct cache violations by restoring tool definitions and retrying validation."""
    violation = state.get("violation_type")
    cached_prefix = state.get("cached_prefix", {})
    current_tool_definitions = state.get("current_tool_definitions")

    logger.info("Correcting violation: %s", violation)

    if violation == "rule_1":
        # reorder cached_prefix keys to CORRECT_ORDER
        cached_prefix = {key: cached_prefix[key] for key in CORRECT_ORDER if key in cached_prefix}

    elif violation == "rule_2":
        # remove tool_results from cached_prefix
        cached_prefix.pop("tool_results", None)

    elif violation == "rule_3":
        # reset current_tool_definitions to match snapshot
        current_tool_definitions = state.get("tool_definitions_snapshot")

    return {
        "cached_prefix": cached_prefix,
        "current_tool_definitions": current_tool_definitions,
        "violation_detected": False,
        "violation_type": None
    }