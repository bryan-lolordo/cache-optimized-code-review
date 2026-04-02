import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from tools import ALL_TOOLS
from v2.graph import graph

BUGGY_CODE = """
def get_user(username):
    query = f"SELECT * FROM users WHERE name = '{username}'"
    return db.execute(query)

def hash_password(password):
    import hashlib
    return hashlib.md5(password.encode()).hexdigest()

API_KEY = "sk-1234567890abcdef"
"""

print("\n=== DEEP AGENT CODE REVIEW (v2) ===\n")

iteration = 0
last_code = BUGGY_CODE.strip()

for chunk in graph.stream(
    {
        "code_input": BUGGY_CODE,
        "violation_detected": False,
        "retry_count": 0,
        "iteration": 0,
        "previous_fixed_count": 0,
        "tool_definitions_snapshot": ALL_TOOLS,
        "current_tool_definitions": ALL_TOOLS,
    },
    config={"configurable": {"thread_id": "deep-v2-demo-1"}},
    stream_mode="updates",
):
    for node_name, node_output in chunk.items():
        if not node_output:
            print(f"-- {node_name} --")
            continue

        # deep supervisor — show the agent plan
        if node_name == "deep_supervisor":
            plan = node_output.get("agent_plan", [])
            reasoning = node_output.get("plan_reasoning", "")
            print(f"-- {node_name} --")
            print(f"  Reasoning: {reasoning}")
            print(f"  Spawning {len(plan)} agents:")
            for spec in plan:
                tools_str = ", ".join(t["name"] for t in spec.get("tools", []))
                print(f"    - {spec['name']} ({spec['depth']}) — {spec['role']}")
                print(f"      tools: [{tools_str}]")
                print(f"      focus: {spec.get('focus_areas', [])}")

        # dispatch — show which agent is next
        elif node_name == "dispatch_agent":
            agent = node_output.get("active_agent")
            remaining = len(node_output.get("pending_agents", []))
            if agent:
                print(f"-- {node_name} --")
                print(f"  Dispatching: {agent['name']} ({remaining} remaining)")

        # execute — show findings
        elif node_name == "execute_agent":
            print(f"-- {node_name} --")
            if "findings" in node_output:
                for f in node_output["findings"]:
                    print(f"  [{f['severity'].upper()}] {f['source']}: {f['issue'][:120]}")
            results = node_output.get("sub_agent_results", [])
            for r in results:
                print(f"  tokens: {r['input_tokens']} input, {r['cache_tokens']} cache")

        # synthesize — show summary
        elif node_name == "synthesize":
            print(f"-- {node_name} --")
            print(f"  Findings synthesized, starting fixer loop")

        # fixer
        elif node_name == "fix_llm":
            iteration += 1
            new_code = node_output.get("current_code", "").strip()
            print(f"\n{'='*60}")
            print(f"  FIX ITERATION {iteration}")
            print(f"{'='*60}")
            print(f"\n  BEFORE:\n")
            for line in last_code.splitlines():
                print(f"    {line}")
            print(f"\n  AFTER:\n")
            for line in new_code.splitlines():
                print(f"    {line}")
            print(f"\n{'='*60}\n")
            last_code = new_code

        # critique — show reflection
        elif node_name == "critique_fix":
            approved = node_output.get("fix_approved")
            critique = node_output.get("fix_critique", "")
            print(f"-- {node_name} --")
            status = "APPROVED" if approved else "REJECTED"
            print(f"  {status}: {critique[:150]}")

        # retry
        elif node_name == "retry_fix":
            print(f"-- {node_name} --")
            print(f"  Retrying fix with critique feedback")

        # cache enforcement
        elif node_name == "cache_validator":
            violated = node_output.get("violation_detected", False)
            vtype = node_output.get("violation_type")
            print(f"-- {node_name} --")
            if violated:
                labels = {
                    "rule_1": "Rule 1: static content must lead cached_prefix",
                    "rule_2": "Rule 2: tool_results must not be in cached_prefix",
                    "rule_3": "Rule 3: tool definitions must match snapshot",
                }
                print(f"  VIOLATION: {labels.get(vtype, vtype)}")
            else:
                print(f"  passed (no violations)")

        elif node_name == "cache_corrector":
            print(f"-- {node_name} --")
            print(f"  corrected -> violation_detected: {node_output.get('violation_detected')}")

        # mark_fixed
        elif node_name == "mark_fixed":
            fixed = node_output.get("fixed_issues", [])
            print(f"-- {node_name} --")
            print(f"  Total fixed: {len(fixed)}")

        # finalize
        elif node_name == "finalize":
            print(f"-- {node_name} --")
            if "final_report" in node_output:
                print(f"  final_report ready")

        # other
        else:
            print(f"-- {node_name} --")

print("\nView full trace: https://smith.langchain.com")
