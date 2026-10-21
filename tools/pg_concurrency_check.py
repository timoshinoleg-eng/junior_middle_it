"""Run the concurrency check of the credit reservation on a real PostgreSQL.

The check needs a PostgreSQL server. Three ways to get one:

1. An existing server: set ``COPILOT_TEST_PG_DSN`` and run the script.
2. A local PostgreSQL: the script starts one in a temporary directory with
   ``initdb`` and ``pg_ctl``, when they are on the PATH.
3. Nothing: the script says what to do and stops.

The script never touches a production database: it refuses a DSN whose
database name is not a test database, and it only creates the tables of the
Copilot store.

    python tools/pg_concurrency_check.py
    COPILOT_TEST_PG_DSN=postgresql://user@127.0.0.1:5432/copilot_test python tools/pg_concurrency_check.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DSN_ENV = "COPILOT_TEST_PG_DSN"
UNSAFE_NAMES = ("prod", "production", "main", "junior")


def _refuse_production(dsn: str) -> None:
    name = dsn.rsplit("/", 1)[-1].split("?", 1)[0].lower()
    if any(word in name for word in UNSAFE_NAMES):
        raise SystemExit(
            "the database %r does not look like a test database: the check "
            "would write into it" % name
        )


def _start_local_server() -> str:
    """Start a PostgreSQL in a temporary directory and return its DSN."""
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        raise SystemExit(
            "no PostgreSQL on the PATH: install PostgreSQL or set %s to a test "
            "database" % DSN_ENV
        )
    directory = tempfile.mkdtemp(prefix="copilot-pg-")
    data = os.path.join(directory, "data")
    socket = os.path.join(directory, "socket")
    os.makedirs(socket, exist_ok=True)
    password = "copilot"
    subprocess.run(
        [initdb, "-D", data, "-U", "copilot", "--auth=scram-sha-256", "--pwfile=-"],
        input=password.encode("utf-8"),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            data,
            "-o",
            "-k %s -h 127.0.0.1 -p 55432" % socket,
            "-w",
            "-t",
            "60",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    return "postgresql://copilot:%s@127.0.0.1:55432/postgres" % password


def main() -> int:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        dsn = _start_local_server()
    _refuse_production(dsn)
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


if __name__ == "__main__":
    raise SystemExit(main())
