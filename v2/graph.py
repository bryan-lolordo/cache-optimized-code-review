from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import RetryPolicy
from langgraph.graph import StateGraph, START, END
from v2.state import DeepReviewState

from v2.nodes.deep_supervisor import deep_supervisor, route_to_agents
from v2.nodes.agent_factory import execute_agent
from v2.nodes.synthesize import synthesize
from v2.nodes.deep_fixer import (
    select_issue,
    fix_llm,
    critique_fix,
    route_after_critique,
    retry_fix,
    mark_fixed,
)
from v2.nodes.finalize import finalize

# reuse v1 cache enforcement
from nodes.cache_validator import cache_validator
from nodes.cache_corrector import cache_corrector

retry = RetryPolicy(max_attempts=3)
workflow = StateGraph(DeepReviewState)

# ── Phase 1: Dynamic agent planning ──
workflow.add_node("deep_supervisor", deep_supervisor, retry_policy=retry)

# ── Phase 2: Parallel agent execution (via Send fan-out) ──
workflow.add_node("execute_agent", execute_agent, retry_policy=retry)

# ── Phase 3: Synthesize findings ──
workflow.add_node("synthesize", synthesize)

# ── Phase 4: Deep fixer with reflection ──
workflow.add_node("select_issue", select_issue)
workflow.add_node("fix_llm", fix_llm, retry_policy=retry)
workflow.add_node("critique_fix", critique_fix, retry_policy=retry)
workflow.add_node("retry_fix", retry_fix)
workflow.add_node("mark_fixed", mark_fixed)

# ── Phase 5: Cache enforcement (reused from v1) ──
workflow.add_node("cache_validator", cache_validator)
workflow.add_node("cache_corrector", cache_corrector)

# ── Phase 6: Finalize ──
workflow.add_node("finalize", finalize)


# ════════════════════════════════════════════
# EDGES
# ════════════════════════════════════════════

# Entry → deep supervisor analyzes code and builds agent plan
workflow.add_edge(START, "deep_supervisor")

# Supervisor → routing function returns Send objects for parallel fan-out
workflow.add_conditional_edges(
    "deep_supervisor",
    route_to_agents,
    ["execute_agent"],
)

# All parallel agents fan back into synthesize
workflow.add_edge("execute_agent", "synthesize")

# Synthesize → start fixing
workflow.add_edge("synthesize", "select_issue")

# select_issue uses Command() to route to fix_llm or finalize directly

# fix_llm → cache validator (enforce cache rules before critique)
workflow.add_edge("fix_llm", "cache_validator")

# Cache validator → clean pass goes to critique, violation goes to corrector
workflow.add_conditional_edges(
    "cache_validator",
    lambda state: "cache_corrector" if state.get("violation_detected") else "critique_fix",
    ["critique_fix", "cache_corrector"],
)

# Cache corrector → back to critique
workflow.add_edge("cache_corrector", "critique_fix")

# Critique → approve or retry
workflow.add_conditional_edges(
    "critique_fix",
    route_after_critique,
    ["retry_fix", "mark_fixed"],
)

# Retry → back to fix_llm with updated prompt
workflow.add_edge("retry_fix", "fix_llm")

# Mark fixed → back to select_issue (picks next or finalizes)
workflow.add_edge("mark_fixed", "select_issue")

# Finalize → END
workflow.add_edge("finalize", END)


checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer)
