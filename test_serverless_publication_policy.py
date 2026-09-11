import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import serverless_publication_policy as policy


class ServerlessPublicationPolicyTests(unittest.TestCase):
    def setUp(self):
        policy.reset_publication_policy_stats()

    def test_iso_job_within_seven_days_is_fresh(self):
        now = datetime(2026, 9, 10, 22, 0, tzinfo=timezone.utc)
        job = {"published": (now - timedelta(days=6, hours=23)).isoformat()}
        with patch.dict(os.environ, {"SERVERLESS_MAX_JOB_AGE_DAYS": "7"}, clear=False):
            self.assertTrue(policy.is_fresh_for_serverless_publication(job, now=now))

    def test_durable_source_published_at_is_supported(self):
        expected = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        parsed = policy.parse_source_datetime(
            {"source_published_at": "2026-09-10T12:00:00Z"}
        )
        self.assertEqual(parsed, expected)

    def test_durable_source_date_has_priority_over_ledger_like_fallback_fields(self):
        parsed = policy.parse_source_datetime(
            {
                "source_published_at": "2026-09-10T12:00:00Z",
                "posted_at": "2026-09-11T12:00:00Z",
            }
        )
        self.assertEqual(parsed, datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))

    def test_old_job_is_rejected_and_counted(self):
        now = datetime(2026, 9, 10, 22, 0, tzinfo=timezone.utc)
        job = {"published": "2022-02-02T19:09:30Z"}
        with patch.dict(os.environ, {"SERVERLESS_MAX_JOB_AGE_DAYS": "7"}, clear=False):
            self.assertFalse(policy.is_fresh_for_serverless_publication(job, now=now))
        self.assertEqual(policy.publication_policy_snapshot()["stale_excluded"], 1)

    def test_unknown_source_date_is_fail_closed(self):
        self.assertFalse(policy.is_fresh_for_serverless_publication({"title": "Fresh?"}))
        snapshot = policy.publication_policy_snapshot()
        self.assertEqual(snapshot["unknown_date_excluded"], 1)
        self.assertFalse(snapshot["unknown_dates_allowed"])

    def test_unreasonable_future_date_is_rejected(self):
        now = datetime(2026, 9, 10, 22, 0, tzinfo=timezone.utc)
        self.assertFalse(
            policy.is_fresh_for_serverless_publication(
                {"published": (now + timedelta(days=1)).isoformat()}, now=now
            )
        )
        self.assertEqual(policy.publication_policy_snapshot()["future_date_excluded"], 1)

    def test_epoch_seconds_and_milliseconds_are_supported(self):
        expected = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        seconds = expected.timestamp()
        self.assertEqual(policy.parse_source_datetime({"published": seconds}), expected)
        self.assertEqual(policy.parse_source_datetime({"published": int(seconds * 1000)}), expected)

    def test_rfc2822_is_supported(self):
        parsed = policy.parse_source_datetime(
            {"published": "Thu, 10 Sep 2026 12:00:00 +0000"}
        )
        self.assertEqual(parsed, datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))

    def test_vercel_policy_sets_conservative_burst_ceiling(self):
        original = policy.core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE
        try:
            with patch.dict(
                os.environ,
                {"VERCEL": "1", "SERVERLESS_MAX_POSTS_PER_CYCLE": "3"},
                clear=False,
            ):
                self.assertTrue(policy.install_serverless_publication_policy())
                self.assertEqual(policy.core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE, 3)
        finally:
            policy.core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE = original
            policy.core.is_suitable_job = policy._ORIGINAL_IS_SUITABLE_JOB

    def test_non_vercel_does_not_apply_serverless_policy(self):
        with patch.dict(os.environ, {"VERCEL": ""}, clear=False):
            self.assertFalse(policy.install_serverless_publication_policy())
            self.assertIs(policy.core.is_suitable_job, policy._ORIGINAL_IS_SUITABLE_JOB)


if __name__ == "__main__":
    unittest.main()
