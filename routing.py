from state import CodeReviewState


def route_supervisor(state: CodeReviewState):
    """Orchestrate the code review by dispatching to all reviewers in parallel."""
    if not state.get("findings"):
        return ["security_reviewer", "code_analyzer", "performance_optimizer", "test_generator"]
    return "fix_issues"

def route_after_corrector(state: CodeReviewState):
    """Route to the intended LLM node after cache correction."""
    return state.get("next_node")

def route_after_test(state: CodeReviewState):
    """Always return to fix_issues — it decides when to finalize. Escape hatch at max iterations."""
    if state.get("iteration", 0) >= 10:
        return "finalize"
    return "fix_issues"
