import os
import json
import logging
from anthropic import Anthropic, APIError
from v2.state import DeepReviewState, AgentSpec, SubAgentResult
from state import Finding

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


def _extract_json(text: str):
    """Try to extract a JSON object/array from text that may contain markdown or prose."""
    text = text.strip()

    # try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # try extracting from markdown fences
    import re
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # try finding first { ... } or [ ... ] block
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        if start == -1:
            continue
        # find matching close by scanning from the end
        end = text.rfind(close_char)
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

    return None


def dispatch_agent(state: DeepReviewState) -> dict:
    """Pop the next agent from the queue and set it as active."""
    pending = list(state.get("pending_agents") or [])

    if not pending:
        logger.info("No more agents to dispatch")
        return {"active_agent": None, "pending_agents": []}

    next_agent = pending.pop(0)
    logger.info("Dispatching agent: %s (%s)", next_agent["name"], next_agent["role"])

    return {
        "active_agent": next_agent,
        "pending_agents": pending,
    }


def execute_agent(state: DeepReviewState) -> dict:
    """Execute the active sub-agent — make the LLM call with its dynamic prompt and tools."""
    agent: AgentSpec = state.get("active_agent")
    if not agent:
        return {"error": "No active agent to execute"}

    code = state.get("code_input", "")
    focus = ", ".join(agent.get("focus_areas", []))

    user_message = f"""Review this code. Focus on: {focus}

```python
{code}
```

Return your findings as JSON:
{{
    "findings": [
        {{
            "type": "{agent['name']}",
            "issue": "Description of the issue",
            "severity": "critical|high|medium|low",
            "line": "line number or range if applicable"
        }}
    ]
}}"""

    # build cached prefix — follows v1 cache rules (Rule 1: static content leads)
    cached_prefix = {
        "system_prompt": agent["system_prompt"],
        "background_knowledge": f"Focus areas: {focus}",
        "tool_definitions": agent["tools"],
    }

    logger.info(
        "Executing agent '%s' with %d tools, depth=%s",
        agent["name"],
        len(agent["tools"]),
        agent["depth"],
    )

    try:
        system_blocks = [
            {
                "type": "text",
                "text": agent["system_prompt"],
                "cache_control": {"type": "ephemeral"},
            }
        ]

        # only include tools if the agent has them
        tools_param = (
            [
                {**tool, "cache_control": {"type": "ephemeral"}}
                if i == len(agent["tools"]) - 1
                else tool
                for i, tool in enumerate(agent["tools"])
            ]
            if agent["tools"]
            else []
        )

        create_kwargs = {
            "model": LLM_MODEL,
            "max_tokens": 2000,
            "system": system_blocks,
            "messages": [{"role": "user", "content": user_message}],
        }
        if tools_param:
            create_kwargs["tools"] = tools_param

        response = client.messages.create(**create_kwargs)

    except APIError as e:
        logger.error("Agent '%s' API call failed: %s", agent["name"], e)
        return {"error": f"Agent {agent['name']} failed: {e}"}

    # parse findings from the response — handle both text and tool_use blocks
    usage = response.usage
    input_tokens = usage.input_tokens
    cache_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

    raw_findings = []

    # first check for tool_use blocks (agent tried to call tools)
    tool_calls = [block for block in response.content if block.type == "tool_use"]
    text_blocks = [block for block in response.content if hasattr(block, "text")]

    if tool_calls:
        logger.info("Agent '%s' made %d tool calls — extracting as findings", agent["name"], len(tool_calls))
        for tc in tool_calls:
            # each tool call's input describes what the agent found
            tool_input = tc.input if isinstance(tc.input, dict) else {}
            raw_findings.append({
                "type": agent["name"],
                "issue": f"[{tc.name}] {tool_input.get('code', tool_input.get('fix_description', str(tool_input)[:300]))}",
                "severity": "high",
            })

    # also check text blocks for additional findings
    if text_blocks:
        raw_text = "\n".join(block.text for block in text_blocks)
        logger.info("Agent '%s' raw text (first 500 chars): %s", agent["name"], raw_text[:500])
        extracted = _extract_json(raw_text)
        if extracted is not None:
            json_findings = extracted.get("findings", []) if isinstance(extracted, dict) else extracted
            if isinstance(json_findings, list):
                raw_findings.extend(json_findings)
        elif not tool_calls:
            # no tool calls and no JSON — treat whole text as a finding
            raw_findings.append({
                "type": agent["name"],
                "issue": raw_text[:500],
                "severity": "medium",
            })

    if not raw_findings:
        logger.warning("Agent '%s' produced no findings from any response blocks", agent["name"])

    # convert to Finding typed dicts
    findings: list[Finding] = []
    for rf in raw_findings:
        findings.append({
            "type": rf.get("type", agent["name"]),
            "issue": rf.get("issue", ""),
            "severity": rf.get("severity", "medium"),
            "source": agent["name"],
            "input_tokens": input_tokens,
            "cache_tokens": cache_tokens,
        })

    sub_result: SubAgentResult = {
        "agent_name": agent["name"],
        "findings": findings,
        "input_tokens": input_tokens,
        "cache_tokens": cache_tokens,
    }

    logger.info(
        "Agent '%s' complete — %d findings, %d input tokens, %d cache tokens",
        agent["name"],
        len(findings),
        input_tokens,
        cache_tokens,
    )

    return {
        "findings": findings,
        "sub_agent_results": [sub_result],
        "tool_results": [
            {
                "type": "agent_result",
                "content": str(raw_findings)[:500],
                "source": agent["name"],
                "input_tokens": input_tokens,
                "cache_tokens": cache_tokens,
            }
        ],
    }


def route_after_agent(state: DeepReviewState) -> str:
    """Route based on whether there are more agents to dispatch."""
    pending = state.get("pending_agents") or []
    if pending:
        return "dispatch_agent"
    return "synthesize"
