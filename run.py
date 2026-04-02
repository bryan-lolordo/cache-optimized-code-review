import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from tools import ALL_TOOLS
from graph import graph_local as graph

BUGGY_CODE = """
def get_user(username):
    query = f"SELECT * FROM users WHERE name = '{username}'"
    return db.execute(query)

def hash_password(password):
    import hashlib
    return hashlib.md5(password.encode()).hexdigest()

API_KEY = "sk-1234567890abcdef"
"""

print("\n=== CACHE-OPTIMIZED CODE REVIEW ===\n")

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
    config={"configurable": {"thread_id": "demo-1"}},
    stream_mode="updates"
):
    for node_name, node_output in chunk.items():
        if not node_output:
            print(f"-- {node_name} --")
            continue

        # review phase — show findings
        if node_name in ("security_reviewer", "code_analyzer", "performance_optimizer", "test_generator"):
            print(f"-- {node_name} --")
            if "findings" in node_output:
                for f in node_output["findings"]:
                    print(f"  [{f['severity'].upper()}] {f['type']}: {f['issue'][:120]}...")

        # sabotage — show what was corrupted
        elif node_name == "sabotage_node":
            if "cached_prefix" in node_output:
                keys = list(node_output["cached_prefix"].keys())
                print(f"-- {node_name} --")
                print(f"  injected bad cached_prefix, keys: {keys}")
            elif "current_tool_definitions" in node_output:
                count = len(node_output["current_tool_definitions"])
                print(f"-- {node_name} --")
                print(f"  injected extra tool (now {count} tools, snapshot has {len(ALL_TOOLS)})")
            else:
                print(f"-- {node_name} --")
                print(f"  clean pass (no sabotage)")

        # validator — show violation
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

        # corrector — show what was fixed
        elif node_name == "cache_corrector":
            print(f"-- {node_name} --")
            print(f"  corrected -> violation_detected: {node_output.get('violation_detected')}")

        # fixer LLM — show before/after code
        elif node_name == "fix_issues_llm":
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

        # other nodes — minimal output
        elif node_name == "fix_issues":
            print(f"-- {node_name} --")
        elif node_name == "supervisor":
            print(f"-- {node_name} --")
        elif node_name == "test_code":
            print(f"-- {node_name} --")
        elif node_name == "finalize":
            print(f"-- {node_name} --")
            if "final_report" in node_output:
                print(f"  final_report ready")

print("\nView full trace: https://smith.langchain.com")
