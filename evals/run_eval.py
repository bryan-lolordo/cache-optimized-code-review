"""Main evaluation harness — compares v1, v2, and v3 pipelines on the same dataset.

Usage:
    python -m evals.run_eval           # v1 vs v2 (default)
    python -m evals.run_eval --v3      # v2 vs v3
    python -m evals.run_eval --all     # v1 vs v2 vs v3
"""

import sys
import os
import itertools
import logging
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

import langsmith
from langsmith.evaluation import evaluate

from graph import graph as v1_graph
from v2.graph import graph as v2_graph
from tools import ALL_TOOLS
from evals.dataset import create_or_get_dataset
from evals.evaluators import (
    finding_recall,
    fix_correctness,
    token_efficiency,
    agent_efficiency,
)


# ────────────────────────────────────────────
# Graph wrapper — turns stream-based graph into a callable target
# ────────────────────────────────────────────

def make_target(graph, label: str):
    """Wrap a LangGraph stream-based graph as a callable for evaluate()."""
    counter = itertools.count()

    def target(inputs: dict) -> dict:
        thread_id = f"eval-{label}-{next(counter)}-{uuid4().hex[:8]}"

        initial_state = {
            "code_input": inputs["code_input"],
            "violation_detected": False,
            "retry_count": 0,
            "iteration": 0,
            "previous_fixed_count": 0,
            "tool_definitions_snapshot": ALL_TOOLS,
            "current_tool_definitions": ALL_TOOLS,
        }

        # accumulate final state from stream updates
        # findings and sub_agent_results use operator.add — must extend, not overwrite
        final_state = {
            "findings": [],
            "sub_agent_results": [],
        }

        for chunk in graph.stream(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            for node_name, node_output in chunk.items():
                if not node_output:
                    continue
                for key, value in node_output.items():
                    if key in ("findings", "sub_agent_results") and isinstance(value, list):
                        final_state.setdefault(key, []).extend(value)
                    else:
                        final_state[key] = value

        return final_state

    return target


def make_v3_target():
    """Wrap the v3 Deep Agents agent as a callable for evaluate().

    V3 uses create_deep_agent() with messages-based input and custom tools
    for state tracking. The target converts eval inputs to messages, runs the
    agent, and extracts findings/code from the tool state.
    """
    from v3.agent import agent
    from v3.tools import reset_review_state, get_review_state

    counter = itertools.count()

    def target(inputs: dict) -> dict:
        thread_id = f"eval-v3-{next(counter)}-{uuid4().hex[:8]}"
        code = inputs["code_input"]

        # reset tool state for this eval run
        reset_review_state(code.strip())

        # invoke the agent with a review request
        try:
            agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Review this code for issues, fix everything you find, "
                                f"and produce a final report:\n\n```python\n{code.strip()}\n```"
                            ),
                        }
                    ]
                },
                config={"configurable": {"thread_id": thread_id}},
            )
        except Exception as e:
            logger.error("V3 agent failed: %s", e)

        # extract results from tool state
        state = get_review_state()

        # convert v3 findings to the evaluator-expected format
        findings = []
        for f in state.get("findings", []):
            findings.append({
                "type": f.get("source", "unknown"),
                "issue": f.get("issue", ""),
                "severity": f.get("severity", "medium"),
                "source": f.get("source", "unknown"),
                "input_tokens": 0,
                "cache_tokens": 0,
            })

        return {
            "findings": findings,
            "current_code": state.get("current_code", ""),
            "fixed_issues": [{"issue": desc} for desc in state.get("fixed_issues", [])],
            "iteration": len(state.get("fixed_issues", [])),
            "sub_agent_results": [],  # v3 doesn't track sub_agent_results the same way
        }

    return target


# ────────────────────────────────────────────
# Results table
# ────────────────────────────────────────────

def _extract_scores(results_list):
    """Extract scores by category from ExperimentResultRow list."""
    scores = {}
    for result in results_list:
        # ExperimentResultRow is a TypedDict: {run, example, evaluation_results}
        example = result["example"]
        category = (example.outputs or {}).get("category", "unknown")

        if category not in scores:
            scores[category] = {}

        # evaluation_results is {"results": [EvaluationResult, ...]}
        eval_results = result["evaluation_results"]
        for er in eval_results.get("results", []):
            scores[category][er.key] = er.score

    return scores


def print_comparison_table(all_results: dict[str, list]):
    """Print a comparison table for any combination of versions."""
    versions = list(all_results.keys())
    print("\n" + "=" * 70)
    print(f"  {' vs '.join(v.upper() for v in versions)} COMPARISON")
    print("=" * 70)

    all_scores = {v: _extract_scores(results) for v, results in all_results.items()}

    # collect all categories
    all_categories = sorted(set(
        cat for scores in all_scores.values() for cat in scores.keys()
    ))

    metrics = ["finding_recall", "fix_correctness", "token_efficiency", "agent_efficiency"]

    # header
    ver_headers = "".join(f"{v.upper():>8}" for v in versions)
    header = f"{'Sample':<28} {'Metric':<20} {ver_headers} {'Winner':>8}"
    print(header)
    print("-" * len(header))

    for category in all_categories:
        for metric in metrics:
            vals = {}
            for v in versions:
                vals[v] = (all_scores[v].get(category, {}) or {}).get(metric)

            val_strs = ""
            for v in versions:
                val = vals[v]
                val_strs += f"{val:.2f}" if val is not None else "  N/A"
                val_strs = f"{val_strs:>8}"

            # determine winner
            valid = {v: val for v, val in vals.items() if val is not None}
            if len(valid) >= 2:
                max_val = max(valid.values())
                winners = [v for v, val in valid.items() if val == max_val]
                winner = winners[0] if len(winners) == 1 else "tie"
            else:
                winner = "-"

            ver_vals = "".join(
                f"{vals[v]:.2f}".rjust(8) if vals[v] is not None else "     N/A"
                for v in versions
            )
            print(f"{category:<28} {metric:<20} {ver_vals} {winner:>8}")
        print()


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    run_v3 = "--v3" in args
    run_all = "--all" in args

    if run_all:
        mode = "v1 vs v2 vs v3"
    elif run_v3:
        mode = "v2 vs v3"
    else:
        mode = "v1 vs v2"

    print(f"\n=== LANGSMITH EVALUATION: {mode.upper()} ===\n")

    # 1. set up dataset
    ls_client = langsmith.Client()
    dataset_name = create_or_get_dataset(ls_client)

    evaluators = [finding_recall, fix_correctness, token_efficiency, agent_efficiency]
    all_results = {}

    # 2. run v1 evaluation (if needed)
    if not run_v3 or run_all:
        print("\n--- Running V1 evaluation ---")
        v1_target = make_target(v1_graph, "v1")
        v1_results = evaluate(
            v1_target,
            data=dataset_name,
            evaluators=evaluators,
            experiment_prefix="code-review-v1-fixed-workers",
            max_concurrency=1,
        )
        all_results["v1"] = list(v1_results)

    # 3. run v2 evaluation
    print("\n--- Running V2 evaluation ---")
    v2_target = make_target(v2_graph, "v2")
    v2_results = evaluate(
        v2_target,
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix="code-review-v2-deep-agents",
        max_concurrency=1,
    )
    all_results["v2"] = list(v2_results)

    # 4. run v3 evaluation (if requested)
    if run_v3 or run_all:
        print("\n--- Running V3 evaluation ---")
        v3_target = make_v3_target()
        v3_results = evaluate(
            v3_target,
            data=dataset_name,
            evaluators=evaluators,
            experiment_prefix="code-review-v3-deep-agents-sdk",
            max_concurrency=1,
        )
        all_results["v3"] = list(v3_results)

    # 5. print comparison
    try:
        print_comparison_table(all_results)
    except Exception as e:
        logger.warning("Could not build comparison table: %s", e)
        print("\nResults are available in LangSmith dashboard.")

    print("\nView experiments: https://smith.langchain.com")
    print("Done.")


if __name__ == "__main__":
    main()
