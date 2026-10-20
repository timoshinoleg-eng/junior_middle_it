import unittest
from datetime import datetime, timedelta, timezone

from copilot import outcome


class StageTests(unittest.TestCase):
    def test_known_stages_are_kept(self):
        for stage in outcome.ALL_STAGES:
            self.assertEqual(outcome.normalize_stage(stage), stage)

    def test_unknown_stage_falls_back_to_sent(self):
        self.assertEqual(outcome.normalize_stage("nonsense"), outcome.STAGE_SENT)
        self.assertEqual(outcome.normalize_stage(""), outcome.STAGE_SENT)

    def test_outcome_stages_are_separate_from_sent(self):
        self.assertNotIn(outcome.STAGE_SENT, outcome.OUTCOME_STAGES)
        self.assertEqual(len(outcome.OUTCOME_STAGES), 3)

    def test_every_stage_has_a_label(self):
        for stage in outcome.ALL_STAGES:
            self.assertTrue(outcome.STAGE_LABELS.get(stage))


class DueTests(unittest.TestCase):
    def test_fresh_application_is_not_due(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(outcome.is_due(now, days=7, now=now))

    def test_old_application_is_due(self):
        now = datetime.now(timezone.utc)
        sent = now - timedelta(days=8)
        self.assertTrue(outcome.is_due(sent, days=7, now=now))

    def test_exactly_on_the_boundary_is_due(self):
        now = datetime.now(timezone.utc)
        sent = now - timedelta(days=7)
        self.assertTrue(outcome.is_due(sent, days=7, now=now))

    def test_missing_timestamp_is_never_due(self):
        self.assertFalse(outcome.is_due(None, days=7))

    def test_naive_timestamp_is_treated_as_utc(self):
        now = datetime.now(timezone.utc)
        sent = (now - timedelta(days=9)).replace(tzinfo=None)
        self.assertTrue(outcome.is_due(sent, days=7, now=now))


class CallbackTests(unittest.TestCase):
    def test_callback_carries_stage_and_hash(self):
        payload = outcome.outcome_callback(outcome.STAGE_INTERVIEW, "abc123")
        self.assertEqual(payload, "copilot_outcome:interview:abc123")

    def test_callback_is_parsed_back(self):
        stage, job_hash = outcome.stage_from_callback("copilot_outcome:rejected:abc123")
        self.assertEqual(stage, outcome.STAGE_REJECTED)
        self.assertEqual(job_hash, "abc123")

    def test_broken_callback_is_ignored(self):
        self.assertEqual(outcome.stage_from_callback("copilot_hr:abc"), ("", ""))
        self.assertEqual(outcome.stage_from_callback(""), ("", ""))


class ReminderTextTests(unittest.TestCase):
    def test_reminder_names_the_vacancy(self):
        text = outcome.reminder_text(title="Junior Python", company="Acme", days=7)
        self.assertIn("Junior Python", text)
        self.assertIn("Acme", text)
        self.assertIn("7", text)

    def test_reminder_without_company_is_fine(self):
        text = outcome.reminder_text(title="Junior Python", days=7)
        self.assertIn("Junior Python", text)


if __name__ == "__main__":
    unittest.main()
