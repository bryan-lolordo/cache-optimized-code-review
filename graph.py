from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import RetryPolicy
from langgraph.graph import StateGraph, START, END
from state import CodeReviewState
from routing import route_supervisor, route_after_test, route_after_corrector

from nodes.supervisor import supervisor
from nodes.security_reviewer import security_reviewer
from nodes.code_analyzer import code_analyzer
from nodes.performance_optimizer import performance_optimizer
from nodes.test_generator import test_generator
from nodes.fix_issues import fix_issues
from nodes.fix_issues_llm import fix_issues_llm
from nodes.test_code import test_code
from nodes.finalize import finalize
from nodes.cache_validator import cache_validator
from nodes.cache_corrector import cache_corrector
from nodes.sabotage_node import sabotage_node

workflow = StateGraph(CodeReviewState)

retry = RetryPolicy(max_attempts=3)

workflow.add_node('supervisor', supervisor)
workflow.add_node('security_reviewer', security_reviewer, retry_policy=retry)
workflow.add_node("code_analyzer", code_analyzer, retry_policy=retry)
workflow.add_node("performance_optimizer", performance_optimizer, retry_policy=retry)
workflow.add_node("test_generator", test_generator, retry_policy=retry)
workflow.add_node("fix_issues", fix_issues)
workflow.add_node("fix_issues_llm", fix_issues_llm, retry_policy=retry)
workflow.add_node("test_code", test_code)
workflow.add_node("finalize", finalize)
workflow.add_node("sabotage_node", sabotage_node)
workflow.add_node("cache_validator", cache_validator)
workflow.add_node("cache_corrector", cache_corrector)


# ── entry ──
workflow.add_edge(START, 'supervisor')

# supervisor routes conditionally — fan out or fix_issues
workflow.add_conditional_edges(
    "supervisor",
    route_supervisor,
    ["security_reviewer", "code_analyzer", "performance_optimizer", "test_generator", "fix_issues"]
)

# workers => back to supervisor
workflow.add_edge('security_reviewer', 'supervisor')
workflow.add_edge('code_analyzer', 'supervisor')
workflow.add_edge('performance_optimizer', 'supervisor')
workflow.add_edge('test_generator', 'supervisor')

# sabotage → cache enforcement (fixer loop only)
workflow.add_edge("sabotage_node", "cache_validator")
workflow.add_conditional_edges(
    "cache_validator",
    lambda state: state.get("next_node") if not state.get("violation_detected") else "cache_corrector",
    ["fix_issues_llm", "cache_corrector"]
)
workflow.add_conditional_edges(
    "cache_corrector",
    route_after_corrector,
    ["fix_issues_llm"]
)

# fixer loop
workflow.add_edge('fix_issues_llm', 'test_code')
workflow.add_conditional_edges(
    "test_code",
    route_after_test,
    ["finalize", "fix_issues"]
)
workflow.add_edge('finalize', END)

checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer)
