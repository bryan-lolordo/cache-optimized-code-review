import logging
from typing import Literal
from state import CodeReviewState

logger = logging.getLogger(__name__)
CORRECT_ORDER = ["system_prompt", "background_knowledge", "tool_definitions"]

def cache_validator(state: CodeReviewState) -> dict:
    """Validate that the cached prefix and tool definitions have not been tampered with."""

    # if cached_prefix is None, skip all checks and pass clean
    if state.get("cached_prefix") is None:
        return {"violation_detected": False, "violation_type": None}

    # check Rule 2 — tool_results not in cached_prefix
    # check this first so extra keys don't trigger a false Rule 1
    if "tool_results" in state.get("cached_prefix", {}):
        logger.warning("Violation: Rule 2 — tool_results found in cached_prefix")
        return {"violation_detected": True, "violation_type": "rule_2"}

    # check Rule 1 — static content leads cached_prefix in correct order
    actual_order = list(state["cached_prefix"].keys())
    if actual_order != CORRECT_ORDER:
        logger.warning("Violation: Rule 1 — key order %s != expected %s", actual_order, CORRECT_ORDER)
        return {"violation_detected": True, "violation_type": "rule_1"}

    # check Rule 3 — current matches snapshot
    # if violation → return immediately
    if state.get("tool_definitions_snapshot") is not None:
        if state.get("tool_definitions_snapshot") != state.get("current_tool_definitions"):
            logger.warning("Violation: Rule 3 — tool definitions mutated from snapshot")
            return {"violation_detected": True, "violation_type": "rule_3"}

    # clean pass
    logger.info("Cache validation passed — no violations")
    return {"violation_detected": False, "violation_type": None}
