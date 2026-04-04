"""v3 agent — Deep Agents SDK orchestrator with specialist subagents.

Replaces v2's manual 12-node StateGraph with a single create_deep_agent() call.
The SDK handles the agent loop, tool calling, state management, and middleware.
"""

import os
from dotenv import load_dotenv
load_dotenv()

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

from v3.tools import report_finding, get_findings, apply_fix, get_current_code, get_review_summary
from v3.prompts import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    SECURITY_REVIEWER_PROMPT,
    PERFORMANCE_REVIEWER_PROMPT,
    QUALITY_REVIEWER_PROMPT,
    TEST_REVIEWER_PROMPT,
    FIX_CRITIC_PROMPT,
)

LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")

# ── Specialist subagents ─────────────────────────────────────────────
# Each subagent is invoked via the built-in `task` tool.
# They run autonomously, analyze code, and return a text report.
# The orchestrator then parses findings and records them via report_finding.

SUBAGENTS = [
    {
        "name": "security_reviewer",
        "description": "Review code for security vulnerabilities: SQL injection, XSS, weak crypto, hardcoded secrets, auth flaws",
        "system_prompt": SECURITY_REVIEWER_PROMPT,
        "tools": [],  # text-only report — orchestrator records findings
    },
    {
        "name": "performance_reviewer",
        "description": "Review code for performance issues: nested loops, N+1 queries, blocking I/O, missing caching",
        "system_prompt": PERFORMANCE_REVIEWER_PROMPT,
        "tools": [],
    },
    {
        "name": "quality_reviewer",
        "description": "Review code for quality problems: long functions, deep nesting, dead code, missing error handling",
        "system_prompt": QUALITY_REVIEWER_PROMPT,
        "tools": [],
    },
    {
        "name": "test_reviewer",
        "description": "Review code for test coverage gaps: untested APIs, missing edge cases, complex untested logic",
        "system_prompt": TEST_REVIEWER_PROMPT,
        "tools": [],
    },
    {
        "name": "fix_critic",
        "description": "Evaluate whether a proposed code fix is correct, complete, and minimal",
        "system_prompt": FIX_CRITIC_PROMPT,
        "tools": [],
    },
]

# ── Create the orchestrator agent ────────────────────────────────────
# The SDK wires up: agent loop, tool calling, TodoListMiddleware,
# SubAgentMiddleware, and FilesystemMiddleware automatically.

checkpointer = MemorySaver()

agent = create_deep_agent(
    name="deep-code-reviewer",
    model=LLM_MODEL,
    tools=[report_finding, get_findings, apply_fix, get_current_code, get_review_summary],
    system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
    subagents=SUBAGENTS,
    checkpointer=checkpointer,
)

# Also expose a version without checkpointer for LangGraph Platform
agent_platform = create_deep_agent(
    name="deep-code-reviewer",
    model=LLM_MODEL,
    tools=[report_finding, get_findings, apply_fix, get_current_code, get_review_summary],
    system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
    subagents=SUBAGENTS,
)
