# Cache-Optimized Code Review Agent

A multi-agent code review system built with [LangGraph](https://github.com/langchain-ai/langgraph) that operationalizes the strategic caching recommendations from ["Don't Break the Cache"](https://arxiv.org/abs/2601.06007) (Kevin Frank et al.) to reduce API costs and latency.

Four specialized reviewers analyze code in parallel across security, quality, performance, and test coverage. An iterative fixer loop addresses each issue one at a time. A cache enforcement layer validates and self-corrects every LLM call in the fixer loop before it fires.

## The Three Cache Rules

Derived from the paper's strategic recommendations (Section 5.1) and encoded as operational constraints:

| Rule | Constraint | Why It Matters |
|------|-----------|----------------|
| **Rule 1** | Static content leads the cached prefix | Dynamic content at the top invalidates the cache for everything below it |
| **Rule 2** | Tool results never enter the cached prefix | Tool outputs are dynamic — including them causes cache misses on every turn |
| **Rule 3** | Tool definitions don't mutate after session start | Modifying tools mid-session cascades invalidation through the entire cache hierarchy |

## Architecture

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

**Review layer:** Four workers run in parallel. Each builds its own cached prefix, performs inline cache validation, makes its API call, and returns findings. Only `findings` is written to shared state — `operator.add` merges results from all workers.

**Fixer loop:** Iterates through findings one at a time. Each fix attempt flows through the full cache enforcement pipeline — `cache_validator` checks all three rules before the API call, `cache_corrector` fixes violations automatically, and `test_code` validates the result before moving to the next issue.

**Sabotage node (demo only):** Injects cache rule violations on a rotating schedule so the validator and corrector can be observed in action via LangSmith traces. Iteration 1 violates Rule 1, iteration 2 violates Rule 2, iteration 3 violates Rule 3, iteration 4+ passes clean.

## LangGraph Patterns Used

This project leverages several core LangGraph capabilities and design patterns:

| Pattern | How It's Used |
|---------|--------------|
| **StateGraph + TypedDict** | `CodeReviewState` defines the shared state schema with typed fields (`Finding`, `TestResults`) so all nodes operate on a known data contract |
| **Reducers** | `operator.add` on `findings` for parallel accumulation; custom `take_last` on `cached_prefix`, `next_node`, `user_message`, `tool_results` for sequential overwrites in the fixer loop |
| **Parallelization (fan-out/fan-in)** | `route_supervisor` returns a list of four node names — LangGraph executes all reviewers in parallel and merges results via reducers |
| **Orchestrator-Worker** | Supervisor dispatches specialized workers, collects findings, synthesizes a report, then hands off to the fixer loop |
| **Conditional Edges** | `add_conditional_edges` on supervisor (fan-out vs. fix), cache_validator (clean vs. violation), and test_code (loop vs. escape hatch) |
| **Command Routing** | `fix_issues` uses `Command(goto=...)` to self-route — it owns the decision to continue fixing or finalize, keeping routing logic where context lives |
| **Evaluator-Optimizer Loop** | `fix_issues` → `fix_issues_llm` → `test_code` → back to `fix_issues` — generates a fix, evaluates it, loops until all issues are resolved or the escape hatch fires |
| **RetryPolicy** | All LLM nodes use `RetryPolicy(max_attempts=3)` for transient API failures |
| **Checkpointing** | `MemorySaver` enables durable execution — graph state persists at every node boundary |
| **Streaming** | `graph.stream(stream_mode="updates")` provides real-time per-node output during execution |

**Design considerations:**
- **State stores raw data, not formatted text** — nodes format prompts locally from state fields, so prompt changes don't require state schema changes
- **Nodes own their routing when they have context** — `fix_issues` and review workers use `Command`; graph-level routing is reserved for decisions that don't require node context
- **Single source of truth for each decision** — `fix_issues` alone decides when to finalize; `test_code` alone decides if a fix passed; no decision is split across nodes

## Project Structure

```
.
├── state.py                  # State schema — CodeReviewState, Finding, TestResults
├── graph.py                  # Graph wiring — nodes, edges, conditional routing
├── routing.py                # Routing functions — supervisor, test, corrector
├── prompts.py                # System prompts and background knowledge per worker
├── tools.py                  # Tool definitions per worker + ALL_TOOLS snapshot
├── run.py                    # Main entry point — runs the graph with streaming output
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
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

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

## Usage

Run the full pipeline:
```bash
python run.py
```

This streams output showing:
- Review findings from each worker (severity, type, issue summary)
- Sabotage injections and cache violations detected
- Cache corrections applied
- Before/after code diffs for each fix iteration
- Final report

Traces are available in [LangSmith](https://smith.langchain.com) under the `cache-optimized-code-review` project.

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | *required* |
| `LANGCHAIN_API_KEY` | LangSmith API key for tracing | *required* |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing | `true` |
| `LANGCHAIN_PROJECT` | LangSmith project name | `cache-optimized-code-review` |
| `LLM_MODEL` | Model for all API calls | `claude-opus-4-5` |

## Planned Improvements

- **Multi-strategy comparison:** The paper benchmarks three caching strategies — full context, system prompt only, and exclude tool results — and finds system-prompt-only consistently outperforms. A planned `--compare` mode will run all three strategies plus a no-cache baseline and report per-strategy token counts, cache hit rates, and cost savings.
- **Prompt expansion:** Anthropic requires 1024+ tokens in a cached block for caching to activate. Current prompts are ~600-750 tokens per worker. Expanding prompts with detailed worked examples and comprehensive reference material will cross this threshold and produce measurable cache hits in LangSmith traces.

## Built With

- [LangGraph](https://github.com/langchain-ai/langgraph) — stateful multi-agent orchestration
- [Anthropic API](https://docs.anthropic.com/) — LLM calls with prompt caching via `cache_control`
- [LangSmith](https://smith.langchain.com) — tracing and token usage observability

## References

- Kevin Frank et al., ["Don't Break the Cache: Optimizing Prompt Caching for Multi-Turn Agentic Systems"](https://arxiv.org/abs/2601.06007) (2025)
