"""Custom evaluators for scoring v1 and v2 pipeline runs.

All evaluators follow the LangSmith signature:
    (run: Run, example: Example) -> EvaluationResult

The comparative evaluator follows:
    (runs: list[Run], example: Example) -> dict
"""

import os
import logging
from anthropic import Anthropic
from langsmith.schemas import Run, Example
from langsmith.evaluation import EvaluationResult

logger = logging.getLogger(__name__)
client = Anthropic()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


# ────────────────────────────────────────────
# 1. Finding Recall
# ────────────────────────────────────────────

def finding_recall(run: Run, example: Example) -> EvaluationResult:
    """Score how many expected findings the pipeline actually detected.

    Uses LLM-based semantic matching — asks Claude whether each expected issue
    is covered by any of the actual findings, regardless of exact wording.
    Score = matched / expected (1.0 if no expected findings).
    """
    actual_findings = (run.outputs or {}).get("findings", [])
    expected = (example.outputs or {}).get("expected_findings", [])

    if not expected:
        # clean code — score 1.0 if no false positives, penalize if findings exist
        false_positive_count = len(actual_findings)
        score = max(0.0, 1.0 - (false_positive_count * 0.2))
        return EvaluationResult(
            key="finding_recall",
            score=score,
            comment=f"Clean code: {false_positive_count} false positives",
        )

    # format actual findings for the LLM
    actual_text = "\n".join(
        f"- [{f.get('severity', '?')}] {f.get('type', '?')}: {f.get('issue', '')}"
        for f in actual_findings
    ) or "No findings produced."

    # format expected findings
    expected_text = "\n".join(
        f"- [{exp.get('severity', '?')}] {exp.get('type', '?')}: {exp['keyword']}"
        for exp in expected
    )

    prompt = f"""You are evaluating a code review pipeline's detection ability.

EXPECTED ISSUES (ground truth):
{expected_text}

ACTUAL FINDINGS (from the pipeline):
{actual_text}

For each expected issue, determine if ANY of the actual findings describe the same issue — even if worded differently. For example, "unsanitized query input" matches "sql injection", and "weak hashing algorithm" matches "md5".

Respond with ONLY a JSON object:
{{
    "matches": [
        {{"expected": "keyword", "matched": true/false, "matched_by": "brief quote from actual finding or null"}}
    ]
}}"""

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next(
            (b.text for b in response.content if hasattr(b, "text")), '{"matches": []}'
        )

        import json
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        result = json.loads(raw)
        matches = result.get("matches", [])

        matched = [m["expected"] for m in matches if m.get("matched")]
        missed = [m["expected"] for m in matches if not m.get("matched")]

    except Exception as e:
        logger.error("finding_recall LLM eval failed: %s", e)
        # fall back to keyword matching
        matched = []
        missed = []
        for exp in expected:
            keyword = exp["keyword"].lower()
            found = any(
                keyword in " ".join([
                    (f.get("issue", "") or ""),
                    (f.get("type", "") or ""),
                    (f.get("source", "") or ""),
                ]).lower()
                for f in actual_findings
            )
            if found:
                matched.append(exp["keyword"])
            else:
                missed.append(exp["keyword"])

    score = len(matched) / len(expected)
    comment = f"Matched: {matched}" if matched else "No matches"
    if missed:
        comment += f" | Missed: {missed}"

    return EvaluationResult(key="finding_recall", score=score, comment=comment)


# ────────────────────────────────────────────
# 2. Fix Correctness (LLM-graded)
# ────────────────────────────────────────────

def fix_correctness(run: Run, example: Example) -> EvaluationResult:
    """LLM-graded evaluation of whether the fixed code resolves identified issues.

    Sends the original code, findings, and fixed code to Claude for scoring.
    Returns score 0.0-1.0.
    """
    outputs = run.outputs or {}
    current_code = outputs.get("current_code")
    findings = outputs.get("findings", [])
    original_code = (example.inputs or {}).get("code_input", "")

    # no fixes needed or attempted
    if not findings or not current_code:
        return EvaluationResult(
            key="fix_correctness",
            score=1.0 if not findings else 0.0,
            comment="No findings" if not findings else "No fixes applied",
        )

    findings_text = "\n".join(
        f"- [{f.get('severity', '?')}] {f.get('issue', '')}" for f in findings
    )

    prompt = f"""Evaluate whether the fixed code correctly resolves the identified issues.

ORIGINAL CODE:
{original_code}

IDENTIFIED ISSUES:
{findings_text}

FIXED CODE:
{current_code}

Score the fix from 0 to 10:
- 10: All issues fully resolved, no new issues introduced
- 7-9: Most issues resolved, minor gaps
- 4-6: Some issues resolved, significant gaps remain
- 1-3: Few issues resolved or new issues introduced
- 0: No meaningful fixes applied

Respond with ONLY a JSON object:
{{"score": <0-10>, "reasoning": "brief explanation"}}"""

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next(
            (b.text for b in response.content if hasattr(b, "text")), '{"score": 5}'
        )

        import json
        # strip markdown fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        result = json.loads(raw)
        score = result.get("score", 5) / 10.0
        reasoning = result.get("reasoning", "")

    except Exception as e:
        logger.error("fix_correctness LLM eval failed: %s", e)
        score = 0.5
        reasoning = f"Evaluation failed: {e}"

    return EvaluationResult(
        key="fix_correctness", score=score, comment=reasoning
    )


# ────────────────────────────────────────────
# 3. Token Efficiency
# ────────────────────────────────────────────

def token_efficiency(run: Run, example: Example) -> EvaluationResult:
    """Measure cache hit ratio from token usage across findings.

    Score = cache_tokens / input_tokens (higher = better caching).
    """
    findings = (run.outputs or {}).get("findings", [])

    total_input = sum(f.get("input_tokens", 0) for f in findings)
    total_cache = sum(f.get("cache_tokens", 0) for f in findings)

    if total_input == 0:
        score = 0.0
        comment = "No token usage recorded"
    else:
        score = total_cache / total_input
        comment = f"{total_input} input, {total_cache} cache read ({score:.1%} hit rate)"

    return EvaluationResult(
        key="token_efficiency",
        score=score,
        comment=comment,
        extra={"total_input_tokens": total_input, "total_cache_tokens": total_cache},
    )


# ────────────────────────────────────────────
# 4. Agent Efficiency (v2 only)
# ────────────────────────────────────────────

def agent_efficiency(run: Run, example: Example) -> EvaluationResult:
    """Measure findings per agent spawned (v2 only).

    Returns N/A for v1 runs that don't have sub_agent_results.
    """
    outputs = run.outputs or {}
    sub_results = outputs.get("sub_agent_results", [])

    if not sub_results:
        return EvaluationResult(
            key="agent_efficiency",
            score=None,
            comment="N/A (no sub-agent results — likely v1 run)",
        )

    findings = outputs.get("findings", [])
    agent_count = len(sub_results)
    finding_count = len(findings)

    score = finding_count / agent_count if agent_count > 0 else 0.0

    return EvaluationResult(
        key="agent_efficiency",
        score=score,
        comment=f"{finding_count} findings from {agent_count} agents ({score:.1f} per agent)",
        extra={"agents_spawned": agent_count, "findings_produced": finding_count},
    )


# ────────────────────────────────────────────
# 5. Comparative Preference (LLM-judged)
# ────────────────────────────────────────────

def preference_evaluator(runs: list, example: Example) -> dict:
    """Compare two pipeline runs on the same example and pick a winner.

    Returns scores dict: {run_id: 1} for winner, {run_id: 0} for loser.
    """
    if len(runs) < 2:
        return {"key": "preference", "scores": {}}

    run_a, run_b = runs[0], runs[1]
    out_a = run_a.outputs or {}
    out_b = run_b.outputs or {}

    def summarize_run(outputs):
        findings = outputs.get("findings", [])
        fixed = outputs.get("fixed_issues") or []
        code = outputs.get("current_code", "N/A")
        iterations = outputs.get("iteration", 0)
        return (
            f"Findings: {len(findings)}, Fixed: {len(fixed)}, "
            f"Iterations: {iterations}\n"
            f"Final code:\n{code[:500] if code else 'N/A'}"
        )

    original_code = (example.inputs or {}).get("code_input", "")

    prompt = f"""Compare two code review pipeline runs on the same input.

ORIGINAL CODE:
{original_code}

--- RUN A ---
{summarize_run(out_a)}

--- RUN B ---
{summarize_run(out_b)}

Which run produced a better review and fix? Consider:
1. Did it find the real issues?
2. Did it fix them correctly?
3. Was it efficient (fewer iterations)?

Respond with ONLY a JSON object:
{{"winner": "A" or "B" or "tie", "reasoning": "brief explanation"}}"""

    try:
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next(
            (b.text for b in response.content if hasattr(b, "text")), '{"winner": "tie"}'
        )

        import json
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        result = json.loads(raw)
        winner = result.get("winner", "tie").upper()

    except Exception as e:
        logger.error("preference_evaluator failed: %s", e)
        winner = "TIE"

    scores = {}
    if winner == "A":
        scores[run_a.id] = 1
        scores[run_b.id] = 0
    elif winner == "B":
        scores[run_a.id] = 0
        scores[run_b.id] = 1
    else:
        scores[run_a.id] = 1
        scores[run_b.id] = 1

    return {"key": "preference", "scores": scores}
