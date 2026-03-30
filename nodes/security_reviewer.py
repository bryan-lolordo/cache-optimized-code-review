import os
import logging
from anthropic import Anthropic, APIError
from state import CodeReviewState
from tools import SECURITY_TOOLS
from prompts import SECURITY_SYSTEM_PROMPT, SECURITY_BACKGROUND
from nodes.cache_validator import CORRECT_ORDER

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

def security_reviewer(state: CodeReviewState) -> dict:
    """Review code for security vulnerabilities — prep, validate, and call LLM."""

    code = state.get("code_input")
    logger.info("Starting security review")

    # ── prep ──
    cached_prefix = {
        "system_prompt": SECURITY_SYSTEM_PROMPT,
        "background_knowledge": SECURITY_BACKGROUND,
        "tool_definitions": SECURITY_TOOLS,
    }
    user_message = f"Review this code for security vulnerabilities:\n\n{code}"

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
        logger.error("Security reviewer API call failed: %s", e)
        return {"findings": [{"type": "security", "issue": f"API error: {e}", "severity": "critical", "source": "security_reviewer", "input_tokens": 0, "cache_tokens": 0}]}

    response_text = next(
        (block.text for block in response.content if hasattr(block, "text")),
        ""
    )

    usage = response.usage
    logger.info("Security review complete — input_tokens=%d, cache_tokens=%d", usage.input_tokens, getattr(usage, "cache_read_input_tokens", 0) or 0)

    findings = [
        {
            "type": "security",
            "issue": response_text,
            "severity": "critical",
            "source": "security_reviewer",
            "input_tokens": usage.input_tokens,
            "cache_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        }
    ]

    return {"findings": findings}
