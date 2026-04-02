# LangSmith Evaluation Suite — V1 vs V2 Comparison

> Design decisions, evaluator logic, and results from the LangSmith evaluation suite that compares the v1 fixed-worker pipeline against the v2 deep agent pipeline.

---

## Motivation

Both v1 and v2 produce the same outputs — findings, fixed code, and a final report. But there was no quantitative way to compare them. "V2 feels better" isn't evidence. This eval suite creates a shared test dataset, runs both pipelines against it, scores the results with custom evaluators, and produces a side-by-side comparison — all visible in the LangSmith dashboard.

---

## Architecture

```
evals/
├── __init__.py
├── dataset.py         # 6 test samples with ground truth expected findings
├── evaluators.py      # 4 custom evaluators + 1 comparative preference evaluator
└── run_eval.py        # Main harness — wraps graphs, runs evaluate(), prints table
```

**Run with:**
```bash
python -m evals.run_eval
```

**Flow:**
1. Upload dataset to LangSmith (idempotent — skips if already exists)
2. Wrap v1 and v2 graphs as callable targets for `evaluate()`
3. Run v1 against all 6 samples, score with all evaluators
4. Run v2 against all 6 samples, score with all evaluators
5. Print comparison table to terminal
6. All results viewable in LangSmith dashboard under the `code-review-v1-vs-v2` dataset

---

## Test Dataset

Six code samples covering different vulnerability profiles. Each sample has `code_input` (the code to review) and `expected_findings` (ground truth with keyword-based matching).

| # | Category | What's in the code | Expected findings |
|---|----------|-------------------|-------------------|
| 1 | security-only | SQL injection, MD5 hashing, hardcoded API key | 3 (sql injection, md5, hardcoded) |
| 2 | performance-only | O(n²) duplicate finder, string concatenation in loop, O(n³) matrix op | 3 (nested loop, string concat, complexity) |
| 3 | mixed security+quality | `eval()` on user input, `os.popen()` command injection, bare except, plaintext passwords | 4 (eval, command injection, bare except, password) |
| 4 | mixed performance+quality | Deeply nested conditionals, duplicate logic blocks, string concat in loop, 7-parameter function | 4 (duplicate, nesting, string concat, parameter) |
| 5 | clean code | Parameterized queries, PBKDF2 hashing, `hmac.compare_digest`, `os.getenv` | 0 (tests false positive rate) |
| 6 | subtle security | SSRF via `requests.get(url)`, timing side-channel in token comparison, path traversal in `open()` | 3 (ssrf, timing, path traversal) |

**Key design decision — keyword matching:** Ground truth uses keywords (e.g., "sql injection") rather than exact strings. The evaluator checks whether the keyword appears (case-insensitive) anywhere across the finding's `issue`, `type`, and `source` fields. This is robust to LLM phrasing variance — the LLM might say "SQL injection vulnerability" or "unsanitized SQL query" and both match.

---

## Evaluators

### 1. finding_recall

**What it measures:** Did the pipeline find the expected issues?

**How it works:** For each expected finding, check whether any actual finding contains the keyword across all text fields (`issue`, `type`, `source`). Score = matched / expected. For clean code samples (no expected findings), penalizes false positives.

**Score range:** 0.0 (missed everything) to 1.0 (found all expected issues)

### 2. fix_correctness

**What it measures:** Does the fixed code actually resolve the identified issues?

**How it works:** LLM-graded. Sends the original code, identified findings, and fixed code to Claude. The LLM scores 0-10 on whether fixes are correct, complete, and don't introduce new issues. Uses the Anthropic SDK directly (consistent with the rest of the codebase).

**Score range:** 0.0 to 1.0 (LLM score / 10)

### 3. token_efficiency

**What it measures:** Cache hit ratio across all LLM calls in the pipeline.

**How it works:** Each `Finding` carries `input_tokens` and `cache_tokens` from its API call. The evaluator sums both across all findings and computes `cache_tokens / input_tokens`.

**Score range:** 0.0 (no cache hits) to 1.0 (100% cache hits)

**Note:** Currently scores 0.0 for both pipelines because prompts are below Anthropic's 1024-token minimum for cache activation. This is expected and documented in the v1 architecture decisions. Once prompts are expanded with detailed examples and reference material, this metric will differentiate.

### 4. agent_efficiency

**What it measures:** How many findings each dynamically spawned agent produces (v2 only).

**How it works:** Reads `sub_agent_results` from the v2 output. Score = findings / agents. Returns N/A for v1 runs.

**Score range:** 0.0+ (findings per agent — higher means each agent is productive)

### 5. preference_evaluator (comparative)

**What it measures:** Which pipeline produced a better overall review and fix.

**How it works:** LLM-judged comparison of two runs on the same sample. Considers finding quality, fix correctness, and efficiency. Returns a winner (A, B, or tie).

---

## Key Design Decisions

### 1. Stream accumulation for graph targets

**Problem:** `evaluate()` expects a callable `target(inputs) -> dict`, but both graphs use `graph.stream()` which emits partial updates per node. The target wrapper must collect the final state.

**Decision:** Accumulate list fields (`findings`, `sub_agent_results`) with `extend` and scalar fields with assignment. This respects the `operator.add` reducer semantics from the graph state — each node's findings are appended, not overwritten.

```python
for key, value in node_output.items():
    if key in ("findings", "sub_agent_results") and isinstance(value, list):
        final_state.setdefault(key, []).extend(value)
    else:
        final_state[key] = value
```

### 2. Sequential execution (max_concurrency=1)

**Problem:** Each eval sample triggers a full graph run with multiple Anthropic API calls. Running 6 samples in parallel could hit rate limits.

**Decision:** `max_concurrency=1` serializes execution. Slower (~15 min total) but reliable. Acceptable for a 6-sample eval suite.

### 3. Anthropic SDK for LLM-graded evals

**Problem:** LangSmith supports LangChain-based LLM evaluators, but this project uses the Anthropic SDK directly throughout.

**Decision:** `fix_correctness` and `preference_evaluator` use `anthropic.Anthropic()` directly, consistent with how every other node in the project makes API calls. No LangChain dependency for evaluation.

### 4. Unique thread IDs per evaluation run

**Problem:** Both graphs use `MemorySaver` for checkpointing. Running the same graph multiple times with the same thread_id would resume from previous state instead of starting fresh.

**Decision:** Each target invocation generates a unique thread_id: `f"eval-{label}-{counter}-{uuid_hex}"`. No state collision between eval runs.

---

## Results (First Full Run)

**Date:** 2026-04-02

### fix_correctness — V2 wins decisively

| Sample | V1 | V2 |
|--------|----|----|
| security-only | 0.50 | **1.00** |
| performance-only | 0.00 | **1.00** |
| mixed-security-quality | 0.00 | **1.00** |
| mixed-performance-quality | 0.00 | **1.00** |
| clean-code | 0.00 | **1.00** |
| subtle-security | 0.00 | **1.00** |

**Why V2 wins:** The critique/reflection loop catches when the fixer returns unchanged code and retries with feedback. V1's fixer has no quality gate — `test_code` only checks syntax, not semantic correctness.

### finding_recall — Both score low (measurement issue)

Both pipelines score near 0.0 on finding_recall. This is a ground truth mismatch, not a pipeline failure. Both pipelines find the issues, but findings from tool-call-based agents store raw code in the `issue` field rather than descriptions like "sql injection." The keyword matcher doesn't find "sql injection" in a raw SQL query string.

**Fix needed:** Either update ground truth keywords to match actual output patterns, or change how agents report findings to include descriptive text.

### token_efficiency — 0.0 for both (expected)

Prompts are ~600-750 tokens, below Anthropic's 1024-token cache activation threshold. Cache hits will appear once prompts are expanded with worked examples and reference material.

### Dynamic agent behavior (observed from traces)

V2's supervisor adapted its agent plan per sample:

| Sample | Agents spawned |
|--------|---------------|
| security-only | 1-2 (security auditor, secrets detector) |
| performance-only | 2-3 (loop optimizer, string perf analyzer) |
| mixed-security-quality | 3 (eval auditor, injection tracer, auth reviewer) |
| clean-code | 2-3 (crypto auditor, query validator, secrets scanner) |
| subtle-security | 3 (timing_attack_auditor, path_traversal_hunter, ssrf_validator) |

V1 always spawns exactly 4 workers regardless of the code.

---

## Viewing Results in LangSmith

1. Go to **https://smith.langchain.com**
2. Click **Datasets & Testing** in the left sidebar
3. Find **"code-review-v1-vs-v2"**
4. Click to see experiments:
   - `code-review-v1-fixed-workers` — v1 results
   - `code-review-v2-deep-agents` — v2 results
5. Click **Compare** for side-by-side view
6. Click into any run for full trace (every node, every LLM call, token counts)

---

## Known Limitations & Next Steps

1. **finding_recall ground truth** — Keywords need to match actual finding output format. Either update dataset keywords or modify agent finding extraction to include descriptive text.
2. **Prompt expansion for cache hits** — Expand prompts past 1024 tokens to activate Anthropic caching and produce measurable token_efficiency differences.
3. **evaluate_existing()** — Can re-score existing experiments with updated evaluators without re-running pipelines (zero Anthropic API cost).
4. **Comparative evaluation** — The `preference_evaluator` is implemented but not yet wired into the main harness. Can be added via `evaluate_comparative()` to produce head-to-head LLM judgments.
