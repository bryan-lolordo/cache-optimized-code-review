# V2 — What Changed and Why

> Everything the v2 branch expands upon from v1: deep agents, parallel execution, structured tool-calling, LangSmith evaluation, LangChain skills, and LangGraph Platform deployment. Built on the `v2-deep-agents` branch.

---

## Overview

V1 is a fixed pipeline — four hardcoded workers, a linear fixer, and cache enforcement that proves the paper's rules work. V2 asks: what if the system could think about what it needs before it starts working?

Five expansions were made on the `v2-deep-agents` branch:

1. **Deep agent architecture** — an LLM-driven orchestrator that dynamically decides what agents to spawn
2. **Parallel execution via Send** — all dynamically spawned agents run concurrently, not sequentially
3. **Structured tool-calling** — agents report findings via a typed `report_finding` tool instead of fragile JSON parsing
4. **LangSmith evaluation suite** — quantitative comparison of v1 vs v2 on the same test dataset
5. **LangGraph Platform deployment** — both graphs served as API endpoints with Studio UI

Plus **LangChain skills** — 11 coding agent instruction files for expert LangChain/LangGraph knowledge.

---

## 1. Deep Agent Architecture

### What changed from V1

| Aspect | V1 | V2 |
|--------|----|----|
| **Workers** | 4 hardcoded nodes in `graph.py` | LLM decides at runtime — could be 1, could be 6 |
| **Dispatch** | Parallel fan-out via list return | Parallel fan-out via `Send` API with dynamic payloads |
| **Prompts** | Static strings in `prompts.py` | Orchestrator generates per-agent prompts dynamically |
| **Tools** | Fixed per worker (`SECURITY_TOOLS`, `ANALYZER_TOOLS`, etc.) | Selected from registry per agent + shared `report_finding` tool |
| **Fixer** | Single pass → test → loop | Fix → critique (reflection) → retry if rejected → loop |
| **Cache enforcement** | `cache_validator` + `cache_corrector` in fixer loop | Same v1 nodes, reused directly — proves cache rules hold with dynamic topology |
| **Sabotage node** | Present in fixer loop for demo | Removed — replaced by the critique loop as the quality gate |

### Architecture

```
START
  ↓
deep_supervisor (LLM analyzes code, generates agent plan as JSON)
  ↓
route_to_agents (returns Send objects for parallel fan-out)
  ↓ ↓ ↓ (parallel via Send API)
execute_agent  execute_agent  execute_agent
  (each with dynamic prompt/tools/focus + report_finding tool)
  ↓ ↓ ↓ (findings merge via operator.add)
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
finalize → END
```

12 nodes total. All dynamically spawned agents execute in parallel — verified by timestamps showing simultaneous starts.

### Key decisions

**Dynamic agent planning via LLM.** The `deep_supervisor` makes an API call with a meta-prompt that analyzes the code and returns a JSON agent plan — agent names, system prompts, tool selections, focus areas, and depth level. In testing, the supervisor spawns 2-3 agents for the demo code instead of v1's fixed 4. The agent count adapts to the task.

**Parallel execution via Send API.** The `route_to_agents` routing function returns a list of `Send("execute_agent", payload)` objects — one per agent. LangGraph dispatches all of them concurrently to the same `execute_agent` node, each with an isolated state payload containing the `AgentSpec` and `code_input`. Findings merge back via `operator.add` on the `findings` field — the same reducer pattern v1 uses for its parallel fan-out.

**Why Send works here but not in v1's original design:** V1's parallel state collision (see learning_journey Section 13) happened because workers wrote to shared fields like `cached_prefix` and `next_node`. V2's `execute_agent` only writes to `findings` and `sub_agent_results` — both have `operator.add` reducers. Send gives each agent isolated input, and the only shared output fields have proper accumulation reducers. No collision.

**Structured tool-calling with `report_finding`.** Every agent receives a `report_finding` tool in addition to its specialist tools (scan_security, detect_sql_injection, etc.). The tool has typed fields: `issue` (string description), `severity` (enum: critical/high/medium/low), and `line` (line number). When agents call this tool, the finding is extracted directly from the structured input — no JSON parsing needed. Agents can also use their specialist tools; the extraction handles both `report_finding` calls and other tool calls as fallback.

**Critique/reflection loop.** V1's `test_code` only checks syntax with `ast.parse()`. V2 replaces it with `critique_fix` — a separate LLM call that evaluates whether the fix is semantically correct. Rejected fixes get retried with the critique feedback appended to the prompt. This is the evaluator-optimizer pattern: generate → evaluate → refine.

**No tools for the fixer.** When `fix_llm` was given tools, it would call `apply_fix` instead of returning fixed code as text. Removing tools and prompting for "return ONLY the complete updated Python code" solved it. The fixer's job is code transformation, not tool orchestration.

**Reusing v1 cache enforcement.** V2 imports `cache_validator` and `cache_corrector` directly from the v1 `nodes/` directory. The enforcement layer operates on state fields that both schemas share. This proves cache rules are topology-independent — they hold whether agents are static or dynamic.

### State schema

V2 extends v1's `CodeReviewState` with `DeepReviewState`:

```python
# new in v2
agent_plan: list[AgentSpec] | None        # supervisor's dynamic plan
plan_reasoning: str | None                 # why these agents were chosen
sub_agent_results: list[SubAgentResult]    # accumulated results (operator.add)
fix_critique: str | None                   # critique feedback
fix_approved: bool | None                  # critique verdict
```

All v1 cache enforcement and fixer fields are preserved. V1's cache validator works without modification. Send handles agent dispatch — no queue or active agent fields needed.

### File structure

```
v2/
├── state.py              # DeepReviewState, AgentSpec, SubAgentResult
├── prompts.py            # Meta-prompts for orchestrator, fixer, critique
├── graph.py              # StateGraph wiring — 12 nodes, Send fan-out
├── run.py                # Entry point with streaming output
└── nodes/
    ├── deep_supervisor.py   # LLM-driven orchestrator + route_to_agents (Send)
    ├── agent_factory.py     # execute_agent with report_finding extraction
    ├── deep_fixer.py        # select_issue, fix_llm, critique_fix, retry_fix, mark_fixed
    ├── synthesize.py        # Merge findings from all agents
    └── finalize.py          # Compile final report
```

V2 imports from the root: `state.py`, `tools.py` (including `REPORT_FINDING_TOOL`), `nodes/cache_validator.py`, `nodes/cache_corrector.py`.

---

## 2. LangSmith Evaluation Suite

### Why

Both v1 and v2 produce the same outputs — findings, fixed code, and a final report. "V2 feels better" isn't evidence. The eval suite creates a shared test dataset, runs both pipelines against it, scores the results with custom evaluators, and produces a side-by-side comparison in the LangSmith dashboard.

### Test dataset

Six code samples with ground truth expected findings:

| # | Category | What's in the code | Expected |
|---|----------|-------------------|----------|
| 1 | security-only | SQL injection, MD5, hardcoded key | 3 findings |
| 2 | performance-only | O(n²) loops, string concat, O(n³) matrix | 3 findings |
| 3 | mixed security+quality | `eval()`, command injection, bare except, plaintext passwords | 4 findings |
| 4 | mixed perf+quality | Deep nesting, duplicate logic, string concat, 7 params | 4 findings |
| 5 | clean code | Parameterized queries, PBKDF2, `hmac.compare_digest` | 0 (false positive test) |
| 6 | subtle security | SSRF, timing side-channel, path traversal | 3 findings |

Ground truth uses LLM-based semantic matching — Claude evaluates whether each actual finding describes the same issue as the expected finding, even if worded differently. Falls back to keyword matching if the LLM call fails.

### Evaluators

| Evaluator | Measures | Score |
|-----------|---------|-------|
| **finding_recall** | Did it find expected issues? LLM-based semantic matching against ground truth | 0.0–1.0 |
| **fix_correctness** | Does fixed code resolve issues? LLM-graded via Anthropic SDK | 0.0–1.0 |
| **token_efficiency** | Cache hit ratio (cache_tokens / input_tokens) | 0.0–1.0 |
| **agent_efficiency** | Findings per agent spawned (v2 only, N/A for v1) | float |
| **preference_evaluator** | Which pipeline is better overall? LLM-judged comparison | winner |

### Results (first full run, 2026-04-02)

**fix_correctness — V2 wins decisively:**

| Sample | V1 | V2 |
|--------|----|----|
| security-only | 0.50 | **1.00** |
| performance-only | 0.00 | **1.00** |
| mixed-security-quality | 0.00 | **1.00** |
| mixed-performance-quality | 0.00 | **1.00** |
| clean-code | 0.00 | **1.00** |
| subtle-security | 0.00 | **1.00** |

**Why V2 wins:** The critique/reflection loop catches when the fixer returns unchanged code and retries with feedback. V1's fixer has no quality gate — `test_code` only checks syntax, not semantic correctness.

**Dynamic agent behavior (from LangSmith traces):**

| Sample | V2 agents spawned |
|--------|-------------------|
| security-only | 1-2 (security auditor, secrets detector) |
| performance-only | 2-3 (loop optimizer, string perf analyzer) |
| mixed-security-quality | 3 (eval auditor, injection tracer, auth reviewer) |
| clean-code | 2-3 (crypto auditor, query validator, secrets scanner) |
| subtle-security | 3 (timing_attack_auditor, path_traversal_hunter, ssrf_validator) |

V1 always spawns exactly 4 workers regardless of the code.

### File structure

```
evals/
├── dataset.py         # 6 test samples with ground truth
├── evaluators.py      # 4 custom evaluators + comparative preference
└── run_eval.py        # Main harness — wraps graphs, runs evaluate(), prints table
```

### Viewing results in LangSmith

1. Go to **https://smith.langchain.com** → **Datasets & Testing**
2. Find **"code-review-v1-vs-v2"**
3. Click to see experiments: `code-review-v1-fixed-workers` and `code-review-v2-deep-agents`
4. Click **Compare** for side-by-side view, or click into any run for the full trace

---

## 3. LangGraph Platform Deployment

### What it is

LangGraph Platform turns your graphs into hosted API endpoints. Instead of running `python v2/run.py` locally, you call your graph over HTTP with streaming, checkpointing, and monitoring built in.

### Configuration

The platform is configured via `langgraph.json` at the project root:

```json
{
  "graphs": {
    "v1_agent": "graph:graph",
    "v2_agent": "v2.graph:graph"
  },
  "dependencies": ["requirements.txt"],
  "env": ".env"
}
```

- **`graphs`** — maps a name to a `module:variable` import path. Both v1 and v2 are served simultaneously.
- **`dependencies`** — what to install. Points to `requirements.txt`.
- **`env`** — where API keys live (Anthropic, LangSmith, etc.).

### Key decision: no checkpointer in deployed graphs

The platform manages persistence automatically. Graphs compiled with `MemorySaver` will be rejected — the platform has its own storage backend. To support both local development and platform deployment, both `graph.py` and `v2/graph.py` now export two versions:

```python
# for LangGraph Platform (no checkpointer — platform handles it)
graph = workflow.compile()

# for local run.py scripts (MemorySaver for checkpointing)
graph_local = workflow.compile(checkpointer=checkpointer)
```

`langgraph.json` points to `graph` (no checkpointer). `run.py` and `v2/run.py` import `graph_local`.

### How to run

```bash
# start the local dev server (requires Docker)
python -m langgraph_cli dev
```

This starts:
- **API** at `http://127.0.0.1:2024`
- **Studio UI** at `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`
- **API Docs** at `http://127.0.0.1:2024/docs`

### Studio UI

LangGraph Studio is a visual IDE connected to your deployment. You can:
- Select `v1_agent` or `v2_agent` from the graph dropdown
- Send code input and watch the graph execute node-by-node
- Inspect state at each step
- View the full execution trace

### Testing via API

```bash
# list available assistants
curl -s -X POST http://127.0.0.1:2024/assistants/search \
  -H "Content-Type: application/json" -d '{}'

# create a thread
curl -s -X POST http://127.0.0.1:2024/threads \
  -H "Content-Type: application/json" -d '{}'

# stream a run
curl -s -X POST http://127.0.0.1:2024/threads/{thread_id}/runs/stream \
  -H "Content-Type: application/json" \
  -d '{"assistant_id": "v2_agent", "input": {"code_input": "..."}, "stream_mode": "updates"}'
```

---

## 4. LangChain Skills

### What they are

LangChain Skills are curated markdown instruction files that enhance coding agents (Claude Code, Cursor, Copilot) with expert knowledge of LangChain/LangGraph patterns. They load into the coding agent's context when working on relevant tasks — they don't affect runtime code.

The blog post ([langchain.com/blog/langchain-skills](https://blog.langchain.com/langchain-skills/)) reports Claude Code performance improved from 29% to 95% on LangChain tasks when using skills.

### Installed skills (11 total)

| Category | Skills |
|----------|--------|
| **Deep Agents** | deep-agents-core, deep-agents-memory, deep-agents-orchestration |
| **LangChain** | langchain-fundamentals, langchain-middleware, langchain-dependencies, langchain-rag |
| **LangGraph** | langgraph-fundamentals, langgraph-human-in-the-loop, langgraph-persistence |
| **General** | framework-selection |

### Where they live

```
.agents/skills/
├── deep-agents-core/SKILL.md
├── deep-agents-memory/SKILL.md
├── deep-agents-orchestration/SKILL.md
├── framework-selection/SKILL.md
├── langchain-dependencies/SKILL.md
├── langchain-fundamentals/SKILL.md
├── langchain-middleware/SKILL.md
├── langchain-rag/SKILL.md
├── langgraph-fundamentals/SKILL.md
├── langgraph-human-in-the-loop/SKILL.md
└── langgraph-persistence/SKILL.md
```

### Install command

```bash
npx skills add langchain-ai/langchain-skills --skill '*' --yes
```

---

## Running Everything

All versions coexist in the same checkout:

```bash
# V1 — fixed pipeline, 4 hardcoded workers, sabotage node
python run.py

# V2 — deep agents, parallel Send, critique loop
python v2/run.py

# Evaluation — compare v1 vs v2 on 6 test samples
python -m evals.run_eval

# Platform — serve both graphs as API endpoints with Studio UI
python -m langgraph_cli dev
```

V1's code at the project root is untouched. V2 lives in `v2/`. Evals live in `evals/`. Skills live in `.agents/skills/`. Platform config is `langgraph.json`. They share read-only imports from the root but don't modify each other.

---

## 5. V3 — Deep Agents SDK

### Why

V2 proves that dynamic agent planning and critique loops produce better results. But building it required 12 hand-wired nodes, raw Anthropic API calls, manual `Send` fan-out, and a 25-field state schema. The Deep Agents SDK (`deepagents` package) wraps all of that into a single `create_deep_agent()` call with built-in middleware for planning, delegation, and file context.

V3 asks: what if v2's capabilities could be expressed in ~200 lines instead of ~500?

### What changed from V2

| Aspect | V2 | V3 |
|--------|----|----|
| **Graph wiring** | 12-node `StateGraph` with manual edges | Single `create_deep_agent()` call |
| **LLM calls** | Raw `Anthropic()` client with `cache_control` | SDK manages model calls via `langchain-anthropic` |
| **Parallel dispatch** | Manual `Send` API fan-out with isolated payloads | `task` tool delegates to named subagents |
| **State schema** | 25-field `DeepReviewState` TypedDict | SDK built-in state + 5 custom tools |
| **Planning** | Implicit in graph topology | `write_todos` middleware tracks review phases |
| **Cache enforcement** | Manual 3-rule validator + corrector | SDK handles prompt construction internally |
| **Files** | 9 files, ~500 lines | 4 files, ~200 lines |

### Architecture

```
create_deep_agent() orchestrator
  |
  |-- write_todos (plan review phases)
  |
  |-- task(security_reviewer)    ─┐
  |-- task(performance_reviewer)  │ subagent delegation
  |-- task(quality_reviewer)      │ (SDK handles lifecycle)
  |-- task(test_reviewer)        ─┘
  |
  |-- report_finding (record each issue)
  |
  |-- apply_fix (fix each issue iteratively)
  |-- task(fix_critic) (validate each fix)
  |-- apply_fix (retry if rejected, max 2)
  |
  |-- get_review_summary (compile final report)
```

No graph nodes, no edges, no conditional routing. The LLM orchestrator decides the flow — the SDK provides the tools and middleware.

### Key decisions

**Subagents as text reporters.** V2 gave each agent a `report_finding` tool. V3 subagents have `tools=[]` — they analyze code and return a text report. The orchestrator parses the report and calls `report_finding` itself. This avoids duplicate findings (subagent tool calls + orchestrator recording) and keeps the subagent interface simple.

**Custom tools for structured state.** Five `@tool` functions manage review state: `report_finding`, `get_findings`, `apply_fix`, `get_current_code`, `get_review_summary`. These give the orchestrator structured I/O while the SDK handles the agent loop.

**TodoListMiddleware for planning.** The orchestrator calls `write_todos` at each phase transition (delegate → record → fix → validate → report). This replaces v2's implicit graph topology as the planning mechanism, and the todo state is visible in the stream output.

**SubAgentMiddleware for delegation.** Five named subagents (4 reviewers + 1 critic) are configured at creation time. The orchestrator chooses which to invoke based on the code — same adaptive behavior as v2's dynamic planning, but with predefined specialist roles.

### File structure

```
v3/
├── agent.py     # create_deep_agent orchestrator + 5 subagent configs
├── prompts.py   # System prompts for orchestrator, 4 reviewers, fix critic
├── tools.py     # Custom tools: report_finding, apply_fix, get_review_summary
└── run.py       # Entry point with streaming output
```

### Running

```bash
# local
python v3/run.py

# LangGraph Platform (alongside v1 and v2)
python -m langgraph_cli dev
```

### Results (first run)

The v3 agent on the same 3-bug demo code (SQL injection, MD5, hardcoded key):
- Delegated to `security_reviewer` and `quality_reviewer` (skipped performance and test — correct for this code)
- Recorded 6 unique findings: 1 critical, 2 high, 2 medium, 1 low
- Applied all 6 fixes in one pass
- Fix critic approved all fixes
- 16 API calls, ~1.5 minutes total
- Final code: parameterized queries, PBKDF2 with salt, env var for API key, type hints, docstrings, error handling

---

## Known Limitations & Next Steps

1. ~~**finding_recall ground truth**~~ — resolved: replaced keyword matching with LLM-based semantic matching that handles phrasing variance
2. **Prompt expansion for cache hits** — expand prompts past 1024 tokens to activate Anthropic caching
3. **evaluate_existing()** — re-score existing experiments with updated evaluators without re-running pipelines
4. **Comparative evaluation** — wire `preference_evaluator` into the main harness via `evaluate_comparative()`
5. **Human-in-the-loop** — use LangGraph's `interrupt()` before applying critical-severity fixes
6. **Production deployment** — deploy to LangGraph Cloud or self-hosted Docker for persistent, multi-tenant access
7. **V3 evaluation** — run the eval suite against v3 for a three-way comparison (v1 vs v2 vs v3)
