import os
import logging
from anthropic import Anthropic, APIError
from state import CodeReviewState
from tools import TEST_GENERATOR_TOOLS
from prompts import TEST_GENERATOR_SYSTEM_PROMPT, TEST_GENERATOR_BACKGROUND
from nodes.cache_validator import CORRECT_ORDER

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

def test_generator(state: CodeReviewState) -> dict:
    """Review code for test coverage gaps — prep, validate, and call LLM."""

    code = state.get("code_input")
    logger.info("Starting test coverage review")

    # ── prep ──
    cached_prefix = {
        "system_prompt": TEST_GENERATOR_SYSTEM_PROMPT,
        "background_knowledge": TEST_GENERATOR_BACKGROUND,
        "tool_definitions": TEST_GENERATOR_TOOLS,
    }
    user_message = f"Review this code for test coverage gaps and recommend test cases:\n\n{code}"

    # ── inline cache validation ──
    actual_order = list(cached_prefix.keys())
    if actual_order != CORRECT_ORDER:
        logger.warning("Cache key order mismatch — reordering")
        cached_prefix = {key: cached_prefix[key] for key in CORRECT_ORDER if key in cached_prefix}

    # ── LLM call ──
    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1000,
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
        logger.error("Test generator API call failed: %s", e)
        return {"findings": [{"type": "testing", "issue": f"API error: {e}", "severity": "low", "source": "test_generator", "input_tokens": 0, "cache_tokens": 0}]}

    response_text = next(
        (block.text for block in response.content if hasattr(block, "text")),
        ""
    )

    usage = response.usage
    logger.info("Test coverage review complete — input_tokens=%d, cache_tokens=%d", usage.input_tokens, getattr(usage, "cache_read_input_tokens", 0) or 0)

    findings = [
        {
            "type": "testing",
            "issue": response_text,
            "severity": "low",
            "source": "test_generator",
            "input_tokens": usage.input_tokens,
            "cache_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        }
    ]

    return {"findings": findings}
