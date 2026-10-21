"""The statements of the Copilot store must pass the validator of the Edge bridge.

The rules of the validator live in the TypeScript of the Edge functions, so
the test runs the real functions instead of copying their rules: the validator
is extracted from the deployed source and called with Node. A copy of the
rules would pass even when the deployed validator refuses a statement, which
is exactly the defect this test has to catch.

Node is optional: without it the test skips itself and says so, so the normal
CI run stays green. The check is reproducible with any Node 22.6 or newer:

    node --version
    python -m unittest test_copilot_edge_validator -v
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from copilot import credit_operations as credit_ops
from copilot import credits as copilot_credits
from copilot.store import CopilotStore

import test_copilot_credit_operations as credit_cases


GROWTH_PROXY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "supabase",
    "functions",
    "growth-proxy",
    "index.ts",
)
INTERACTIVE_PROXY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "supabase",
    "functions",
    "interactive-growth-proxy",
    "index.ts",
)

HARNESS = """import { readFileSync } from "node:fs";

__CONSTANTS__

__VALIDATOR__

const input = JSON.parse(readFileSync(0, "utf8")) as { queries: string[] };
const results = input.queries.map((query: string) => __NAME__(query));
process.stdout.write(JSON.stringify(results));
"""


def _extract_function(source: str, name: str) -> str:
    """Return the source of one function of the Edge function."""
    lines = source.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("function %s(" % name):
            start = index
            break
    if start is None:
        raise AssertionError("the validator %s is not in the source" % name)
    for index in range(start + 1, len(lines)):
        if lines[index] == "}":
            return "\n".join(lines[start : index + 1])
    raise AssertionError("the validator %s has no end in the source" % name)


def _extract_constant(source: str, name: str) -> str:
    """Return the declaration of one constant of the Edge function."""
    match = re.search(r"^const\s+%s\s*=\s*[^;]+;" % name, source, re.MULTILINE)
    if not match:
        raise AssertionError("the constant %s is not in the source" % name)
    return match.group(0)


def _node_binary():
    """The Node binary of the machine, if it is new enough for type stripping."""
    binary = shutil.which("node")
    if not binary:
        return None, ""
    try:
        finished = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return None, ""
    match = re.search(r"v(\d+)\.(\d+)", finished.stdout or "")
    if not match:
        return None, ""
    major, minor = int(match.group(1)), int(match.group(2))
    if major < 22 or (major == 22 and minor < 6):
        return None, "node %s cannot strip the types of the validator" % finished.stdout.strip()
    return binary, ""


def _run_validator(binary, path: str, name: str, constant: str, queries) -> list:
    """Call the real validator of the Edge function with the given queries."""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    harness = (
        HARNESS.replace("__CONSTANTS__", _extract_constant(source, constant))
        .replace("__VALIDATOR__", _extract_function(source, name))
        .replace("__NAME__", name)
    )
    with tempfile.TemporaryDirectory() as directory:
        script = os.path.join(directory, "validator.mts")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(harness)
        finished = subprocess.run(
            [binary, script],
            input=json.dumps({"queries": list(queries)}),
            capture_output=True,
            text=True,
            timeout=120,
        )
    if finished.returncode != 0:
        raise AssertionError(
            "the validator %s could not run: %s" % (name, (finished.stderr or "")[:400])
        )
    return json.loads(finished.stdout)


def _statements():
    """Every statement of the Copilot store that the bridge has to accept."""
    store, backend = credit_cases.make_store(balance=3)
    operation = credit_ops.cv_operation(store, 42, credit_cases.JOB["hash"])
    operation.reserve()
    operation.release()
    copilot_credits.CreditManager(store).ensure_daily_allowance(42)
    copilot_credits.CreditManager(store).grant_referral(42)
    store.claim_document(42, credit_cases.JOB["hash"], "cv.pdf")
    store.release_document_claim(42, credit_cases.JOB["hash"], "claim:test")
    store.save_document(
        42,
        credit_cases.JOB["hash"],
        telegram_file_id="FILE-1",
        filename="cv.pdf",
        fit_score=50,
        claim="claim:test",
    )
    store.claim_ai_report(42, credit_cases.JOB["hash"])
    store.save_ai_report(
        42, credit_cases.JOB["hash"], claim="claim:test", model="m", answer="a"
    )
    store.release_ai_report_claim(42, credit_cases.JOB["hash"], "claim:test")
    store.grant_credits_with_event(42, 5, copilot_credits.REASON_REFERRAL, "")
    store.log_credit_event(42, 0, credit_ops.REASON_DELIVERED, credit_cases.JOB["hash"])
    store.credit_event_counts(42, (credit_ops.REASON_SPEND,), credit_cases.JOB["hash"])
    store.confirm_credit_operation(
        42,
        credit_cases.JOB["hash"],
        credit_ops.REASON_SPEND,
        credit_ops.REASON_DELIVERED,
    )
    store.mark_credit_operation_unknown(
        42,
        credit_cases.JOB["hash"],
        credit_ops.REASON_SPEND,
        credit_ops.REASON_UNKNOWN,
    )
    manager = copilot_credits.CreditManager(store)
    manager.reserve_llm_call(42, credit_cases.JOB["hash"])
    return backend.statements


class EdgeValidatorTests(unittest.TestCase):
    """The real validators of the Edge functions accept the new statements."""

    def setUp(self):
        self.binary, self.reason = _node_binary()
        if not self.binary:
            self.skipTest(self.reason or "node is not installed")

    def test_the_private_growth_proxy_accepts_every_statement(self):
        statements = _statements()
        self.assertTrue(statements)
        queries = [query for query, _params in statements]
        results = _run_validator(
            self.binary, GROWTH_PROXY, "validatePrivateGrowthSql", "MAX_PRIVATE_SQL_BYTES", queries
        )
        refused = [
            query
            for query, result in zip(queries, results)
            if not result
        ]
        self.assertEqual(refused, [], "the private bridge would refuse: %s" % refused[:1])

    def test_the_interactive_growth_proxy_accepts_every_statement(self):
        statements = _statements()
        self.assertTrue(statements)
        queries = [query for query, _params in statements]
        results = _run_validator(
            self.binary, INTERACTIVE_PROXY, "validateGrowthSql", "MAX_SQL_BYTES", queries
        )
        refused = [
            query
            for query, result in zip(queries, results)
            if not result
        ]
        self.assertEqual(refused, [], "the interactive bridge would refuse: %s" % refused[:1])

    def test_the_validator_really_refuses_a_forbidden_statement(self):
        """The negative control of the two tests above.

        A validator that accepts everything would make the tests above
        meaningless, so a statement outside the allowlist has to be refused.
        """
        queries = [
            "SELECT * FROM growth_copilot_credits",
            "SELECT * FROM auth.users",
            "DROP TABLE growth_copilot_credits",
            "SELECT 1; SELECT 2",
        ]
        results = _run_validator(
            self.binary,
            GROWTH_PROXY,
            "validatePrivateGrowthSql",
            "MAX_PRIVATE_SQL_BYTES",
            queries,
        )
        self.assertTrue(results[0])
        self.assertEqual(results[1:], ["", "", ""])


if __name__ == "__main__":
    unittest.main()
