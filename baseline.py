import os
import logging
from dotenv import load_dotenv
load_dotenv()

from anthropic import Anthropic, APIError
from tools import SECURITY_TOOLS, ANALYZER_TOOLS, PERFORMANCE_TOOLS, TEST_GENERATOR_TOOLS, FIX_TOOLS
from prompts import (
    SECURITY_SYSTEM_PROMPT, SECURITY_BACKGROUND,
    ANALYZER_SYSTEM_PROMPT, ANALYZER_BACKGROUND,
    PERFORMANCE_SYSTEM_PROMPT, PERFORMANCE_BACKGROUND,
    TEST_GENERATOR_SYSTEM_PROMPT, TEST_GENERATOR_BACKGROUND,
    FIX_SYSTEM_PROMPT, FIX_BACKGROUND
)

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

def call_without_cache(system_prompt, background, tools, user_message):
    """Make an API call with no cache_control — simulates broken cache."""
    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1000,
            system=[
                {"type": "text", "text": system_prompt},
                {"type": "text", "text": background}
            ],
            tools=tools,
            messages=[{"role": "user", "content": user_message}]
        )
    except APIError as e:
        logger.error("Baseline API call failed: %s", e)
        return {"text": f"API error: {e}", "input_tokens": 0, "cache_read": 0}

    return {
        "text": next((b.text for b in response.content if hasattr(b, "text")), ""),
        "input_tokens": response.usage.input_tokens,
        "cache_read": 0  # no cache_control means no cache hits
    }

def run_baseline(code: str) -> dict:
    """Run the full review + fix pipeline without cache enforcement."""
    total_tokens = {"input": 0, "cache_read": 0}

    # review layer
    workers = [
        (SECURITY_SYSTEM_PROMPT, SECURITY_BACKGROUND, SECURITY_TOOLS,
         f"Review this code for security vulnerabilities:\n\n{code}"),
        (ANALYZER_SYSTEM_PROMPT, ANALYZER_BACKGROUND, ANALYZER_TOOLS,
         f"Analyze this code for quality and best practices:\n\n{code}"),
        (PERFORMANCE_SYSTEM_PROMPT, PERFORMANCE_BACKGROUND, PERFORMANCE_TOOLS,
         f"Analyze this code for performance bottlenecks:\n\n{code}"),
        (TEST_GENERATOR_SYSTEM_PROMPT, TEST_GENERATOR_BACKGROUND, TEST_GENERATOR_TOOLS,
         f"Review this code for test coverage gaps:\n\n{code}"),
    ]

    findings = []
    for system_prompt, background, tools, message in workers:
        print(f"  baseline worker firing...")
        result = call_without_cache(system_prompt, background, tools, message)
        findings.append(result["text"])
        total_tokens["input"] += result["input_tokens"]

    # fixer loop — simulate 4 fix iterations
    current_code = code
    for i, finding in enumerate(findings):
        print(f"  baseline fix iteration {i+1}...")
        fix_message = f"Fix this issue:\n{finding}\n\nCode:\n{current_code}"
        result = call_without_cache(FIX_SYSTEM_PROMPT, FIX_BACKGROUND, FIX_TOOLS, fix_message)
        current_code = result["text"]
        total_tokens["input"] += result["input_tokens"]

    return total_tokens
