import os
import json
import logging
import re
from anthropic import Anthropic, APIError
from v2.state import DeepReviewState, AgentSpec, SubAgentResult
from state import Finding
from tools import REPORT_FINDING_TOOL

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


def _extract_json(text: str):
    """Try to extract a JSON object/array from text that may contain markdown or prose."""
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        if start == -1:
            continue
        end = text.rfind(close_char)
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

    return None


def execute_agent(state: dict) -> dict:
    """Execute a sub-agent — receives isolated state from Send payload.

    The Send payload contains:
        - agent_spec: AgentSpec with name, system_prompt, tools, focus_areas, depth
        - code_input: the code to review
        - agent_plan: full plan (for state propagation)
        - plan_reasoning: why these agents were chosen
    """
    agent: AgentSpec = state.get("agent_spec")
    if not agent:
        return {"error": "No agent_spec in Send payload"}

    code = state.get("code_input", "")
    focus = ", ".join(agent.get("focus_areas", []))

    user_message = f"""Review this code. Focus on: {focus}

```python
{code}
```

Use the `report_finding` tool to report EACH issue you find. Call it once per issue with a clear description, severity, and line number."""

    logger.info(
        "Executing agent '%s' with %d tools + report_finding, depth=%s",
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

        # always include report_finding + agent's specialist tools
        all_tools = agent["tools"] + [REPORT_FINDING_TOOL]
        tools_param = [
            {**tool, "cache_control": {"type": "ephemeral"}}
            if i == len(all_tools) - 1
            else tool
            for i, tool in enumerate(all_tools)
        ]

        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=2000,
            system=system_blocks,
            tools=tools_param,
            messages=[{"role": "user", "content": user_message}],
        )

    except APIError as e:
        logger.error("Agent '%s' API call failed: %s", agent["name"], e)
        return {"error": f"Agent {agent['name']} failed: {e}"}

    usage = response.usage
    input_tokens = usage.input_tokens
    cache_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

    # extract findings — prioritize report_finding tool calls
    raw_findings = []
    tool_calls = [block for block in response.content if block.type == "tool_use"]
    text_blocks = [block for block in response.content if hasattr(block, "text") and block.text.strip()]

    # structured findings from report_finding tool
    for tc in tool_calls:
        if tc.name == "report_finding":
            inp = tc.input if isinstance(tc.input, dict) else {}
            raw_findings.append({
                "type": agent["name"],
                "issue": inp.get("issue", ""),
                "severity": inp.get("severity", "medium"),
                "line": inp.get("line", ""),
            })
            logger.info(
                "Agent '%s' reported: [%s] %s",
                agent["name"],
                inp.get("severity", "?"),
                inp.get("issue", "")[:100],
            )
        else:
            # other tool calls — extract as before
            inp = tc.input if isinstance(tc.input, dict) else {}
            raw_findings.append({
                "type": agent["name"],
                "issue": f"[{tc.name}] {inp.get('code', inp.get('fix_description', str(inp)[:300]))}",
                "severity": "high",
            })

    # also check text blocks for JSON findings (fallback)
    if text_blocks and not raw_findings:
        raw_text = "\n".join(block.text for block in text_blocks)
        extracted = _extract_json(raw_text)
        if extracted is not None:
            json_findings = extracted.get("findings", []) if isinstance(extracted, dict) else extracted
            if isinstance(json_findings, list):
                raw_findings.extend(json_findings)
        else:
            raw_findings.append({
                "type": agent["name"],
                "issue": raw_text[:500],
                "severity": "medium",
            })

    if not raw_findings:
        logger.warning("Agent '%s' produced no findings", agent["name"])

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
    }
