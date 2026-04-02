# V2 — Deep Agent Architecture

> Design decisions and architecture for the v2 deep agent code review system. Built on the `v2-deep-agents` branch as a `v2/` subdirectory alongside the original v1 code.

---

## Motivation

V1 is a fixed pipeline — four hardcoded review workers always run in parallel regardless of what the code actually needs. A trivial 8-line script with only security issues still spawns a performance optimizer and test generator. The supervisor dispatches; it doesn't think.

V2 replaces this with an LLM-driven orchestrator that analyzes the code first and dynamically decides what agents to spawn, what tools to give them, and what to focus on. This is the "deep agent" pattern — the supervisor is itself an agent that reasons about the task before delegating.

---

## What Changed from V1

| Aspect | V1 | V2 |
|--------|----|----|
| **Workers** | 4 hardcoded nodes in `graph.py` | LLM decides at runtime — could be 1, could be 6 |
| **Prompts** | Static strings in `prompts.py` | Orchestrator generates per-agent prompts dynamically |
| **Tools** | Fixed per worker (`SECURITY_TOOLS`, `ANALYZER_TOOLS`, etc.) | Selected from a shared registry per agent based on what the LLM thinks is needed |
| **Fixer** | Single pass → test → loop | Fix → critique (reflection) → retry if rejected → loop |
| **Cache enforcement** | `cache_validator` + `cache_corrector` in fixer loop | Same v1 nodes, reused directly — proves cache rules hold with dynamic topology |
| **Sabotage node** | Present in fixer loop for demo | Removed in v2 — replaced by the critique loop as the quality gate |

---

## Architecture

```
START
  ↓
deep_supervisor (LLM analyzes code, generates agent plan as JSON)
  ↓
dispatch_agent ←────────────────────────┐
  ↓                                     │
execute_agent (runs sub-agent with      │
  dynamic prompt/tools/focus)           │
  ↓                                     │
  ├── more agents pending? ─────────────┘
  ↓
synthesize (merge findings, build report)
  ↓
select_issue ←──────────────────────────┐
  ↓                                     │
fix_llm (apply fix — no tools, text only)│
  ↓                                     │
cache_validator → cache_corrector?      │
  ↓                                     │
critique_fix (reflection — is fix ok?)  │
  ├── rejected → retry_fix → fix_llm    │
  ↓ approved                            │
mark_fixed ─────────────────────────────┘
  ↓ (no issues left)
finalize
  ↓
END
```

**13 nodes total** — comparable to v1's 12, but with fundamentally different behavior.

---

## Key Design Decisions

### 1. Dynamic agent planning via LLM

**Problem:** V1 always spawns 4 workers. For the demo buggy code (SQL injection, MD5, hardcoded key), only security analysis is relevant. Spawning a performance optimizer and test generator wastes tokens and API calls.

**Decision:** The `deep_supervisor` node makes an Anthropic API call with a meta-prompt that instructs the LLM to analyze the code and return a JSON agent plan. The plan specifies agent names, system prompts, tool selections, focus areas, and depth level.

**Why JSON output:** The orchestrator needs structured output to build `AgentSpec` objects. Unstructured text would require fragile parsing. The JSON schema is defined in the prompt so the LLM knows exactly what to produce.

**Result:** In testing, the supervisor consistently spawns 2 agents for the demo code (security + secrets) instead of 4. For more complex code, it would spawn more. The agent count adapts to the task.

### 2. Sequential agent dispatch (not parallel)

**Problem:** V1 runs 4 workers in parallel via `route_supervisor` returning a list. V2's agents are dynamically generated — LangGraph can't fan out to nodes that don't exist at compile time.

**Options explored:**
- **Option A — Dynamic node registration at runtime:** Register new nodes in the graph after the supervisor runs. Not supported by LangGraph — the graph is compiled before execution.
- **Option B — Single `execute_agent` node in a dispatch loop:** One node that pops agents from a queue and executes them sequentially. Simple, no compile-time limitations.
- **Option C — Use `Send` API with dynamic payloads:** Send each agent spec as a separate payload to a generic worker node. Possible, but adds the same parallel state collision issues from v1 (see v1 architecture_decisions.md, Decision 5).

**Decision:** Option B — sequential dispatch loop. `dispatch_agent` pops the next agent from `pending_agents`, `execute_agent` runs it, `route_after_agent` checks if more agents remain. Simpler than parallel, and for 2-4 agents the latency difference is minimal.

**Trade-off:** Sequential execution means agents can't run in parallel. This is acceptable because (a) the number of agents is small (typically 2-4), (b) each agent call takes 2-5 seconds, and (c) the architectural simplicity is worth the ~5-10 second overhead.

### 3. Tool call extraction from sub-agents

**Problem:** When sub-agents are given tools, they try to call them (returning `tool_use` blocks) instead of returning findings as text. The execute_agent node only checked `text` blocks initially, producing 0 findings.

**First fix:** Added logging to see raw response content. Discovered responses contained only `tool_use` blocks — no text.

**Decision:** Parse both `tool_use` and `text` blocks. Tool calls are converted to findings by extracting the tool name and input parameters. A robust `_extract_json` helper handles markdown fences, embedded JSON, and mixed prose+JSON responses.

**Why not simulate tool execution:** These are review tools (scan_security, detect_sql_injection) — they describe what to look for, not actual executables. The LLM's decision to call a specific tool on specific code IS the finding. The tool call itself is the signal.

### 4. Critique/reflection loop instead of test_code

**Problem:** V1's `test_code` validates fixes with `ast.parse()` and a progress check. This catches syntax errors and stalls but can't evaluate whether a fix is semantically correct — it doesn't know if replacing MD5 with SHA256 is actually the right fix for the identified issue.

**Decision:** Replace `test_code` with a `critique_fix` node — a separate LLM call that evaluates the proposed fix against the original code and the identified issue. The critique returns a structured JSON verdict: approved/rejected with explanation.

**Retry mechanism:** If the critique rejects a fix, `retry_fix` rebuilds the prompt with the critique feedback appended. The fixer gets a second (and third) chance before the issue is marked as fixed regardless (escape hatch at 2 retries per issue).

**Why this is "deep":** The fixer doesn't just apply fixes — it gets feedback from a separate evaluator and iterates. This is the evaluator-optimizer pattern: generate → evaluate → refine. The critique agent acts as a quality gate that v1 lacked.

### 5. No tools for the fixer LLM

**Problem:** When `fix_llm` was given `FIX_TOOLS` (apply_fix, run_tests), it would call `apply_fix` with the original code as a parameter rather than returning the fixed code as text. The tool call contained a description of what to fix but not the actual fixed code.

**Decision:** Remove tools from the fixer LLM call entirely. The fix prompt explicitly instructs "return ONLY the complete updated Python code — no explanations, no markdown fences, no tool calls." The fixer returns pure code as text.

**Why this works:** The fixer's job is code transformation, not tool orchestration. It reads the issue description and current code, then outputs the fixed version. Tools added a layer of indirection that prevented the LLM from directly producing the desired output.

### 6. Reusing v1 cache enforcement

**Decision:** V2 imports `cache_validator` and `cache_corrector` directly from `nodes/` (the v1 directory). No duplication, no v2-specific versions.

**Why:** The cache enforcement layer operates on `cached_prefix`, `tool_results`, `tool_definitions_snapshot`, and `current_tool_definitions` — all present in the v2 state schema. The validator/corrector don't care whether the agents upstream were static or dynamic. This proves the cache rules are topology-independent — they hold whether you have 4 hardcoded workers or 2 dynamically spawned agents.

---

## State Schema Changes

V2 extends v1's `CodeReviewState` with new fields in `DeepReviewState`:

```python
# new in v2
agent_plan: list[AgentSpec] | None        # the supervisor's dynamic plan
plan_reasoning: str | None                 # why these agents were chosen
pending_agents: list[AgentSpec] | None     # dispatch queue
active_agent: AgentSpec | None             # currently executing agent
sub_agent_results: list[SubAgentResult]    # accumulated results (operator.add)
fix_critique: str | None                   # critique feedback
fix_approved: bool | None                  # critique verdict
```

All v1 cache enforcement and fixer fields are preserved — `cached_prefix`, `tool_results`, `violation_detected`, etc. This ensures v1's cache validator works without modification.

---

## New TypedDicts

```python
class AgentSpec(TypedDict):
    name: str               # unique identifier
    role: str               # one-line description
    system_prompt: str       # generated by orchestrator
    tools: list[dict]        # selected from tool registry
    focus_areas: list[str]   # what to look for in this code
    depth: str               # "shallow" or "deep"

class SubAgentResult(TypedDict):
    agent_name: str
    findings: list[Finding]
    input_tokens: int
    cache_tokens: int
```

---

## File Structure

```
v2/
├── __init__.py
├── state.py              # DeepReviewState, AgentSpec, SubAgentResult
├── prompts.py            # Meta-prompts for orchestrator, fixer, critique
├── graph.py              # StateGraph wiring — 13 nodes, all edges
├── run.py                # Entry point with streaming output
└── nodes/
    ├── __init__.py
    ├── deep_supervisor.py   # LLM-driven orchestrator
    ├── agent_factory.py     # dispatch_agent, execute_agent, route_after_agent
    ├── deep_fixer.py        # select_issue, fix_llm, critique_fix, retry_fix, mark_fixed
    ├── synthesize.py        # Merge findings from all agents
    └── finalize.py          # Compile final report
```

V2 imports from the root project:
- `state.py` — `Finding`, `TestResults`, `take_last` reducer
- `tools.py` — `ALL_TOOLS`, individual tool groups, `FIX_TOOLS`
- `nodes/cache_validator.py` — reused directly as a graph node
- `nodes/cache_corrector.py` — reused directly as a graph node

---

## Running Both Versions

Both versions coexist in the same checkout and run independently:

```bash
# V1 — fixed pipeline, 4 hardcoded workers, sabotage node
python run.py

# V2 — deep agents, dynamic orchestrator, critique loop
python v2/run.py
```

V1's code at the project root is completely untouched. V2 lives entirely in the `v2/` subdirectory. They share read-only imports from the root but neither modifies the other's state or behavior.

---

## Observed Behavior (Test Run)

**Input:** The same 8-line buggy code from v1 (SQL injection, MD5 hashing, hardcoded API key).

**Phase 1 — Dynamic Planning:**
The supervisor analyzed the code and spawned 2 agents (not 4):
- `sql_crypto_auditor` (deep) — SQL injection + weak crypto, tools: scan_security, detect_sql_injection
- `secrets_detector` (shallow) — hardcoded credentials, tools: detect_secrets, scan_security

**Phase 2 — Agent Execution:**
Both agents produced findings (4 total). Each agent made tool calls that were extracted as findings.

**Phase 3 — Fix with Reflection:**
- Iteration 1: Fixed all 3 issues in one pass (parameterized query, SHA256, os.getenv). Critique **approved**.
- Iterations 2-4: Code already fixed, critique correctly approved remaining issues.

**Phase 4 — Cache enforcement:**
Validator passed on all iterations — no violations detected. Cache enforcement worked correctly with the dynamic agent topology.

---

## What V2 Demonstrates

1. **Dynamic orchestration** — the supervisor is an agent, not a router
2. **Runtime agent creation** — prompts, tools, and focus areas generated per-task
3. **Reflection/critique** — fixes are evaluated before acceptance, not just syntax-checked
4. **Topology-independent cache enforcement** — v1's cache rules hold even when agent count and composition change at runtime
5. **Shared infrastructure** — v2 reuses v1's cache enforcement without modification, proving the enforcement layer is general-purpose
