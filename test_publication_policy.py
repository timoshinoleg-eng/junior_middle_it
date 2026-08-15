import unittest

from channel_bot import (
    Config,
    classify_job_level,
    diversify_jobs_by_track_and_source,
    format_job_message_legacy,
    select_jobs_for_publication,
)


class PublicationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.original_diversify = Config.ENABLE_SOURCE_DIVERSIFY
        self.original_emergency_limit = Config.EMERGENCY_MAX_POSTS_PER_CYCLE
        Config.ENABLE_SOURCE_DIVERSIFY = True
        Config.EMERGENCY_MAX_POSTS_PER_CYCLE = 0
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

    def test_zero_emergency_limit_keeps_every_qualified_job(self):
        selected = select_jobs_for_publication(self.jobs)
        self.assertEqual(len(selected), len(self.jobs))
        self.assertEqual({job["title"] for job in selected}, {job["title"] for job in self.jobs})

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


if __name__ == "__main__":
    unittest.main()
