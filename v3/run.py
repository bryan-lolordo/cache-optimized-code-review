"""v3 entry point — run the Deep Agents code review with streaming output."""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

from v3.agent import agent
from v3.tools import reset_review_state, get_review_state

BUGGY_CODE = """
def get_user(username):
    query = f"SELECT * FROM users WHERE name = '{username}'"
    return db.execute(query)

def hash_password(password):
    import hashlib
    return hashlib.md5(password.encode()).hexdigest()

API_KEY = "sk-1234567890abcdef"
"""

print("\n=== DEEP AGENT CODE REVIEW (v3 — Deep Agents SDK) ===\n")

# Reset shared tool state for this session
reset_review_state(BUGGY_CODE.strip())

config = {"configurable": {"thread_id": "deep-v3-demo-1"}}

# Stream the agent's execution
for chunk in agent.stream(
    {
        "messages": [
            {
                "role": "user",
                "content": f"Review this code for issues, fix everything you find, and produce a final report:\n\n```python\n{BUGGY_CODE.strip()}\n```",
            }
        ]
    },
    config=config,
    stream_mode="updates",
):
    for node_name, node_output in chunk.items():
        if not node_output:
            continue

        # Show agent messages and tool calls
        raw_messages = node_output.get("messages", [])
        # Handle LangGraph Overwrite wrapper
        if hasattr(raw_messages, "value"):
            messages = raw_messages.value
        elif isinstance(raw_messages, list):
            messages = raw_messages
        else:
            messages = [raw_messages] if raw_messages else []
        for msg in messages:
            # Tool calls from the agent
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "")
                    args = tc.get("args", {})

                    if name == "task":
                        agent_name = args.get("subagent_type", args.get("agent", "unknown"))
                        instruction = args.get("description", args.get("instruction", ""))[:120]
                        print(f"  >> task({agent_name}): {instruction}...")
                    elif name == "write_todos":
                        todos = args.get("todos", [])
                        print(f"  >> write_todos: {len(todos)} items")
                        for t in todos:
                            status = t.get("status", "?")
                            content = t.get("content", "")
                            print(f"     [{status}] {content}")
                    elif name == "report_finding":
                        sev = args.get("severity", "?")
                        issue = args.get("issue", "")[:120]
                        source = args.get("source", "?")
                        print(f"  >> report_finding [{sev.upper()}] ({source}): {issue}")
                    elif name == "apply_fix":
                        desc = args.get("description", "")[:120]
                        print(f"  >> apply_fix: {desc}")
                    elif name == "get_review_summary":
                        print(f"  >> get_review_summary")
                    else:
                        print(f"  >> {name}({', '.join(f'{k}=' for k in args)})")

            # Tool responses
            elif hasattr(msg, "name") and hasattr(msg, "content"):
                # Tool result — show for task (subagent reports)
                if msg.name == "task" and isinstance(msg.content, str):
                    # Truncate long subagent reports
                    report = msg.content
                    if len(report) > 300:
                        report = report[:300] + "..."
                    print(f"  << subagent report: {report}")

            # Agent text output
            elif hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
                text = msg.content.strip()
                if len(text) > 500:
                    text = text[:500] + "..."
                print(f"  agent: {text}")

# Show final state
print("\n" + "=" * 60)
print("FINAL STATE")
print("=" * 60)
state = get_review_state()
print(f"Findings: {len(state['findings'])}")
print(f"Fixes applied: {len(state['fixed_issues'])}")
for i, f in enumerate(state["findings"], 1):
    print(f"  {i}. [{f['severity'].upper()}] ({f['source']}): {f['issue'][:100]}")
print(f"\nFixes:")
for i, fix in enumerate(state["fixed_issues"], 1):
    print(f"  {i}. {fix}")

print(f"\nFinal code:\n")
for line in state["current_code"].splitlines():
    print(f"  {line}")

print("\nView full trace: https://smith.langchain.com")
