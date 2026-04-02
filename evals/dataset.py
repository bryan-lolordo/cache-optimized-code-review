"""Test dataset for v1 vs v2 pipeline evaluation.

Each sample has:
- inputs.code_input: the code to review
- outputs.expected_findings: ground truth with keyword-based matching
- outputs.category: for filtering/reporting
"""

import langsmith


SAMPLES = [
    # ── 1. Security-only (the classic demo code) ──
    {
        "inputs": {
            "code_input": """
def get_user(username):
    query = f"SELECT * FROM users WHERE name = '{username}'"
    return db.execute(query)

def hash_password(password):
    import hashlib
    return hashlib.md5(password.encode()).hexdigest()

API_KEY = "sk-1234567890abcdef"
"""
        },
        "outputs": {
            "expected_findings": [
                {"type": "security", "keyword": "sql injection", "severity": "critical"},
                {"type": "security", "keyword": "md5", "severity": "high"},
                {"type": "security", "keyword": "hardcoded", "severity": "critical"},
            ],
            "expected_finding_count_min": 3,
            "category": "security-only",
        },
    },

    # ── 2. Performance-only ──
    {
        "inputs": {
            "code_input": """
def find_duplicates(items):
    duplicates = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j]:
                if items[i] not in duplicates:
                    duplicates.append(items[i])
    return duplicates

def build_report(records):
    report = ""
    for record in records:
        report = report + str(record) + "\\n"
    return report

def process_matrix(matrix):
    result = []
    for i in range(len(matrix)):
        for j in range(len(matrix[0])):
            for k in range(len(matrix)):
                result.append(matrix[i][j] * matrix[k][j])
    return result
"""
        },
        "outputs": {
            "expected_findings": [
                {"type": "performance", "keyword": "nested loop", "severity": "high"},
                {"type": "performance", "keyword": "string concatenat", "severity": "medium"},
                {"type": "performance", "keyword": "complexity", "severity": "high"},
            ],
            "expected_finding_count_min": 2,
            "category": "performance-only",
        },
    },

    # ── 3. Mixed: security + quality ──
    {
        "inputs": {
            "code_input": """
import os, sys, json
from flask import Flask, request

app = Flask(__name__)
USERS = {}

@app.route("/execute", methods=["POST"])
def execute_code():
    code = request.form.get("code")
    result = eval(code)
    return str(result)

@app.route("/login", methods=["POST"])
def login():
    try:
        username = request.form["username"]
        password = request.form["password"]
        if USERS.get(username) == password:
            return "ok"
    except:
        pass
    return "fail"

def load_config(path):
    data = os.popen(f"cat {path}").read()
    return json.loads(data)
"""
        },
        "outputs": {
            "expected_findings": [
                {"type": "security", "keyword": "eval", "severity": "critical"},
                {"type": "security", "keyword": "command injection", "severity": "critical"},
                {"type": "security", "keyword": "bare except", "severity": "medium"},
                {"type": "security", "keyword": "password", "severity": "high"},
            ],
            "expected_finding_count_min": 3,
            "category": "mixed-security-quality",
        },
    },

    # ── 4. Mixed: performance + quality ──
    {
        "inputs": {
            "code_input": """
import os
import sys
import json
import re
import time
import random

def process_data(data, flag, mode, threshold, retries, verbose, debug):
    results = []
    for item in data:
        if flag:
            if mode == "fast":
                if item > threshold:
                    if verbose:
                        print(f"Processing {item}")
                    results.append(item * 2)
                else:
                    if debug:
                        print(f"Skipping {item}")
            elif mode == "slow":
                time.sleep(0.1)
                results.append(item)
        else:
            results.append(item)

    # duplicate logic
    filtered = []
    for item in data:
        if flag:
            if mode == "fast":
                if item > threshold:
                    filtered.append(item * 2)
            elif mode == "slow":
                filtered.append(item)
        else:
            filtered.append(item)

    output = ""
    for r in results:
        output = output + str(r) + ","

    return output
"""
        },
        "outputs": {
            "expected_findings": [
                {"type": "quality", "keyword": "duplicate", "severity": "medium"},
                {"type": "quality", "keyword": "nesting", "severity": "medium"},
                {"type": "performance", "keyword": "string concatenat", "severity": "medium"},
                {"type": "quality", "keyword": "parameter", "severity": "low"},
            ],
            "expected_finding_count_min": 2,
            "category": "mixed-performance-quality",
        },
    },

    # ── 5. Clean code (tests false positive rate) ──
    {
        "inputs": {
            "code_input": """
import os
import hashlib
import hmac
import secrets
from typing import Optional


def get_user(db, username: str) -> Optional[dict]:
    query = "SELECT * FROM users WHERE name = ?"
    return db.execute(query, (username,))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100000
    ).hex() + ":" + salt


def verify_password(password: str, stored: str) -> bool:
    hash_hex, salt = stored.split(":")
    new_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100000
    ).hex()
    return hmac.compare_digest(hash_hex, new_hash)


API_KEY = os.getenv("API_KEY")
"""
        },
        "outputs": {
            "expected_findings": [],
            "expected_finding_count_min": 0,
            "category": "clean-code",
        },
    },

    # ── 6. Subtle security (looks clean, isn't) ──
    {
        "inputs": {
            "code_input": """
import requests
import time


def fetch_resource(url: str) -> str:
    response = requests.get(url)
    return response.text


def check_token(provided: str, stored: str) -> bool:
    if len(provided) != len(stored):
        return False
    for a, b in zip(provided, stored):
        if a != b:
            return False
    return True


def read_file(filename: str) -> str:
    with open(f"/data/uploads/{filename}") as f:
        return f.read()
"""
        },
        "outputs": {
            "expected_findings": [
                {"type": "security", "keyword": "ssrf", "severity": "high"},
                {"type": "security", "keyword": "timing", "severity": "high"},
                {"type": "security", "keyword": "path traversal", "severity": "high"},
            ],
            "expected_finding_count_min": 2,
            "category": "subtle-security",
        },
    },
]


def create_or_get_dataset(
    client: langsmith.Client,
    dataset_name: str = "code-review-v1-vs-v2",
) -> str:
    """Create the evaluation dataset in LangSmith if it doesn't exist."""
    # check if dataset already exists
    try:
        existing = client.read_dataset(dataset_name=dataset_name)
        print(f"Dataset '{dataset_name}' already exists ({existing.id})")
        return dataset_name
    except langsmith.utils.LangSmithNotFoundError:
        pass
    except langsmith.utils.LangSmithAuthError:
        raise SystemExit(
            "LangSmith authentication failed. Check LANGCHAIN_API_KEY in your .env file.\n"
            "Get a key at https://smith.langchain.com/settings"
        )

    # create dataset
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Code review evaluation: 6 samples covering security, performance, quality, clean code, and subtle bugs.",
    )

    # upload examples
    client.create_examples(
        inputs=[s["inputs"] for s in SAMPLES],
        outputs=[s["outputs"] for s in SAMPLES],
        dataset_id=dataset.id,
    )

    print(f"Dataset '{dataset_name}' created with {len(SAMPLES)} examples")
    return dataset_name
