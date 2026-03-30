"""Demo-only node: injects cache rule violations so the validator has something to catch.

Rotation:
  iteration 1 → Rule 1 violation (wrong key order)
  iteration 2 → Rule 2 violation (tool_results inside cached_prefix)
  iteration 3 → Rule 3 violation (mutated tool_definitions)
  iteration 4+ → clean pass (no sabotage)
"""

import logging
from state import CodeReviewState

logger = logging.getLogger(__name__)


def sabotage_node(state: CodeReviewState) -> dict:
    iteration = state.get("iteration", 0)
    logger.info("Sabotage node — iteration=%d", iteration)
    cached_prefix = state.get("cached_prefix", {})

    if iteration == 1:
        # Rule 1 — reorder keys so static content no longer leads
        reordered = {}
        reordered["tool_definitions"] = cached_prefix.get("tool_definitions", [])
        reordered["background_knowledge"] = cached_prefix.get("background_knowledge", "")
        reordered["system_prompt"] = cached_prefix.get("system_prompt", "")
        return {"cached_prefix": reordered}

    elif iteration == 2:
        # Rule 2 — inject tool_results into cached_prefix while keeping correct key order
        corrupted = {
            "system_prompt": cached_prefix.get("system_prompt", ""),
            "background_knowledge": cached_prefix.get("background_knowledge", ""),
            "tool_definitions": cached_prefix.get("tool_definitions", []),
            "tool_results": [{"type": "sabotage", "content": "should not be here"}],
        }
        return {"cached_prefix": corrupted}

    elif iteration == 3:
        # Rule 3 — mutate current_tool_definitions so they no longer match snapshot
        mutated_tools = list(state.get("current_tool_definitions") or [])
        mutated_tools.append({
            "name": "injected_tool",
            "description": "This tool should not exist",
            "input_schema": {"type": "object", "properties": {}}
        })
        return {"current_tool_definitions": mutated_tools}

    # iteration 4+ — clean pass
    return {}
