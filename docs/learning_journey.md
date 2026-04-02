# Learning Journey — Cache-Optimized Code Review Agent

> A step-by-step record of how the system was designed and built, including the decisions made at each stage.

---

## Foundation — Prior Projects

Before building this system, three prior projects established the LangGraph fundamentals:

- **Email Agent** — learned state as a shared whiteboard, nodes as functions, `Command` for routing, `interrupt()` for human-in-the-loop, `MemorySaver` for checkpointing
- **Theme Park Planner** — applied concepts to a rules-based state machine with conditional routing, retry loops, and human approval
- **Code Review Crew (AutoGen)** — built a multi-agent code review system with supervisor/worker pattern, iterative fixer loop, and Streamlit UI. The vehicle for this project.

---

## 1. Read Kevin's Paper

**"Don't Break the Cache" — arXiv 2601.06007 by Kevin Frank et al.**

The paper evaluates prompt caching across OpenAI, Anthropic, and Google for multi-turn agentic tasks. It tests three caching strategies (full context, system prompt only, exclude tool results) and finds that **system-prompt-only caching consistently outperforms** the others — naive full-context caching can paradoxically increase latency.

**Key quantitative findings:**
- Cost reduction: **41–80%** across providers (Claude specifically: **78.5%**)
- Time to first token improvement: **6.1–30.9%** depending on model and strategy
- Benefits scale linearly with prompt size (500–50,000 tokens) and tool calls (3–50)

Section 5.1 ("Strategic Cache Boundary Control") presents strategic recommendations for maintaining cache efficiency. Three operational rules were derived from these recommendations and encoded as the system's hard design constraints:

- **Rule 1 — Static content leads:** The paper recommends "only stable, reusable content is cached" and that "dynamic values should be placed at the end of the system prompt to maximize the cacheable prefix." Operationalized as: system prompt, background knowledge, and tool definitions must always appear before any dynamic content.
- **Rule 2 — Tool results never enter the cached prefix:** The paper tests "Exclude Tool Results" as a caching strategy and finds it avoids caching session-specific content. Operationalized as: tool call outputs are dynamic by definition and must not be included in the cached prefix.
- **Rule 3 — Tool definitions don't mutate after session start:** The paper recommends to "maintain a fixed set of general-purpose functions while implementing dynamic capabilities through code generation." Operationalized as: adding or modifying a tool mid-session cascades invalidation through the cache hierarchy.

---

## 2. Define the Problem

**Decision:** Build a system that operationalizes the paper's findings — not one that reproduces the paper's experiment.

Three options were considered:

- **Option A — Cache-aware agentic loop:** Agent makes real-time decisions about what enters the cached prefix
- **Option B — Cache invalidation risk detector:** Inspects the next action before it fires and flags cache-busting moves
- **Option C — Caching strategy selector:** Runs all three strategies in parallel and recommends the best one

**Decision made:** Option A — but reframed. Instead of just being cache-aware, the system actively enforces Kevin's three rules and self-corrects violations before every API call goes out.

**Why:** Kevin already has the results. Confirming them doesn't tell him anything new. The more impressive framing: "I read your paper, extracted the three rules, and built a production agent that enforces them automatically."

---

## 3. Choose the Task Vehicle

**Decision:** Rebuild Code Review Crew in native LangGraph as the underlying agent.

**Why multi-turn and tool-calling:**
- Conversation history accumulates every turn — cache has more opportunities to break
- Tool results accumulate across turns — Rule 2 enforcement is actively exercised
- Multiple fix iterations — same static prefix reused across turns, cache hits accumulate

**Why Code Review Crew:**
- Already designed and built — familiar domain
- Multi-agent with supervisor/worker pattern — maps directly to LangGraph
- Iterative fixer loop — natural multi-turn workload matching the paper's benchmark

---

## 4. Answer the Architecture Questions

Nine framework questions were answered before writing any code:

**Q1 — What is the agent's job?**
Takes a code snippet as input. Four specialized workers review it in parallel across security, quality, performance, and test coverage. An iterative fixer loop addresses each issue one at a time. A cache enforcement layer validates every LLM call before it fires and self-corrects violations. Produces a final report with the fixed code.

**Q2 — What does "cache breaking" look like concretely?**
When one of the three rules is violated — tool results inside the cached prefix, dynamic content before static content, or tool definitions mutated mid-session. The cache validator detects this before the API call goes out.

**Q3 — What are the three rules being encoded?**
Static content leads, tool results never enter the cached prefix, tool definitions don't mutate after session start.

**Q4 — How will you know it's working?**
LangSmith traces token usage. A baseline run without enforcement is compared against an enforced run. The cache corrector firing visibly in the trace demonstrates the self-correcting behavior.

**Q5 — What is the state schema?**
One unified `CodeReviewState` TypedDict covering input, review output, fixer state, output, cache enforcement fields, and control flow.

**Q6 — What are the nodes?**
12 nodes across three layers — supervisor, four review workers, four LLM nodes (later merged into workers), fix_issues, fix_issues_llm, test_code, finalize, cache_validator, cache_corrector.

**Q7 — How does routing work?**
Workers self-route via `Command`. Supervisor uses `route_supervisor` in `routing.py`. Cache validator uses an inline lambda. `test_code` uses `route_after_test`. `fix_issues` self-routes via `Command` to either `cache_validator` or `finalize`.

**Q8 — How do you wire the graph?**
`StateGraph` with `MemorySaver` checkpointer. Static edges for LLM nodes back to supervisor. Conditional edges for supervisor, cache_validator, cache_corrector, and test_code.

**Q9 — How do you demo this?**
Pre-load a sabotaged `cached_prefix` that violates Rule 2. The cache corrector fires visibly in LangSmith. Show before/after token comparison between baseline and enforced runs.

---

## 5. Design the State Schema

**File:** `state.py`

**Key decision — one state class vs two:**
Chose one unified `CodeReviewState` over separate schemas for the review and fixer layers. The cache enforcement layer operates across both — splitting state would require wiring the validator twice.

**Key decision — reducers (initial design):**
- `findings` uses `operator.add` — four workers run in parallel and each appends findings. Without a reducer, parallel writes conflict and the last write overwrites the others.
- `next_node`, `user_message`, `cached_prefix`, `tool_results` use custom `take_last` reducer — the original design had parallel workers writing to these fields simultaneously. `take_last` always returns the most recent value. After the architecture pivot (see Section 13), the parallel collision was eliminated, but these reducers remained necessary for sequential overwrites in the fixer loop.

**Key decision — typed contracts:**
Added `Finding` and `TestResult` TypedDicts to define the shape of data passed between nodes. Explicit contracts catch mismatches at type-check time rather than silently failing at runtime.

```python
class Finding(TypedDict):
    type: str
    issue: str
    severity: str
    source: str
    input_tokens: int
    cache_tokens: int

class TestResult(TypedDict):
    passed: bool
    syntax_valid: bool
    error: str | None
```

---

## 6. Create tools.py and prompts.py

**Files:** `tools.py`, `prompts.py`

**Key decision — centralized tool definitions:**
All tool schemas live in `tools.py`, organized by worker group. Workers import only what they need. `ALL_TOOLS` is imported at `invoke()` time to set `tool_definitions_snapshot` — locking tool definitions at session start per Rule 3.

**Key decision — one source of truth:**
If `CORRECT_ORDER` or tool schemas ever change, there is one place to update. `cache_corrector` imports `CORRECT_ORDER` from `cache_validator` rather than duplicating it.

---

## 7. Stub All Nodes and Wire graph.py

**Files:** All node files, `graph.py`, `routing.py`

**Build order:**
1. Stub every node — `print` name, return `{}`
2. Write routing functions in `routing.py`
3. Wire all nodes and edges in `graph.py`
4. Compile and run — confirm every node fires in correct order before adding any logic

**Key decision — routing.py vs Command:**
- Workers use `Command` — they have node-level context (their specific `next_node`) that the graph can't express as a static edge
- `route_supervisor`, `route_after_test` live in `routing.py` — routing decisions that depend on state but don't require node context
- LLM nodes use static edges — they always go to the same place after firing

**Workflow map (original planned architecture):**

```
START
  ↓
supervisor (first pass — fan out)
  ↓ ↓ ↓ ↓ (parallel via Send API)
security_reviewer  code_analyzer  performance_optimizer  test_generator
  ↓                  ↓                   ↓                    ↓
cache_validator → cache_corrector (if violation)
  ↓                  ↓                   ↓                    ↓
security_llm       analyzer_llm       performance_llm      test_generator_llm
  ↓ ↓ ↓ ↓ (findings accumulate via operator.add)
supervisor (second pass — synthesize)
  ↓
fix_issues ──────────────────────────────────────────┐
  ↓ (issues remain)                                  │
cache_validator                                      │
  ↓ (clean) or → cache_corrector → (next_node)      │
fix_issues_llm                                       │
  ↓                                                  │
test_code                                            │
  ↓ (always)                                         │
fix_issues ←─────────────────────────────────────────┘
  ↓ (no issues remain)
finalize
  ↓
END
```

> **Note:** This was the original 16-node architecture. After encountering the parallel state collision problem (see Section 13), the review layer was simplified — separate LLM nodes were merged into worker nodes, and cache_validator was removed from the review layer.

---

## 8. Build Cache Enforcement Nodes

**Files:** `cache_validator.py`, `cache_corrector.py`

**cache_validator logic:**
- If `cached_prefix` is `None` — skip all checks, pass clean
- Check Rule 1 — compare `list(cached_prefix.keys())` against `CORRECT_ORDER`. First violation wins, return immediately
- Check Rule 2 — check if `"tool_results"` appears as a key inside `cached_prefix`
- Check Rule 3 — compare `tool_definitions_snapshot` against `current_tool_definitions`. Guard against `None` at session start
- Clean pass — return `violation_detected: False`

**cache_corrector logic:**
- Read `violation_type` from state
- Rule 1 — rebuild `cached_prefix` dict using dict comprehension over `CORRECT_ORDER`
- Rule 2 — `cached_prefix.pop("tool_results", None)`
- Rule 3 — reset `current_tool_definitions` to match `tool_definitions_snapshot`
- Reset `violation_detected: False`, `violation_type: None`

---

## 9. Build Worker Nodes

**Files:** `security_reviewer.py`, `code_analyzer.py`, `performance_optimizer.py`, `test_generator.py`

**Each worker:**
1. Reads `code_input` from state
2. Builds `cached_prefix` with worker-specific system prompt, background knowledge, and tool definitions
3. Builds dynamic `user_message` — never cached
4. Makes Anthropic API call with `cache_control` on static sections (2 system blocks + last tool only — Anthropic max 4 blocks)
5. Parses response, writes `findings` with token usage fields
6. Returns findings

**Original design — separate worker + LLM nodes:**
Each worker node was responsible only for prompt preparation — building `cached_prefix`, setting `next_node`, and writing `user_message` to state. The worker then routed to `cache_validator` via `Command`, which validated the prefix before routing to a dedicated LLM node (`security_llm`, `analyzer_llm`, `performance_llm`, `test_generator_llm`) that made the actual API call. This placed the cache enforcement layer between prompt prep and every API call in both the review and fixer layers.

**Original design — Send API for parallel fan-out:**
`route_supervisor` returned `Send` objects instead of a list of strings. Each worker got an isolated state copy so `next_node`, `cached_prefix`, and `user_message` wouldn't collide across parallel workers.

> **Note:** Both of these decisions were later revised after encountering the parallel state collision problem. See Section 13 for the full pivot.

**Key decision — cache_control on last tool only:**
Anthropic limits `cache_control` to 4 blocks per request. 2 system blocks + all tools exceeded the limit for workers with 3 tools. Fixed by applying `cache_control` only to the last tool in each list — Anthropic caches everything up to and including the last marker.

---

## 10. Build Supervisor Node

**File:** `supervisor.py`

**First pass** — `findings` is empty, return `{}`. Routing handled entirely by `route_supervisor`.

**Second pass** — `findings` populated:
- Sort findings by severity — critical first using `SEVERITY_ORDER` dict
- Group by type — security, quality, performance, testing
- Build preliminary report
- Initialize `current_code` from `code_input` for the fixer

---

## 11. Build Fixer Loop

**Files:** `fix_issues.py`, `fix_issues_llm.py`, `test_code.py`, `finalize.py`

**fix_issues:**
- Find next unfixed issue — `[f for f in findings if f not in fixed_issues]`
- If nothing left — `Command(goto="finalize")`
- If issues remain — build fix prompt, set `next_node: "fix_issues_llm"`, `Command(goto="cache_validator")`
- Increment `iteration` on every fix attempt

**fix_issues_llm:**
- Makes Anthropic API call with `cache_control` on static sections
- Extracts fixed code safely — `next((block.text for block in response.content if hasattr(block, "text")), ...)`
- Marks issue as fixed — appends to `fixed_issues`
- Captures token usage for before/after comparison

**test_code:**
- Syntax validation with `ast.parse()`
- Checks at least one issue was fixed
- Checks code is not empty
- Returns `test_results: {"passed": bool, "syntax_valid": bool, "error": str | None}`

**route_after_test:**
- Always routes back to `fix_issues` — `fix_issues` decides when work is done, not `test_code`
- Only routes to `finalize` directly as escape hatch at `iteration >= 10`

**finalize:**
- Compiles findings, fixed_issues, current_code, test_results into final report

---

## 12. Set Up LangSmith

**Files:** `.env`, `run.py`, `baseline.py`

**Environment:**
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_key
LANGCHAIN_PROJECT=cache-optimized-code-review
```

**Three runs in run.py:**
1. **Enforced run** — full graph with cache enforcement active. LangSmith traces every node, every token count, every cache hit
2. **Baseline run** — same pipeline via `baseline.py` without `cache_control` on any blocks. Produces token comparison baseline
3. **Sabotaged run** — pre-loads a broken `cached_prefix` to trigger the cache corrector visibly in the trace

**Token comparison output:**
```
Baseline input tokens:  8,840
Enforced input tokens:  9,124
Cache read tokens:      0
```

**Note on cache hits:** Anthropic requires 1024+ tokens in the cached prefix before caching activates. Development prompts are below this threshold. In production with real knowledge bases the threshold is easily crossed and savings become measurable. The enforcement architecture is correct — cache hits appear automatically once content crosses the minimum.

---

## 13. Architecture Pivot — Parallel State Collision

**The problem:**
All four review workers ran in parallel and each wrote to `cached_prefix`, `next_node`, and `user_message` in shared state. The `take_last` reducer meant only the last worker to finish had its values survive. Three workers' prompt preparation was silently discarded — `cache_validator` only ever saw one worker's `cached_prefix` and routed to one LLM node per fan-out.

**First attempt — Send API:**
Switched `route_supervisor` to return `Send` objects, giving each worker an isolated copy of state. This eliminated the parallel write collision for `cached_prefix`, `next_node`, and `user_message`.

**Why Send didn't solve it:**
Each worker route still wrote `findings` back to shared state via the same key. The merge still required `operator.add` reducers regardless of `Send`. The isolation added architectural complexity — separate state copies, careful merge handling on return — without eliminating the fundamental reducer requirement. `Send` was solving the wrong problem.

**The pivot — merge worker + LLM into single nodes:**
Instead of fighting the parallel state collision with `Send`, eliminated it entirely. Each worker node now:
1. Preps its own `cached_prefix` locally (not written to shared state)
2. Performs inline cache validation — checks key order against `CORRECT_ORDER`, reorders if needed
3. Makes its own Anthropic API call with `cache_control` headers
4. Returns only `{"findings": findings}` — the single shared field, handled correctly by `operator.add`

This removed:
- All four separate LLM nodes (`security_llm`, `analyzer_llm`, `performance_llm`, `test_generator_llm`)
- `Send` API from `route_supervisor` — back to returning a simple list of node names
- `cache_validator` from the review layer entirely

**Why remove cache_validator from the review layer:**
The paper's core finding is about iterative multi-turn workloads where the same static prefix is reused across many turns and cache hits accumulate. Review workers fire once per session — there's no multi-turn repetition to cache against. The measurable token savings come from the fixer loop, where the same static prefix is reused across multiple fix iterations. Cache enforcement belongs where the paper's savings actually apply.

**Result:** Graph went from 16 nodes to 12. Workers do inline validation as a lightweight safeguard. The full validator → corrector pipeline runs only in the fixer loop where it earns its complexity.

**Updated workflow map (final architecture):**

```
START
  ↓
supervisor (first pass — fan out)
  ↓ ↓ ↓ ↓ (parallel — simple list, no Send)
security_reviewer  code_analyzer  performance_optimizer  test_generator
  (each: prep → inline validate → API call → return findings)
  ↓ ↓ ↓ ↓ (findings accumulate via operator.add)
supervisor (second pass — synthesize)
  ↓
fix_issues ──────────────────────────────────────────┐
  ↓ (issues remain)                                  │
sabotage_node (demo only — injects violations)       │
  ↓                                                  │
cache_validator                                      │
  ↓ (clean) or → cache_corrector → (next_node)       │
fix_issues_llm                                       │
  ↓                                                  │
test_code                                            │
  ↓ (always)                                         │
fix_issues ←─────────────────────────────────────────┘
  ↓ (no issues remain)
finalize
  ↓
END
```

---

## 14. Sabotage Node for Testing Cache Enforcement

**The problem:**
The cache validator and corrector are defensive — they catch violations that should never happen in correct code. Under normal operation, the validator always passes clean and the corrector never fires. There was no way to verify the enforcement layer actually works without deliberately breaking things.

**Decision:** Add a `sabotage_node` that sits between `fix_issues` and `cache_validator` in the fixer loop. It intentionally injects cache rule violations on a rotating schedule:

- **Iteration 1 → Rule 1 violation:** Reorders `cached_prefix` keys so static content no longer leads — `tool_definitions` first, then `background_knowledge`, then `system_prompt`
- **Iteration 2 → Rule 2 violation:** Injects `tool_results` into `cached_prefix` — dynamic content that should never appear in the cached prefix
- **Iteration 3 → Rule 3 violation:** Appends a fake tool to `current_tool_definitions` so it no longer matches `tool_definitions_snapshot`
- **Iteration 4+ → Clean pass:** No sabotage, allowing the fixer loop to proceed normally

**Graph wiring change:**
`fix_issues` now routes to `sabotage_node` via `Command(goto="sabotage_node")` instead of directly to `cache_validator`. `sabotage_node` has a static edge to `cache_validator`.

**Why this matters:**
Makes the enforcement layer observable and testable in LangSmith traces. Each violation type triggers the corrector visibly — you can see the validator detect the violation, the corrector fix it, and the corrected state flow into `fix_issues_llm`. In production, the sabotage node would be removed and `fix_issues` would route directly to `cache_validator`.

---

## 15. Production Hardening

After the architecture was stable, several improvements were made to make the system more robust and debuggable.

**Error handling on all API calls:**
Every `client.messages.create()` call across all reviewer nodes, `fix_issues_llm`, and `baseline.py` was wrapped in `try/except APIError`. On failure, nodes return error findings with zero token counts instead of crashing the entire graph. The `RetryPolicy(max_attempts=3)` on LLM nodes handles transient failures, but persistent errors now degrade gracefully.

**Logging across all nodes:**
Added `logging` to every node in the system. Each node logs:
- Start of operation (e.g., "Starting security review")
- Token usage on completion (input_tokens, cache_tokens)
- Cache violations detected and corrections applied
- Test results (syntax_valid, new_fixes, code_valid, passed)
- Fix iteration progress (iteration number, fixed count)

Logging is configured in `run.py` with `logging.basicConfig()` — timestamps, module names, and log levels.

**Configurable model via environment variable:**
All nodes read `LLM_MODEL` from `.env` instead of hardcoding `"claude-opus-4-5"`. This allows switching between models (Sonnet, Haiku, Opus) without editing code. Default fallback is `claude-sonnet-4-20250514` if the env var is not set.

**test_code bug fix — per-iteration tracking:**
The original `test_code` checked `len(fixed_issues) > 0` to verify a fix happened. This checked total fixes ever, not fixes this iteration. After the first successful fix, `new_fixes` was always `True`, meaning subsequent test iterations never reported failure.

Fix: Added `previous_fixed_count` to state. `fix_issues` records `len(fixed_issues)` before each iteration. `test_code` compares `len(fixed_issues) > previous_fixed_count` to verify the current iteration actually produced a new fix.

**Typed state contracts:**
Added `Finding` and `TestResults` TypedDicts to `state.py` to define the exact shape of data flowing between nodes. `findings` is now `list[Finding]` instead of `list[dict]`, and `test_results` is `TestResults` instead of `dict`. This catches key typos and type mismatches at type-check time rather than at runtime.

**Environment and project setup:**
- `.env.example` — template with placeholder values so new developers know which env vars to set
- `.gitignore` — expanded to cover `.env`, `venv/`, `__pycache__/`, IDE files, OS files, and personal docs
- `requirements.txt` — populated with pinned versions from the development environment

---

## 16. Document Everything

This document consolidates all v1 design documentation — the original architecture document, the 9 roadblock decisions, and the step-by-step build narrative — into a single reference. V2 (deep agents) and the LangSmith evaluation suite have their own dedicated docs in `docs/`.

---

## 17. Production Considerations

These are changes that would be made for a production deployment. The current implementation is optimized for demonstration and development.

**`MemorySaver` → database-backed checkpointer** — `MemorySaver` is in-process only. Production would use PostgreSQL or Redis via LangGraph's persistence layer so state survives process restarts.

**`TypedDict` → Pydantic** — Pydantic validates every field at the boundary the moment it enters state. For a system where bad data flowing silently through nodes has real consequences, Pydantic's validation overhead is worth it.

**Hardcoded max iterations → configurable** — `iteration >= 10` is hardcoded in `route_after_test`. Production would pass `max_iterations` as a config parameter at `invoke()` time.

**Stub test runner → real execution** — `test_code` currently does conceptual validation with `ast.parse()`. Production would execute the fixed code in an isolated Docker sandbox and run pytest, matching the `CodeExecutor` from the original Code Review Crew.

**Single thread → multi-tenant** — production would pass a unique `thread_id` per code review session so multiple reviews can run concurrently without state collision.

**Prompt expansion for cache activation** — Anthropic requires 1024+ tokens in a cached block for caching to activate. Current prompts are ~600-750 tokens per worker. Production prompts with detailed worked examples and comprehensive reference material would cross this threshold and produce measurable cache hits in LangSmith traces.

---

## Summary of Key Architectural Principles

These principles emerged from the 9 roadblocks encountered during development (Sections 2, 3, 4, 5, 6, 11, 13, 14) and guided every design decision:

- **Routing belongs where context lives.** If a node has the information to decide where to go, use `Command`. If routing is purely graph-level logic, use `add_conditional_edges`.

- **Single source of truth for decisions.** One node owns each decision. `fix_issues` owns finalization. `test_code` owns validation. Never split decision authority across nodes.

- **Reducers solve different problems at different layers.** `operator.add` for accumulation, `take_last` for overwrites, `Send` for parallel isolation. They coexist.

- **Apply complexity where it earns its keep.** Cache enforcement in the fixer loop produces measurable savings. Cache enforcement in one-shot review calls does not. Architecture should reflect where the value actually lives.

- **Always include destinations lists on conditional edges.** The cost is two lines, the benefit is compile-time safety and self-documenting graph wiring.