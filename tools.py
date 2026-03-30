SECURITY_TOOLS = [
    {
        "name": "scan_security",
        "description": "Run Bandit security scanner on the provided code",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to scan"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "detect_sql_injection",
        "description": "Check code for SQL injection vulnerability patterns",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to check"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "detect_secrets",
        "description": "Find hardcoded secrets, API keys, and passwords in code",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to scan for secrets"}
            },
            "required": ["code"]
        }
    }
]

ANALYZER_TOOLS = [
    {
        "name": "run_static_analysis",
        "description": "Run static analysis on code for quality, structure, and PEP 8 compliance",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to analyze"},
                "language": {"type": "string", "description": "Programming language of the code"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "check_code_smells",
        "description": "Identify code smells, anti-patterns, and structural issues",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to inspect"}
            },
            "required": ["code"]
        }
    }
]

PERFORMANCE_TOOLS = [
    {
        "name": "measure_complexity",
        "description": "Measure cyclomatic complexity and identify performance bottlenecks",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to profile"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "detect_nested_loops",
        "description": "Detect nested loops and O(n^2) or worse complexity patterns",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to check"}
            },
            "required": ["code"]
        }
    }
]

TEST_GENERATOR_TOOLS = [
    {
        "name": "analyze_test_coverage",
        "description": "Analyze existing test coverage and identify untested code paths",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to analyze"},
                "existing_tests": {"type": "string", "description": "Any existing test code"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "suggest_test_cases",
        "description": "Suggest unit test cases for the provided code",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to generate tests for"},
                "framework": {"type": "string", "description": "Test framework to use e.g. pytest"}
            },
            "required": ["code"]
        }
    }
]

FIX_TOOLS = [
    {
        "name": "apply_fix",
        "description": "Apply a suggested fix to the code and return the updated version",
        "input_schema": {
            "type": "object",
            "properties": {
                "original_code": {"type": "string", "description": "The original code to fix"},
                "fix_description": {"type": "string", "description": "Description of the fix to apply"}
            },
            "required": ["original_code", "fix_description"]
        }
    },
    {
        "name": "run_tests",
        "description": "Execute test cases against the provided code and return results",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to test"},
                "test_cases": {"type": "array", "description": "List of test cases to run", "items": {"type": "string"}}
            },
            "required": ["code"]
        }
    }
]

# all tools combined — used for tool_definitions_snapshot at session start
ALL_TOOLS = SECURITY_TOOLS + ANALYZER_TOOLS + PERFORMANCE_TOOLS + TEST_GENERATOR_TOOLS + FIX_TOOLS