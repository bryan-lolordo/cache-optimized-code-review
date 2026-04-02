import os
import json
import logging
from anthropic import Anthropic, APIError
from langgraph.types import Send
from v2.state import DeepReviewState, AgentSpec
from v2.prompts import ORCHESTRATOR_SYSTEM_PROMPT, ORCHESTRATOR_BACKGROUND
from tools import ALL_TOOLS

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

# map tool names to their definitions for dynamic selection
TOOL_REGISTRY = {}
for tool in ALL_TOOLS:
    TOOL_REGISTRY[tool["name"]] = tool


def deep_supervisor(state: DeepReviewState) -> dict:
    """LLM-driven orchestrator that analyzes code and decides what agents to spawn.

    Returns agent_plan and plan_reasoning to state. The routing function
    (route_to_agents) reads these and returns Send objects for parallel fan-out.
    """

    code = state.get("code_input", "")

    # build the available tools list for the orchestrator to choose from
    available_tools_desc = "\n".join(
        f"- {t['name']}: {t['description']}" for t in ALL_TOOLS
    )

    user_message = f"""Analyze this code and decide what specialist agents to spawn:

```python
{code}
```

Available tools you can assign to agents:
{available_tools_desc}"""

    logger.info("Deep supervisor: analyzing code to build agent plan")

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": ORCHESTRATOR_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": ORCHESTRATOR_BACKGROUND,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": user_message}],
        )
    except APIError as e:
        logger.error("Deep supervisor API call failed: %s", e)
        return []

    # parse the LLM's agent plan
    raw = next(
        (block.text for block in response.content if hasattr(block, "text")), "{}"
    )

    # strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Failed to parse agent plan JSON: %s", raw[:200])
        return []

    # convert raw plan to typed AgentSpec list
    agent_specs = []
    for agent_raw in plan.get("agents", []):
        # resolve tool names to full tool definitions
        tools = [
            TOOL_REGISTRY[name]
            for name in agent_raw.get("tool_names", [])
            if name in TOOL_REGISTRY
        ]

        spec: AgentSpec = {
            "name": agent_raw["name"],
            "role": agent_raw.get("role", ""),
            "system_prompt": agent_raw.get("system_prompt", ""),
            "tools": tools,
            "focus_areas": agent_raw.get("focus_areas", []),
            "depth": agent_raw.get("depth", "shallow"),
        }
        agent_specs.append(spec)

    reasoning = plan.get("reasoning", "")
    logger.info(
        "Deep supervisor planned %d agents: %s (parallel via Send)",
        len(agent_specs),
        [s["name"] for s in agent_specs],
    )

    return {
        "agent_plan": agent_specs,
        "plan_reasoning": reasoning,
        "current_code": code,
    }


def route_to_agents(state: DeepReviewState) -> list[Send]:
    """Routing function that returns Send objects for parallel fan-out.

    Called via add_conditional_edges after deep_supervisor.
    Each Send targets 'execute_agent' with an isolated payload.
    """
    agent_specs = state.get("agent_plan") or []
    code = state.get("code_input", "")
    reasoning = state.get("plan_reasoning", "")

    return [
        Send("execute_agent", {
            "agent_spec": spec,
            "code_input": code,
            "agent_plan": agent_specs,
            "plan_reasoning": reasoning,
        })
        for spec in agent_specs
    ]
