import logging
from typing import Literal
from langgraph.types import Command
from state import CodeReviewState
from tools import FIX_TOOLS
from prompts import FIX_SYSTEM_PROMPT, FIX_BACKGROUND

logger = logging.getLogger(__name__)


def fix_issues(state: CodeReviewState) -> Command[Literal["sabotage_node", "finalize"]]:
    """Select the next unfixed issue and build a fix prompt."""
 
    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues", [])
    current_code = state.get("current_code") or state.get("code_input")
 
    # find next unfixed issue — highest severity first
    unfixed = [f for f in findings if f not in fixed_issues]
 
    logger.info("fix_issues: %d findings, %d fixed, %d remaining", len(findings), len(fixed_issues), len(unfixed))

    # if nothing left to fix, route to finalize
    if not unfixed:
        logger.info("All issues fixed — routing to finalize")
        return Command(
            update={"iteration": state.get("iteration", 0)},
            goto="finalize"
        )
 
    next_issue = unfixed[0]
 
    # build cached prefix with fix-specific static content
    cached_prefix = {
        "system_prompt": FIX_SYSTEM_PROMPT,
        "background_knowledge": FIX_BACKGROUND,
        "tool_definitions": FIX_TOOLS,
    }
 
    # build dynamic user message — never cached
    user_message = f"""
    Fix this issue in the code:
 
    Issue: {next_issue}
 
    Current code:
    {current_code}
 
    Apply the minimal fix and return the complete updated code.
    """
 
    return Command(
        update={
            "cached_prefix": cached_prefix,
            "next_node": "fix_issues_llm",
            "user_message": user_message,
            "tool_results": None,
            "iteration": state.get("iteration", 0) + 1,
            "previous_fixed_count": len(fixed_issues),
        },
        goto="sabotage_node"
    )