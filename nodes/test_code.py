import ast
import logging
from state import CodeReviewState

logger = logging.getLogger(__name__)


def test_code(state: CodeReviewState) -> dict:
    """Validate the fixed code by running basic checks."""
 
    current_code = state.get("current_code", "")
    fixed_issues = state.get("fixed_issues", [])
 
    error = None
    passed = False
 
    # check 1 — syntax validation
    try:
        ast.parse(current_code)
        syntax_valid = True
    except SyntaxError as e:
        syntax_valid = False
        error = f"Syntax error: {str(e)}"
 
    # check 2 — verify at least one issue was fixed this iteration
    # compare current count against the count recorded before this iteration started
    previous_count = state.get("previous_fixed_count", 0)
    new_fixes = len(fixed_issues) > previous_count
 
    # check 3 — no obvious regressions
    # basic check: code is not empty and is longer than a stub
    code_valid = len(current_code.strip()) > 10
 
    passed = syntax_valid and new_fixes and code_valid
    logger.info("test_code: syntax_valid=%s, new_fixes=%s, code_valid=%s, passed=%s", syntax_valid, new_fixes, code_valid, passed)

    return {
        "test_results": {
            "passed": passed,
            "syntax_valid": syntax_valid,
            "error": error
        }
    }