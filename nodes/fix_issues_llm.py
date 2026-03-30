import os
import logging
from anthropic import Anthropic, APIError
from state import CodeReviewState

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

def fix_issues_llm(state: CodeReviewState) -> dict:
    cached_prefix = state.get("cached_prefix", {})
    user_message = state.get("user_message")
    findings = state.get("findings", [])
    fixed_issues = state.get("fixed_issues", []) or []

    logger.info("Fix LLM call — iteration=%d, fixed_so_far=%d", state.get("iteration", 0), len(fixed_issues))

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": cached_prefix.get("system_prompt", ""),
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    "type": "text",
                    "text": cached_prefix.get("background_knowledge", ""),
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            tools=[
                {**tool, "cache_control": {"type": "ephemeral"}} if i == len(cached_prefix.get("tool_definitions", [])) - 1
                else tool
                for i, tool in enumerate(cached_prefix.get("tool_definitions", []))
            ],
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
    except APIError as e:
        logger.error("Fix LLM API call failed: %s", e)
        return {"error": f"API error: {e}"}

    fixed_code = next(
        (block.text for block in response.content if hasattr(block, "text")),
        state.get("current_code", "")
    )

    unfixed = [f for f in findings if f not in fixed_issues]
    newly_fixed = unfixed[0] if unfixed else None
    updated_fixed = fixed_issues + [newly_fixed] if newly_fixed else fixed_issues

    usage = response.usage
    logger.info("Fix LLM complete — input_tokens=%d, cache_tokens=%d", usage.input_tokens, getattr(usage, "cache_read_input_tokens", 0) or 0)

    return {
        "current_code": fixed_code,
        "fixed_issues": updated_fixed,
        "tool_results": [
            {
                "type": "fix_result",
                "content": fixed_code,
                "source": "fix_issues_llm",
                "input_tokens": usage.input_tokens,
                "cache_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            }
        ]
    }
