"""Run the concurrency check of the credit reservation on a real PostgreSQL.

The check needs a PostgreSQL server. Three ways to get one:

1. An existing server: set ``COPILOT_TEST_PG_DSN`` and run the script.
2. A local PostgreSQL: the script starts one in a temporary directory with
   ``initdb`` and ``pg_ctl``, when they are on the PATH.
3. Nothing: the script says what to do and stops.

Only database copilot_test on localhost, 127.0.0.1 or ::1 is allowed.
The local server creates that dedicated database and is stopped and removed
on success or failure. Every supplied DSN is checked before connecting.

    python tools/pg_concurrency_check.py
    COPILOT_TEST_PG_DSN=postgresql://user@127.0.0.1:5432/copilot_test python tools/pg_concurrency_check.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager, ExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DSN_ENV = "COPILOT_TEST_PG_DSN"
from tools.copilot_pg_safety import validate_test_dsn


@contextmanager
def _start_local_server():
    """Yield a local copilot_test DSN and always clean up this server."""
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    createdb = shutil.which("createdb")
    if not initdb or not pg_ctl or not createdb:
        raise SystemExit("initdb, pg_ctl and createdb must be on PATH; otherwise set COPILOT_TEST_PG_DSN to local copilot_test")
    directory = os.path.realpath(tempfile.mkdtemp(prefix="copilot-pg-"))
    if os.path.dirname(directory) != os.path.realpath(tempfile.gettempdir()) or not os.path.basename(directory).startswith("copilot-pg-"):
        raise RuntimeError("Temporary PostgreSQL directory is outside the expected temp root")
    data = os.path.join(directory, "data")
    password_file = os.path.join(directory, "password")
    start_attempted = False
    try:
        with open(password_file, "w", encoding="utf-8") as stream:
            stream.write("copilot\n")
        subprocess.run([initdb, "-D", data, "-U", "copilot", "--auth=scram-sha-256", "--pwfile=" + password_file],
                       check=True, capture_output=True)
        start_attempted = True
        subprocess.run([pg_ctl, "-D", data, "-o", "-h 127.0.0.1 -p 55432", "-w", "-t", "60", "start"],
                       check=True, capture_output=True)
        subprocess.run([createdb, "-h", "127.0.0.1", "-p", "55432", "-U", "copilot", "copilot_test"],
                       env={**os.environ, "PGPASSWORD": "copilot"}, check=True, capture_output=True)
        yield "postgresql://copilot:copilot@127.0.0.1:55432/copilot_test"
    finally:
        try:
            if start_attempted:
                result = subprocess.run([pg_ctl, "-D", data, "-m", "fast", "stop"], check=False, capture_output=True)
                if result.returncode:
                    print("WARNING: temporary PostgreSQL stop failed", file=sys.stderr)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


def main() -> int:
    stack = ExitStack()
    previous_dsn = os.environ.get(DSN_ENV)
    try:
        dsn = os.environ.get(DSN_ENV)
        if not dsn:
            dsn = stack.enter_context(_start_local_server())
        validate_test_dsn(dsn)
        os.environ[DSN_ENV] = dsn
        import test_copilot_credit_concurrency as check

        scenarios = check.run_scenarios(dsn)
        for name, result in scenarios.items():
            print("== %s" % name)
            for key, value in result.items():
                print("   %-18s %s" % (key, value))
        failures = 0
        import unittest

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(check.CreditConcurrencyTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        failures += len(result.failures) + len(result.errors)
        return 1 if failures else 0
    finally:
        try:
            stack.close()
        finally:
            if previous_dsn is None:
                os.environ.pop(DSN_ENV, None)
            else:
                os.environ[DSN_ENV] = previous_dsn


if __name__ == "__main__":
    raise SystemExit(main())
