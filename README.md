# 🔍 Cache-Optimized Code Review Agent

A multi-agent code review system built with [LangGraph](https://github.com/langchain-ai/langgraph) that operationalizes the strategic caching recommendations from ["Don't Break the Cache"](https://arxiv.org/abs/2601.06007) (Kevin Frank et al.) to reduce API costs and latency.

> 📄 **[From First Agent to Production Systems](docs/customer_facing_technical_pitch.md)** — A customer-facing technical pitch that walks through the prototype-to-production journey with LangChain, covering architecture, state management, observability, reliability, evaluation, and infrastructure — all grounded in real results from this project.

---

Three versions coexist in this repo, each demonstrating a different level of abstraction:

- **V1 (root):** Four hardcoded workers analyze code in parallel across security, quality, performance, and test coverage. An iterative fixer loop addresses each issue one at a time. A cache enforcement layer validates and self-corrects every LLM call in the fixer loop.
- **V2 (`v2/`):** An LLM-driven orchestrator dynamically decides what agents to spawn based on the code. Agents execute in parallel via the `Send` API, report findings through a structured `report_finding` tool, and a critique/reflection loop validates each fix before moving on.
- **V3 (`v3/`):** Same review capabilities rebuilt with the **Deep Agents SDK**. Replaces v2's manual 12-node `StateGraph` with a single `create_deep_agent()` call. The SDK provides the agent loop, tool calling, task planning (`write_todos`), subagent delegation (`task`), and state management automatically — cutting the codebase from ~500 lines to ~200.

## 📐 The Three Cache Rules

Derived from the paper's strategic recommendations (Section 5.1) and encoded as operational constraints:

| Rule | Constraint | Why It Matters |
|------|-----------|----------------|
| **Rule 1** | Static content leads the cached prefix | Dynamic content at the top invalidates the cache for everything below it |
| **Rule 2** | Tool results never enter the cached prefix | Tool outputs are dynamic — including them causes cache misses on every turn |
| **Rule 3** | Tool definitions don't mutate after session start | Modifying tools mid-session cascades invalidation through the entire cache hierarchy |

## 🏗️ Architecture

<details>
<summary><strong>V1 — Fixed Pipeline</strong></summary>

```
START
  |
supervisor (first pass -- fan out)
  | | | | (parallel)
security_reviewer  code_analyzer  performance_optimizer  test_generator
  (each: prep -> inline validate -> API call -> return findings)
  | | | | (findings accumulate via operator.add)
supervisor (second pass -- synthesize)
  |
fix_issues -----------------------------------------+
  | (issues remain)                                 |
sabotage_node (demo only -- injects violations)     |
  |                                                 |
cache_validator                                     |
  | (clean) or -> cache_corrector -> (next_node)    |
fix_issues_llm                                      |
  |                                                 |
test_code                                           |
  | (always)                                        |
fix_issues <----------------------------------------+
  | (no issues remain)
finalize
  |
END
```

</details>

<details>
<summary><strong>V2 — Deep Agents (Manual Graph)</strong></summary>

```
START
  |
deep_supervisor (LLM analyzes code, generates agent plan as JSON)
  |
route_to_agents (returns Send objects for parallel fan-out)
  | | | (parallel via Send API)
execute_agent  execute_agent  execute_agent
  (each with dynamic prompt/tools/focus + report_finding tool)
  | | | (findings merge via operator.add)
synthesize (merge findings, build report)
  |
select_issue <-------------------------------------+
  |                                                |
fix_llm (apply fix -- no tools, text only)         |
  |                                                |
cache_validator -> cache_corrector?                |
  |                                                |
critique_fix (reflection -- is fix correct?)       |
  |-- rejected -> retry_fix -> fix_llm             |
  |                                                |
mark_fixed ----------------------------------------+
  | (no issues left)
finalize -> END
```

</details>

<details>
<summary><strong>V3 — Deep Agents SDK</strong></summary>

```
create_deep_agent() orchestrator
  |
  |-- write_todos (plan review phases)
  |
  |-- task(security_reviewer)  ─┐
  |-- task(performance_reviewer) │ subagent delegation
  |-- task(quality_reviewer)     │ (SDK handles lifecycle)
  |-- task(test_reviewer)       ─┘
  |
  |-- report_finding (record each issue)
  |
  |-- apply_fix (fix each issue iteratively)
  |-- task(fix_critic) (validate each fix)
  |-- apply_fix (retry if rejected, max 2)
  |
  |-- get_review_summary (compile final report)
```

The entire flow is driven by a single agent with the SDK's built-in middleware
(TodoList, SubAgent, Filesystem) — no manual graph wiring needed.

</details>

**Key differences V1 → V2:**
- Workers are dynamically planned by the LLM — could be 1, could be 6
- `Send` API dispatches agents in parallel with isolated payloads
- Agents report findings via a typed `report_finding` tool (no JSON parsing)
- Critique/reflection loop replaces syntax-only `test_code` validation
- Cache enforcement from v1 is reused directly — proves cache rules are topology-independent

**Key differences V2 → V3:**
- Manual `StateGraph` with 12 nodes → single `create_deep_agent()` call
- Raw Anthropic API calls → SDK manages model calls via LangChain
- Manual `Send` fan-out → `task` tool for subagent delegation
- Custom `DeepReviewState` TypedDict → SDK built-in state + custom tools
- Manual cache enforcement → SDK handles prompt construction internally
- ~500 lines across 9 files → ~200 lines across 4 files

## 🧩 LangGraph Patterns Used

| Pattern | How It's Used |
|---------|--------------|
| **StateGraph + TypedDict** | `CodeReviewState` (v1) and `DeepReviewState` (v2) define shared state schemas with typed fields |
| **Reducers** | `operator.add` on `findings` for parallel accumulation; custom `take_last` for sequential overwrites |
| **Parallelization (fan-out/fan-in)** | V1: list return from routing function. V2: `Send` objects with isolated payloads |
| **Orchestrator-Worker** | V1: static dispatch. V2: LLM-driven dynamic agent planning |
| **Conditional Edges** | Routing on supervisor, cache_validator, test_code (v1), critique_fix (v2) |
| **Command Routing** | `fix_issues` (v1) and `select_issue` (v2) use `Command(goto=...)` for self-routing |
| **Evaluator-Optimizer Loop** | V1: fix → test → loop. V2: fix → critique → retry → loop |
| **Send API** | V2 dispatches dynamic agents in parallel — each gets isolated state, findings merge via reducers |
| **RetryPolicy** | All LLM nodes use `RetryPolicy(max_attempts=3)` for transient API failures |
| **Checkpointing** | `MemorySaver` for local execution; platform-managed for deployment |
| **Streaming** | `graph.stream(stream_mode="updates")` for real-time per-node output |
| **Deep Agents SDK** | V3: `create_deep_agent()` replaces manual graph wiring with middleware-driven orchestration |
| **SubAgentMiddleware** | V3: `task` tool delegates to specialist subagents (security, performance, quality, test, critic) |
| **TodoListMiddleware** | V3: `write_todos` for planning and progress tracking across review phases |

<details>
<summary><strong>📁 Project Structure</strong></summary>

```
.
├── state.py                  # V1 state schema — CodeReviewState, Finding, TestResults
├── graph.py                  # V1 graph wiring — nodes, edges, conditional routing
├── routing.py                # V1 routing functions — supervisor, test, corrector
├── prompts.py                # V1 system prompts and background knowledge per worker
├── tools.py                  # Tool definitions per worker + report_finding + ALL_TOOLS
├── run.py                    # V1 entry point — runs the graph with streaming output
├── baseline.py               # Baseline comparison — same pipeline without caching
├── nodes/
│   ├── supervisor.py         # Orchestrator — fan out on first pass, synthesize on second
│   ├── security_reviewer.py  # Security vulnerability review
│   ├── code_analyzer.py      # Code quality and best practices review
│   ├── performance_optimizer.py  # Performance bottleneck review
│   ├── test_generator.py     # Test coverage gap review
│   ├── fix_issues.py         # Issue selector — picks next unfixed issue, builds fix prompt
│   ├── fix_issues_llm.py     # Fix LLM call — applies the fix via API
│   ├── test_code.py          # Fix validator — syntax check, fix verification
│   ├── finalize.py           # Report compiler — findings, fixes, final code
│   ├── cache_validator.py    # Validates cached prefix against all three rules
│   ├── cache_corrector.py    # Self-corrects detected violations
│   └── sabotage_node.py      # Demo node — injects violations for testing
├── v2/
│   ├── state.py              # DeepReviewState, AgentSpec, SubAgentResult
│   ├── prompts.py            # Meta-prompts for orchestrator, fixer, critique
│   ├── graph.py              # V2 graph — 12 nodes, Send fan-out, critique loop
│   ├── run.py                # V2 entry point with streaming output
│   └── nodes/
│       ├── deep_supervisor.py   # LLM-driven orchestrator + route_to_agents (Send)
│       ├── agent_factory.py     # execute_agent with report_finding extraction
│       ├── deep_fixer.py        # select_issue, fix_llm, critique_fix, retry_fix, mark_fixed
│       ├── synthesize.py        # Merge findings from all agents
│       └── finalize.py          # Compile final report
├── v3/
│   ├── agent.py              # create_deep_agent orchestrator + subagent config
│   ├── prompts.py            # System prompts for orchestrator and all subagents
│   ├── tools.py              # Custom tools — report_finding, apply_fix, get_review_summary
│   └── run.py                # V3 entry point with streaming output
├── evals/
│   ├── dataset.py            # 6 test samples with ground truth expected findings
│   ├── evaluators.py         # Custom evaluators — recall, correctness, efficiency
│   └── run_eval.py           # Evaluation harness — runs v1 vs v2 comparison
├── docs/
│   ├── learning_journey.md   # V1 design decisions and learning notes
│   ├── v2_expansion.md       # V2 architecture, eval results, platform setup
│   └── customer_facing_technical_pitch.md  # Technical pitch — prototype to production
├── .agents/skills/           # LangChain skills for coding agent context (11 skills)
├── langgraph.json            # LangGraph Platform config — serves both v1 and v2
├── requirements.txt
├── .env.example
└── .gitignore
```

</details>

## ⚙️ Setup

**Prerequisites:** Python 3.11+

1. Clone the repository:
   ```bash
   git clone https://github.com/bryan-lolordo/cache-optimized-code-review.git
   cd cache-optimized-code-review
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up environment variables:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your API keys:
   ```
   ANTHROPIC_API_KEY=your-api-key-here
   LANGCHAIN_API_KEY=your-langchain-api-key-here
   ```

## 🚀 Usage

```bash
# V1 — fixed pipeline, 4 hardcoded workers, cache enforcement with sabotage demo
python run.py

# V2 — deep agents, parallel Send, critique/reflection loop
python v2/run.py

# V3 — Deep Agents SDK, single create_deep_agent() orchestrator
python v3/run.py

# Evaluation — compare v1 vs v2 on 6 test samples in LangSmith
python -m evals.run_eval

# LangGraph Platform — serve both graphs as API endpoints with Studio UI
python -m langgraph_cli dev
```

Traces for all runs are available in [LangSmith](https://smith.langchain.com) under the `cache-optimized-code-review` project.

## 🌐 LangGraph Platform

All three graphs are served simultaneously via `langgraph.json`:

```json
{
  "graphs": {
    "v1_agent": "graph:graph",
    "v2_agent": "v2.graph:graph",
    "v3_agent": "v3.agent:agent_platform"
  }
}
```

Start the local dev server (requires Docker):
```bash
python -m langgraph_cli dev
```

This starts the API at `http://127.0.0.1:2024` and connects to [LangGraph Studio](https://smith.langchain.com/studio) for visual graph execution and state inspection.

## 📋 Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | *required* |
| `LANGCHAIN_API_KEY` | LangSmith API key for tracing | *required* |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing | `true` |
| `LANGCHAIN_PROJECT` | LangSmith project name | `cache-optimized-code-review` |
| `LLM_MODEL` | Model for all API calls | `claude-sonnet-4-20250514` |

## 🛠️ Built With

- [LangGraph](https://github.com/langchain-ai/langgraph) — stateful multi-agent orchestration
- [Deep Agents SDK](https://github.com/langchain-ai/deepagents) — batteries-included agent framework (v3)
- [Anthropic API](https://docs.anthropic.com/) — LLM calls with prompt caching via `cache_control`
- [LangSmith](https://smith.langchain.com) — tracing, evaluation, and token usage observability

## 📚 References

- Kevin Frank et al., ["Don't Break the Cache: Optimizing Prompt Caching for Multi-Turn Agentic Systems"](https://arxiv.org/abs/2601.06007) (2025)
