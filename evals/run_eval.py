"""Main evaluation harness — compares v1 and v2 pipelines on the same dataset.

Usage:
    python -m evals.run_eval
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


def print_comparison_table(v1_results, v2_results):
    """Print a side-by-side comparison of v1 and v2 evaluation results."""
    print("\n" + "=" * 70)
    print("  V1 vs V2 COMPARISON")
    print("=" * 70)

    v1_scores = _extract_scores(v1_results)
    v2_scores = _extract_scores(v2_results)

    # print table
    header = f"{'Sample':<28} {'Metric':<20} {'V1':>8} {'V2':>8} {'Winner':>8}"
    print(header)
    print("-" * len(header))

    metrics = ["finding_recall", "fix_correctness", "token_efficiency", "agent_efficiency"]
    all_categories = sorted(set(list(v1_scores.keys()) + list(v2_scores.keys())))

    for category in all_categories:
        for metric in metrics:
            v1_val = (v1_scores.get(category, {}) or {}).get(metric)
            v2_val = (v2_scores.get(category, {}) or {}).get(metric)

            v1_str = f"{v1_val:.2f}" if v1_val is not None else "N/A"
            v2_str = f"{v2_val:.2f}" if v2_val is not None else "N/A"

            if v1_val is not None and v2_val is not None:
                if v1_val > v2_val:
                    winner = "v1"
                elif v2_val > v1_val:
                    winner = "v2"
                else:
                    winner = "tie"
            else:
                winner = "-"

            print(f"{category:<28} {metric:<20} {v1_str:>8} {v2_str:>8} {winner:>8}")
        print()


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────

def main():
    print("\n=== LANGSMITH EVALUATION: V1 vs V2 ===\n")

    # 1. set up dataset
    ls_client = langsmith.Client()
    dataset_name = create_or_get_dataset(ls_client)

    # 2. build targets
    v1_target = make_target(v1_graph, "v1")
    v2_target = make_target(v2_graph, "v2")

    evaluators = [finding_recall, fix_correctness, token_efficiency, agent_efficiency]

    # 3. run v1 evaluation
    print("\n--- Running V1 evaluation ---")
    v1_results = evaluate(
        v1_target,
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix="code-review-v1-fixed-workers",
        max_concurrency=1,
    )

    # 4. run v2 evaluation
    print("\n--- Running V2 evaluation ---")
    v2_results = evaluate(
        v2_target,
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix="code-review-v2-deep-agents",
        max_concurrency=1,
    )

    # 5. print comparison
    try:
        print_comparison_table(list(v1_results), list(v2_results))
    except Exception as e:
        logger.warning("Could not build comparison table: %s", e)
        print("\nResults are available in LangSmith dashboard.")

    print("\nView experiments: https://smith.langchain.com")
    print("Done.")


if __name__ == "__main__":
    main()
