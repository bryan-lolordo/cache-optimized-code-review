# V2 — What Changed and Why

> Everything the v2 branch expands upon from v1: deep agents, LangSmith evaluation, and LangChain skills. Built on the `v2-deep-agents` branch.

---

## Overview

V1 is a fixed pipeline — four hardcoded workers, a linear fixer, and cache enforcement that proves the paper's rules work. V2 asks: what if the system could think about what it needs before it starts working?

Three expansions were made on the `v2-deep-agents` branch:

1. **Deep agent architecture** — an LLM-driven orchestrator that dynamically decides what agents to spawn, replacing the hardcoded 4-worker fan-out
2. **LangSmith evaluation suite** — a quantitative comparison framework that scores both pipelines on the same test dataset
3. **LangChain skills** — coding agent instruction files that enhance Claude Code with expert LangChain/LangGraph knowledge

---

## 1. Deep Agent Architecture

### What changed from V1

| Aspect | V1 | V2 |
|--------|----|----|
| **Workers** | 4 hardcoded nodes in `graph.py` | LLM decides at runtime — could be 1, could be 6 |
| **Prompts** | Static strings in `prompts.py` | Orchestrator generates per-agent prompts dynamically |
| **Tools** | Fixed per worker (`SECURITY_TOOLS`, `ANALYZER_TOOLS`, etc.) | Selected from a shared registry per agent |
| **Fixer** | Single pass → test → loop | Fix → critique (reflection) → retry if rejected → loop |
| **Cache enforcement** | `cache_validator` + `cache_corrector` in fixer loop | Same v1 nodes, reused directly — proves cache rules hold with dynamic topology |
| **Sabotage node** | Present in fixer loop for demo | Removed — replaced by the critique loop as the quality gate |

### Architecture

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
finalize → END
```

13 nodes total — comparable to v1's 12, but with fundamentally different behavior.

### Key decisions

**Dynamic agent planning via LLM.** The `deep_supervisor` makes an API call with a meta-prompt that analyzes the code and returns a JSON agent plan — agent names, system prompts, tool selections, focus areas, and depth level. In testing, the supervisor spawns 2 agents for the demo code (security + secrets) instead of v1's fixed 4. The agent count adapts to the task.

**Sequential agent dispatch.** V2's agents are generated at runtime — LangGraph can't fan out to nodes that don't exist at compile time. A dispatch loop (`dispatch_agent` → `execute_agent` → check queue) is simpler than `Send` with dynamic payloads and avoids v1's parallel state collision issues. For 2-4 agents the latency overhead is ~5-10 seconds.

**Tool call extraction from sub-agents.** When agents are given tools, they return `tool_use` blocks instead of text. The `execute_agent` node parses both block types — tool calls are converted to findings by extracting the tool name and input parameters. A `_extract_json` helper handles markdown fences, embedded JSON, and mixed responses.

**Critique/reflection loop.** V1's `test_code` only checks syntax with `ast.parse()`. V2 replaces it with `critique_fix` — a separate LLM call that evaluates whether the fix is semantically correct. Rejected fixes get retried with the critique feedback appended to the prompt. This is the evaluator-optimizer pattern: generate → evaluate → refine.

**No tools for the fixer.** When `fix_llm` was given tools, it would call `apply_fix` instead of returning fixed code as text. Removing tools and prompting for "return ONLY the complete updated Python code" solved it. The fixer's job is code transformation, not tool orchestration.

**Reusing v1 cache enforcement.** V2 imports `cache_validator` and `cache_corrector` directly from the v1 `nodes/` directory. The enforcement layer operates on state fields that both schemas share. This proves cache rules are topology-independent — they hold whether agents are static or dynamic.

### State schema

V2 extends v1's `CodeReviewState` with `DeepReviewState`:

```python
# new in v2
agent_plan: list[AgentSpec] | None        # supervisor's dynamic plan
plan_reasoning: str | None                 # why these agents were chosen
pending_agents: list[AgentSpec] | None     # dispatch queue
active_agent: AgentSpec | None             # currently executing agent
sub_agent_results: list[SubAgentResult]    # accumulated results (operator.add)
fix_critique: str | None                   # critique feedback
fix_approved: bool | None                  # critique verdict
```

All v1 cache enforcement and fixer fields are preserved. V1's cache validator works without modification.

### File structure

```
v2/
├── state.py              # DeepReviewState, AgentSpec, SubAgentResult
├── prompts.py            # Meta-prompts for orchestrator, fixer, critique
├── graph.py              # StateGraph wiring — 13 nodes
├── run.py                # Entry point with streaming output
└── nodes/
    ├── deep_supervisor.py   # LLM-driven orchestrator
    ├── agent_factory.py     # dispatch_agent, execute_agent, route_after_agent
    ├── deep_fixer.py        # select_issue, fix_llm, critique_fix, retry_fix, mark_fixed
    ├── synthesize.py        # Merge findings from all agents
    └── finalize.py          # Compile final report
```

V2 imports from the root: `state.py`, `tools.py`, `nodes/cache_validator.py`, `nodes/cache_corrector.py`.

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

Ground truth uses keyword matching — "sql injection" matches any finding containing that phrase across `issue`, `type`, and `source` fields. Robust to LLM phrasing variance.

### Evaluators

| Evaluator | Measures | Score |
|-----------|---------|-------|
| **finding_recall** | Did it find expected issues? Keyword match against ground truth | 0.0–1.0 |
| **fix_correctness** | Does fixed code resolve issues? LLM-graded via Anthropic SDK | 0.0–1.0 |
| **token_efficiency** | Cache hit ratio (cache_tokens / input_tokens) | 0.0–1.0 |
| **agent_efficiency** | Findings per agent spawned (v2 only, N/A for v1) | float |
| **preference_evaluator** | Which pipeline is better overall? LLM-judged comparison | winner |

### Key eval decisions

**Stream accumulation.** `evaluate()` expects `target(inputs) -> dict`, but both graphs use `graph.stream()`. The wrapper accumulates list fields (`findings`, `sub_agent_results`) with `extend` and scalar fields with assignment — respecting `operator.add` reducer semantics.

**Sequential execution.** `max_concurrency=1` avoids rate limits. Each sample triggers a full graph run with multiple API calls. Slower (~15 min) but reliable.

**Anthropic SDK for LLM-graded evals.** `fix_correctness` and `preference_evaluator` use `anthropic.Anthropic()` directly, consistent with how every node in the project makes API calls.

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

**finding_recall — both score low (measurement issue).** Both pipelines find the issues, but findings from tool-call agents store raw code in the `issue` field rather than descriptions. The keyword matcher doesn't find "sql injection" in a raw SQL query. Fix: update ground truth keywords or change agent finding extraction.

**token_efficiency — 0.0 for both (expected).** Prompts are ~600-750 tokens, below Anthropic's 1024-token cache activation threshold. Cache hits will appear once prompts are expanded.

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

### How to run

```bash
python -m evals.run_eval
```

### Viewing results in LangSmith

1. Go to **https://smith.langchain.com** → **Datasets & Testing**
2. Find **"code-review-v1-vs-v2"**
3. Click to see experiments: `code-review-v1-fixed-workers` and `code-review-v2-deep-agents`
4. Click **Compare** for side-by-side view, or click into any run for the full trace

---

## 3. LangChain Skills

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

### Most relevant to this project

- **deep-agents-core/memory/orchestration** — patterns for exactly what v2 does (dynamic agent spawning, orchestration)
- **langgraph-fundamentals** — StateGraph, reducers, Command routing
- **langgraph-human-in-the-loop** — useful for adding `interrupt()` for critical fixes
- **langgraph-persistence** — checkpointing patterns beyond MemorySaver

### Install command

```bash
npx skills add langchain-ai/langchain-skills --skill '*' --yes
```

---

## Running Both Versions

Both versions coexist in the same checkout:

```bash
# V1 — fixed pipeline, 4 hardcoded workers, sabotage node
python run.py

# V2 — deep agents, dynamic orchestrator, critique loop
python v2/run.py

# Evaluation — compare v1 vs v2 on 6 test samples
python -m evals.run_eval
```

V1's code at the project root is untouched. V2 lives in `v2/`. Evals live in `evals/`. Skills live in `.agents/skills/`. They share read-only imports from the root but don't modify each other.

---

## Known Limitations & Next Steps

1. **finding_recall ground truth** — keywords need to match actual finding output format, or agent finding extraction needs descriptive text
2. **Prompt expansion for cache hits** — expand prompts past 1024 tokens to activate Anthropic caching
3. **Parallel agent dispatch** — use LangGraph's `Send` API to fan out dynamically spawned agents in parallel instead of sequentially
4. **evaluate_existing()** — re-score existing experiments with updated evaluators without re-running pipelines
5. **Comparative evaluation** — wire `preference_evaluator` into the main harness via `evaluate_comparative()`
6. **Human-in-the-loop** — use LangGraph's `interrupt()` before applying critical-severity fixes
