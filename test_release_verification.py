"""Tests of the release verification tool.

The tool is an operational helper for the manual rollout of v7.0, so its
expectations must not drift away from the repository: the table list has to
match the migrations of P9-P13, and the flag list has to stay documented in
``.env.example.txt``.
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import verify_release_v7 as verify

MIGRATIONS = Path("supabase/migrations")


class ReleaseVerificationExpectationTests(unittest.TestCase):
    def test_expected_tables_match_the_copilot_migrations(self):
        created = set()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(
                r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+public\.(growth_copilot_\w+)",
                text,
                re.IGNORECASE,
            ):
                created.add(match.group(1).lower())
        self.assertTrue(created, "no Copilot tables found in the migrations")
        self.assertEqual(sorted(created), sorted(verify.EXPECTED_TABLES))

    def test_expected_flags_are_documented_in_the_env_example(self):
        example = Path(".env.example.txt").read_text(encoding="utf-8")
        for name in verify.EXPECTED_FLAGS:
            self.assertIn(name, example, name)

    def test_llm_layer_stays_off_in_the_production_expectation(self):
        self.assertEqual(verify.EXPECTED_FLAGS["COPILOT_LLM_ENABLED"], "false")
        self.assertEqual(verify.EXPECTED_FLAGS["COPILOT_LLM_SEND_RESUME"], "false")

    def test_webapp_url_points_to_the_production_mini_app(self):
        self.assertEqual(
            verify.EXPECTED_FLAGS["COPILOT_WEBAPP_URL"],
            verify.PROD_BASE + "/copilot",
        )

    def test_validator_probe_uses_the_distinct_from_comparison(self):
        self.assertIn("IS DISTINCT FROM CURRENT_DATE", verify.VALIDATOR_PROBE)
        self.assertIn("growth_copilot_credits", verify.VALIDATOR_PROBE)


class HealthCheckTests(unittest.TestCase):
    def test_healthy_payload_has_no_problems(self):
        payload = {
            "ok": True,
            "collector_ready": True,
            "durable_growth": True,
            "missing_required": [],
        }
        self.assertEqual(verify.health_problems(payload), [])

    def test_missing_env_is_reported(self):
        payload = {
            "ok": False,
            "collector_ready": True,
            "durable_growth": False,
            "missing_required": ["TELEGRAM_BOT_TOKEN"],
        }
        problems = verify.health_problems(payload)
        self.assertTrue(any("durable_growth" in item for item in problems))
        self.assertTrue(any("TELEGRAM_BOT_TOKEN" in item for item in problems))


class MiniAppPageTests(unittest.TestCase):
    def test_complete_page_has_no_problems(self):
        body = (
            "<title>Career Copilot</title>"
            '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
            '<script src="/miniapp/app.js"></script>'
        )
        self.assertEqual(verify.html_problems(body), [])

    def test_page_without_the_telegram_script_is_reported(self):
        problems = verify.html_problems("<title>Career Copilot</title>")
        self.assertTrue(any("Telegram Web App" in item for item in problems))


class FlagCheckTests(unittest.TestCase):
    def test_all_expected_flags_pass(self):
        self.assertEqual(verify.flag_problems(dict(verify.EXPECTED_FLAGS)), [])

    def test_missing_flag_is_reported(self):
        flags = dict(verify.EXPECTED_FLAGS)
        flags.pop("COPILOT_WEBAPP_ENABLED")
        problems = verify.flag_problems(flags)
        self.assertTrue(any("COPILOT_WEBAPP_ENABLED is missing" in item for item in problems))

    def test_hidden_value_is_reported(self):
        flags = dict(verify.EXPECTED_FLAGS)
        flags["COPILOT_ENABLED"] = ""
        problems = verify.flag_problems(flags)
        self.assertTrue(any("hidden value" in item for item in problems))

    def test_wrong_value_is_reported(self):
        flags = dict(verify.EXPECTED_FLAGS)
        flags["COPILOT_LLM_ENABLED"] = "true"
        problems = verify.flag_problems(flags)
        self.assertTrue(any("COPILOT_LLM_ENABLED" in item for item in problems))


class TableCheckTests(unittest.TestCase):
    def _rows(self):
        return [
            {"table_name": name, "rls_enabled": True} for name in verify.EXPECTED_TABLES
        ]

    def test_complete_set_without_grants_has_no_problems(self):
        self.assertEqual(verify.table_problems(self._rows(), []), [])

    def test_missing_table_is_reported(self):
        rows = self._rows()
        rows = [row for row in rows if row["table_name"] != "growth_copilot_rate_limits"]
        problems = verify.table_problems(rows, [])
        self.assertTrue(any("growth_copilot_rate_limits" in item for item in problems))

    def test_missing_rls_is_reported(self):
        rows = self._rows()
        rows[0]["rls_enabled"] = False
        problems = verify.table_problems(rows, [])
        self.assertTrue(any("no RLS" in item for item in problems))

    def test_privilege_for_anon_is_reported(self):
        grants = [{"table_name": "growth_copilot_credits", "grantee": "anon", "privilege_type": "SELECT"}]
        problems = verify.table_problems(self._rows(), grants)
        self.assertTrue(any("anon" in item for item in problems))


class DeploymentCheckTests(unittest.TestCase):
    def test_env_added_after_the_deployment_is_reported(self):
        problems = verify.deployment_problems(1000, {"COPILOT_ENABLED": 2000})
        self.assertTrue(any("redeploy" in item for item in problems))

    def test_env_added_before_the_deployment_is_accepted(self):
        self.assertEqual(verify.deployment_problems(2000, {"COPILOT_ENABLED": 1000}), [])

    def test_unknown_timestamps_are_accepted(self):
        self.assertEqual(verify.deployment_problems(None, {"COPILOT_ENABLED": 0}), [])


class RolloutStateTests(unittest.TestCase):
    def _complete_state(self):
        state = {name: True for name in verify.REQUIRED_ROLLOUT_FLAGS}
        state["webapp_url"] = True
        state["llm"] = False
        state["ready"] = True
        return state

    def test_finished_rollout_has_no_problems(self):
        self.assertEqual(verify.rollout_problems(self._complete_state()), [])

    def test_switched_off_feature_is_reported(self):
        state = self._complete_state()
        state["webapp"] = False
        problems = verify.rollout_problems(state)
        self.assertTrue(any("webapp" in item for item in problems))

    def test_missing_webapp_url_is_reported(self):
        state = self._complete_state()
        state["webapp_url"] = False
        problems = verify.rollout_problems(state)
        self.assertTrue(any("COPILOT_WEBAPP_URL" in item for item in problems))

    def test_llm_layer_must_stay_off(self):
        state = self._complete_state()
        state["llm"] = True
        problems = verify.rollout_problems(state)
        self.assertTrue(any("LLM" in item for item in problems))

    def test_missing_section_is_reported(self):
        problems = verify.rollout_problems(None)
        self.assertTrue(any("copilot" in item for item in problems))


class ValidatorVersionTests(unittest.TestCase):
    def test_current_version_has_no_problems(self):
        self.assertEqual(verify.validator_version_problems({"validator": 2}), [])

    def test_old_build_is_reported(self):
        problems = verify.validator_version_problems({"validator": 1})
        self.assertTrue(any("redeploy" in item for item in problems))

    def test_missing_version_is_reported(self):
        problems = verify.validator_version_problems({"ok": True})
        self.assertTrue(any("validator" in item for item in problems))

    def test_broken_response_is_reported(self):
        problems = verify.validator_version_problems("not json")
        self.assertTrue(problems)


class CopilotValidatorCheckTests(unittest.TestCase):
    def _run(self, payload):
        results = []
        verify.check_copilot_validator(results, payload)
        return results[0]

    def test_accepted_validator_passes(self):
        self.assertEqual(self._run({"copilot": {"validator_ok": True}})["status"], verify.PASS)

    def test_rejected_validator_fails(self):
        entry = self._run({"copilot": {"validator_ok": False}})
        self.assertEqual(entry["status"], verify.FAIL)
        self.assertIn("credit statement", entry["detail"])

    def test_unknown_validator_is_skipped(self):
        self.assertEqual(self._run({"copilot": {"validator_ok": None}})["status"], verify.SKIP)


class ValidatorVersionCheckTests(unittest.TestCase):
    def test_functional_probe_overrides_a_missing_marker(self):
        results = []
        verify.check_validator_versions(results, {"copilot": {"validator_ok": True}})
        self.assertEqual(len(results), 2)
        for entry in results:
            self.assertEqual(entry["status"], verify.PASS)
            self.assertIn("credit statement", entry["detail"])

    def test_marker_decides_when_the_functional_probe_is_unknown(self):
        results = []
        with patch("tools.verify_release_v7.http", return_value=(200, '{"validator": 2}')):
            verify.check_validator_versions(results, {"copilot": {"validator_ok": None}})
        for entry in results:
            self.assertEqual(entry["status"], verify.PASS)

    def test_old_build_fails_when_the_functional_probe_is_unknown(self):
        results = []
        with patch("tools.verify_release_v7.http", return_value=(200, '{"validator": 1}')):
            verify.check_validator_versions(results, {"copilot": {"validator_ok": None}})
        for entry in results:
            self.assertEqual(entry["status"], verify.FAIL)


if __name__ == "__main__":
    unittest.main()
