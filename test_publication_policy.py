import asyncio
import unittest
from unittest.mock import patch

from channel_bot import (
    Config,
    check_application_url_status,
    classify_job_level,
    classify_url_preflight_outcome,
    diversify_jobs_by_track_and_source,
    format_job_message_legacy,
    is_duplicate_in_batch,
    preflight_application_urls,
    select_jobs_for_publication,
)


class PublicationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.original_diversify = Config.ENABLE_SOURCE_DIVERSIFY
        self.original_emergency_limit = Config.EMERGENCY_MAX_POSTS_PER_CYCLE
        self.original_unlimited = Config.UNLIMITED_POSTS_PER_CYCLE
        Config.ENABLE_SOURCE_DIVERSIFY = True
        Config.EMERGENCY_MAX_POSTS_PER_CYCLE = 0
        Config.UNLIMITED_POSTS_PER_CYCLE = False
        self.jobs = [
            {"title": "Backend A", "source": "Source A", "primary_track": "development"},
            {"title": "Data A", "source": "Source A", "primary_track": "data_ai"},
            {"title": "Backend B", "source": "Source B", "primary_track": "development"},
            {"title": "QA A", "source": "Source C", "primary_track": "qa"},
            {"title": "Data B", "source": "Source B", "primary_track": "data_ai"},
        ]

    def tearDown(self):
        Config.ENABLE_SOURCE_DIVERSIFY = self.original_diversify
        Config.EMERGENCY_MAX_POSTS_PER_CYCLE = self.original_emergency_limit
        Config.UNLIMITED_POSTS_PER_CYCLE = self.original_unlimited

    def test_zero_emergency_limit_is_clamped_to_finite_default(self):
        # v7 (B05): "0 = unlimited" is gone; a 0 ceiling falls back to the
        # finite default (20) so the selection can never be unbounded.
        Config.EMERGENCY_MAX_POSTS_PER_CYCLE = 0
        many = [
            {"title": f"Backend {i}", "source": f"Source {i % 3}", "primary_track": "development"}
            for i in range(25)
        ]
        selected = select_jobs_for_publication(many)
        self.assertEqual(len(selected), 20)

    def test_unlimited_requires_explicit_flag(self):
        Config.EMERGENCY_MAX_POSTS_PER_CYCLE = 20
        many = [
            {"title": f"Backend {i}", "source": f"Source {i % 3}", "primary_track": "development"}
            for i in range(25)
        ]
        selected = select_jobs_for_publication(many)
        self.assertEqual(len(selected), 20)
        Config.UNLIMITED_POSTS_PER_CYCLE = True
        selected_all = select_jobs_for_publication(many)
        self.assertEqual(len(selected_all), 25)

    def test_thematic_queue_interleaves_streams_before_repeating(self):
        selected = diversify_jobs_by_track_and_source(self.jobs)
        self.assertEqual(
            [job["primary_track"] for job in selected[:3]],
            ["development", "data_ai", "qa"],
        )

    def test_positive_emergency_limit_is_the_only_truncation_path(self):
        Config.EMERGENCY_MAX_POSTS_PER_CYCLE = 3
        selected = select_jobs_for_publication(self.jobs)
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            [job["primary_track"] for job in selected],
            ["development", "data_ai", "qa"],
        )

    def test_in_run_dedup_uses_normalized_url_before_publication(self):
        seen_hashes = set()
        seen_fingerprints = []
        first = {
            "title": "Junior Backend Engineer",
            "company": "Acme",
            "url": "https://example.com/jobs/123?utm_source=board",
        }
        repeat = {
            "title": "Junior Backend Engineer",
            "company": "Acme",
            "url": "https://example.com/jobs/123?ref=feed",
        }
        self.assertFalse(is_duplicate_in_batch(first, seen_hashes, seen_fingerprints))
        self.assertTrue(is_duplicate_in_batch(repeat, seen_hashes, seen_fingerprints))

    def test_legacy_card_displays_specific_geo_availability(self):
        text = format_job_message_legacy(
            {
                "title": "Middle Interview Engineer",
                "company": "Acme",
                "location": "Remote",
                "level": "Middle",
                "source": "Example",
                "url": "https://example.com/jobs/123",
                "primary_track": "development",
                "geo_restriction": "Canada",
            }
        )
        self.assertIn("Доступность:</b> Canada", text)

    def test_level_classifier_requires_explicit_junior_or_middle_evidence(self):
        self.assertIsNone(
            classify_job_level(
                {"title": "Platform Software Engineer", "description": "Remote systems role."}
            )
        )
        self.assertEqual(
            classify_job_level(
                {"title": "DevOps Engineer", "description": "Regular remote role."}
            ),
            "Middle",
        )

    def test_legacy_card_exposes_thematic_stream(self):
        text = format_job_message_legacy(
            {
                "title": "Junior Data Engineer",
                "company": "Acme",
                "source": "Example",
                "url": "https://example.com/jobs/1",
                "primary_track": "data_ai",
            }
        )
        self.assertIn("Поток:</b> Data / AI", text)

    def test_url_preflight_excludes_only_explicitly_closed_links(self):
        for status_code in (404, 410):
            with self.subTest(status_code=status_code):
                self.assertEqual(classify_url_preflight_outcome(status_code), "excluded")

        for status_code in (200, 204, 302):
            with self.subTest(status_code=status_code):
                self.assertEqual(classify_url_preflight_outcome(status_code), "passed")

        for status_code in (None, 401, 403, 429, 500, 503):
            with self.subTest(status_code=status_code):
                self.assertEqual(classify_url_preflight_outcome(status_code), "unknown")

    def test_url_preflight_invalid_link_is_unknown_without_http_request(self):
        with patch("channel_bot.requests.request") as request:
            status = check_application_url_status("https://[")
        self.assertIsNone(status)
        request.assert_not_called()

    def test_url_preflight_retries_head_incompatible_ats_with_compact_get(self):
        class FakeResponse:
            def __init__(self, status_code, headers=None):
                self.status_code = status_code
                self.headers = headers or {}

            def close(self):
                return None

        responses = [FakeResponse(405), FakeResponse(200)]
        with patch("http_guard.validate_url", return_value=True), \
             patch("channel_bot.requests.request", side_effect=responses) as request:
            status = check_application_url_status("https://example.test/apply")

        self.assertEqual(status, 200)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "HEAD")
        self.assertEqual(request.call_args_list[1].args[0], "GET")
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["Range"], "bytes=0-1023")

    def test_url_preflight_skips_host_outside_allowlist_without_request(self):
        with patch("channel_bot.requests.request") as request:
            status = check_application_url_status("https://not-on-allowlist.example/apply")
        self.assertIsNone(status)
        request.assert_not_called()

    def test_url_preflight_filters_only_closed_candidates_without_network(self):
        jobs = [
            {"url": "https://example.test/closed"},
            {"url": "https://example.test/blocked"},
            {"url": "https://example.test/live"},
        ]
        responses = {
            "https://example.test/closed": 410,
            "https://example.test/blocked": 403,
            "https://example.test/live": 200,
        }
        with patch("channel_bot.check_application_url_status", side_effect=lambda url: responses[url]):
            stats = asyncio.run(preflight_application_urls(jobs))

        self.assertEqual(stats, {"passed": 1, "excluded": 1, "unknown": 1, "disabled": 0})
        self.assertEqual(jobs[0]["url_preflight_status"], "excluded")
        self.assertEqual(jobs[1]["url_preflight_status"], "unknown")
        self.assertEqual(jobs[2]["url_preflight_status"], "passed")


if __name__ == "__main__":
    unittest.main()
