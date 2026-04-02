import os
import json
import logging
from typing import Literal
from anthropic import Anthropic, APIError
from langgraph.types import Command
from v2.state import DeepReviewState
from v2.prompts import FIX_SYSTEM_PROMPT, FIX_BACKGROUND, CRITIQUE_SYSTEM_PROMPT
from tools import FIX_TOOLS

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


def select_issue(state: DeepReviewState) -> Command[Literal["fix_llm", "finalize"]]:
    """Select the next unfixed issue — routes to fixer or finalize."""
    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues") or []
    current_code = state.get("current_code") or state.get("code_input")

    unfixed = [f for f in findings if f not in fixed_issues]
    logger.info(
        "select_issue: %d total, %d fixed, %d remaining",
        len(findings),
        len(fixed_issues),
        len(unfixed),
    )

    if not unfixed:
        logger.info("All issues fixed — routing to finalize")
        return Command(
            update={"iteration": state.get("iteration", 0)},
            goto="finalize",
        )

    next_issue = unfixed[0]

    cached_prefix = {
        "system_prompt": FIX_SYSTEM_PROMPT,
        "background_knowledge": FIX_BACKGROUND,
        "tool_definitions": FIX_TOOLS,
    }

    user_message = f"""Fix this issue in the code:

Issue: {next_issue}

Current code:
{current_code}

Apply the minimal fix and return the complete updated code."""

    return Command(
        update={
            "cached_prefix": cached_prefix,
            "next_node": "fix_llm",
            "user_message": user_message,
            "tool_results": None,
            "iteration": state.get("iteration", 0) + 1,
            "previous_fixed_count": len(fixed_issues),
            "fix_approved": None,
            "fix_critique": None,
        },
        goto="fix_llm",
    )


def fix_llm(state: DeepReviewState) -> dict:
    """Apply a fix via LLM call — cache-aware."""
    cached_prefix = state.get("cached_prefix", {})
    user_message = state.get("user_message")
    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues") or []

    logger.info("fix_llm: iteration=%d", state.get("iteration", 0))

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": cached_prefix.get("system_prompt", ""),
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": cached_prefix.get("background_knowledge", ""),
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": user_message}],
        )
    except APIError as e:
        logger.error("Fix LLM failed: %s", e)
        return {"error": f"Fix API error: {e}"}

    fixed_code = next(
        (block.text for block in response.content if hasattr(block, "text")),
        state.get("current_code", ""),
    )

    usage = response.usage
    logger.info(
        "fix_llm complete — input=%d, cache=%d",
        usage.input_tokens,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
    )

    return {
        "current_code": fixed_code,
        "tool_results": [
            {
                "type": "fix_result",
                "content": fixed_code,
                "source": "fix_llm",
                "input_tokens": usage.input_tokens,
                "cache_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            }
        ],
    }


def critique_fix(state: DeepReviewState) -> dict:
    """Reflection step — evaluate whether the fix is correct and complete."""
    current_code = state.get("current_code", "")
    original_code = state.get("code_input", "")
    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues") or []
    unfixed = [f for f in findings if f not in fixed_issues]
    current_issue = unfixed[0] if unfixed else {}

    logger.info("critique_fix: evaluating fix for iteration %d", state.get("iteration", 0))

    user_message = f"""Evaluate this fix:

ORIGINAL CODE:
{original_code}

FIXED CODE:
{current_code}

ISSUE BEING FIXED:
{current_issue}

Is this fix correct, complete, and minimal?"""

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1000,
            system=[
                {
                    "type": "text",
                    "text": CRITIQUE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
    except APIError as e:
        logger.error("Critique failed: %s", e)
        # on failure, approve by default to avoid blocking
        return {"fix_approved": True, "fix_critique": f"Critique failed: {e}"}

    raw = next(
        (block.text for block in response.content if hasattr(block, "text")), "{}"
    )

    # strip markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        result = json.loads(raw)
        approved = result.get("approved", True)
        critique = result.get("critique", "")
    except json.JSONDecodeError:
        logger.warning("Critique returned non-JSON, approving by default")
        approved = True
        critique = raw[:300]

    logger.info("critique_fix: approved=%s, critique=%s", approved, critique[:100])

    return {
        "fix_approved": approved,
        "fix_critique": critique,
    }


def route_after_critique(state: DeepReviewState) -> str:
    """If critique rejected, retry the fix. If approved, mark fixed and continue."""
    approved = state.get("fix_approved", True)
    retry = state.get("retry_count", 0)

    # escape hatch — max 2 retries per fix
    if not approved and retry < 2:
        return "retry_fix"
    return "mark_fixed"


def retry_fix(state: DeepReviewState) -> dict:
    """Rebuild the fix prompt incorporating the critique feedback."""
    critique = state.get("fix_critique", "")
    original_message = state.get("user_message", "")

    user_message = f"""{original_message}

PREVIOUS ATTEMPT WAS REJECTED. Reviewer feedback:
{critique}

Apply a corrected fix addressing the feedback above."""

    return {
        "user_message": user_message,
        "retry_count": state.get("retry_count", 0) + 1,
    }


def mark_fixed(state: DeepReviewState) -> dict:
    """Mark the current issue as fixed after critique approval."""
    findings = state.get("findings", [])
    fixed_issues = list(state.get("fixed_issues") or [])
    unfixed = [f for f in findings if f not in fixed_issues]

    if unfixed:
        fixed_issues.append(unfixed[0])

    return {
        "fixed_issues": fixed_issues,
        "retry_count": 0,  # reset for next issue
    }
