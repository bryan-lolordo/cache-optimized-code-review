from typing import TypedDict, Annotated
import operator

def take_last(left, right):
    return right


class Finding(TypedDict):
    type: str           # security, quality, performance, testing
    issue: str
    severity: str       # critical, high, medium, low
    source: str         # node name that produced this finding
    input_tokens: int
    cache_tokens: int


class TestResults(TypedDict):
    passed: bool
    syntax_valid: bool
    error: str | None


class CodeReviewState(TypedDict):
    # input
    code_input: str

    # review layer output
    findings: Annotated[list[Finding], operator.add]

    # fixer layer
    current_code: str | None
    fixed_issues: list[Finding] | None
    iteration: int
    test_results: TestResults | None

    # output
    final_report: str | None

    # cache enforcement
    cached_prefix: Annotated[dict | None, take_last]
    tool_results: Annotated[list[dict] | None, take_last]
    tool_definitions_snapshot: list[dict] | None
    current_tool_definitions: list[dict] | None

    # control flow
    violation_detected: bool
    violation_type: str | None
    next_node: Annotated[str | None, take_last]
    user_message: Annotated[str | None, take_last]
    previous_fixed_count: int
    retry_count: int
    error: str | None

