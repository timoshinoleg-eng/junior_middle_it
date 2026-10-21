"""No network or PostgreSQL process is needed for test-target safeguards."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, mock_open
from tools.copilot_pg_safety import validate_test_dsn
from tools import pg_concurrency_check as runner
import test_copilot_credit_concurrency as checks


class DatabaseTargetTests(unittest.TestCase):
    def test_only_exact_local_test_database_is_allowed(self):
        for host in ("localhost", "127.0.0.1", "::1"):
            validate_test_dsn("dbname=copilot_test host=" + host)
        for dsn in ("", "dbname=postgres host=localhost", "dbname=postgres_test host=localhost", "dbname=prod_copilot_test host=localhost", "dbname=copilot_test_prod host=localhost", "dbname=copilot_test host=remote.example", "dbname=copilot_test host=localhost hostaddr=192.0.2.1"):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                validate_test_dsn(dsn)

    def test_reset_refuses_before_connection_or_delete(self):
        with patch("psycopg.connect") as connect:
            with self.assertRaises(ValueError):
                checks._reset("postgresql://user@localhost/postgres")
        connect.assert_not_called()

    def test_class_setup_refuses_before_connection(self):
        with patch.dict(os.environ, {"COPILOT_TEST_PG_DSN": "postgresql://user@localhost/postgres"}), patch("psycopg.connect") as connect:
            with self.assertRaises(ValueError):
                checks.CreditConcurrencyTests.setUpClass()
        connect.assert_not_called()

    def test_local_server_is_stopped_and_removed_when_scenario_raises(self):
        directory = os.path.join(tempfile.gettempdir(), "copilot-pg-unit")
        with patch.dict(os.environ, {"COPILOT_TEST_PG_DSN": ""}), patch.object(runner.shutil, "which", side_effect=lambda name: name), patch.object(runner.tempfile, "mkdtemp", return_value=directory), patch("builtins.open", mock_open()), patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, patch.object(runner.shutil, "rmtree") as remove, patch.object(checks, "run_scenarios", side_effect=RuntimeError("scenario failed")) as scenarios:
            with self.assertRaisesRegex(RuntimeError, "scenario failed"):
                runner.main()
        self.assertTrue(scenarios.call_args.args[0].endswith("/copilot_test"))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(command[0] == "createdb" and command[-1] == "copilot_test" for command in commands))
        self.assertTrue(any(command[0] == "pg_ctl" and command[-3:] == ["-m", "fast", "stop"] for command in commands))
        remove.assert_called_once_with(os.path.realpath(directory), ignore_errors=True)

    def test_missing_createdb_fails_before_creating_directory(self):
        with patch.object(runner.shutil, "which", side_effect=lambda name: None if name == "createdb" else name), patch.object(runner.tempfile, "mkdtemp") as mkdir:
            with self.assertRaisesRegex(SystemExit, "createdb"):
                with runner._start_local_server():
                    self.fail("server must not start")
        mkdir.assert_not_called()

    def test_local_server_cleanup_on_success_and_database_creation_failure(self):
        for fail_creation in (False, True):
            with self.subTest(fail_creation=fail_creation):
                directory = os.path.join(tempfile.gettempdir(), "copilot-pg-unit")
                def run_command(command, **kwargs):
                    if fail_creation and command[0] == "createdb":
                        raise RuntimeError("database creation failed")
                    return SimpleNamespace(returncode=0)
                with patch.object(runner.shutil, "which", side_effect=lambda name: name), patch.object(runner.tempfile, "mkdtemp", return_value=directory), patch("builtins.open", mock_open()), patch.object(runner.subprocess, "run", side_effect=run_command) as run, patch.object(runner.shutil, "rmtree") as remove:
                    if fail_creation:
                        with self.assertRaisesRegex(RuntimeError, "database creation failed"):
                            with runner._start_local_server():
                                self.fail("must not yield a DSN")
                    else:
                        with runner._start_local_server() as dsn:
                            validate_test_dsn(dsn)
                self.assertEqual(run.call_args.args[0][-3:], ["-m", "fast", "stop"])
                remove.assert_called_once_with(os.path.realpath(directory), ignore_errors=True)
