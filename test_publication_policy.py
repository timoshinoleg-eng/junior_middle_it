import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import channel_bot
from channel_bot import (
    Config,
    _bump_source_funnel,
    _new_source_funnel,
    _source_funnel_stage_total,
    check_application_url_status,
    classify_job_level,
    classify_url_preflight_outcome,
    collect_and_post_once,
    diversify_jobs_by_track_and_source,
    format_job_message_legacy,
    is_duplicate_in_batch,
    preflight_application_urls,
    probe_candidates_for_publication,
    select_jobs_for_publication,
)


_VACANCY_TITLES = (
    "Junior Backend Engineer",
    "Junior Frontend Engineer",
    "Junior DevOps Engineer",
    "Junior Data Analyst",
    "Junior QA Automation Engineer",
)


def _vacancy(index: int) -> dict:
    return {
        "title": _VACANCY_TITLES[index - 1],
        "company": f"Acme {index}",
        "url": f"https://example.com/jobs/{index}",
        "description": "Remote role. Ship and maintain production services.",
        "location": "Remote",
        "source": "Example",
        "published": "2026-09-25T10:00:00Z",
    }


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

    def test_structured_level_metadata_is_authoritative_evidence(self):
        self.assertEqual(
            classify_job_level(
                {
                    "title": "Software Engineer",
                    "description": "Remote product role.",
                    "level": "Mid Level",
                }
            ),
            "Middle",
        )
        self.assertEqual(
            classify_job_level(
                {
                    "title": "Software Engineer",
                    "description": "Remote product role.",
                    "tags": ["New Grad"],
                }
            ),
            "Junior",
        )
        self.assertIsNone(
            classify_job_level(
                {
                    "title": "Software Engineer",
                    "description": "Remote product role.",
                    "seniority": {"name": "Principal"},
                }
            )
        )

    def test_entry_level_wording_is_recognised_without_guessing(self):
        for title in ("New Grad Backend Engineer", "Software Engineering Intern"):
            with self.subTest(title=title):
                self.assertEqual(
                    classify_job_level({"title": title, "description": "Remote role."}),
                    "Junior",
                )
        self.assertEqual(
            classify_job_level({"title": "Engineer II", "description": "Remote role."}),
            "Middle",
        )
        self.assertEqual(
            classify_job_level({"title": "Engineer I", "description": "Remote role."}),
            "Junior",
        )

    def test_default_pre_selection_hook_keeps_every_candidate(self):
        jobs = [_vacancy(1), _vacancy(2)]
        self.assertEqual(asyncio.run(probe_candidates_for_publication(jobs)), jobs)

    def test_source_funnel_totals_a_stage_across_sources(self):
        funnel = _new_source_funnel([{"source": "Greenhouse", "fetched": 2}])
        _bump_source_funnel(funnel, {"source": "Greenhouse:acme"}, "pre_selection_duplicate")
        _bump_source_funnel(funnel, {"source": "RSS:WWR"}, "pre_selection_duplicate")
        _bump_source_funnel(funnel, {"source": "RSS:WWR"}, "posted")
        self.assertEqual(_source_funnel_stage_total(funnel, "pre_selection_duplicate"), 2)
        self.assertEqual(_source_funnel_stage_total(funnel, "posted"), 1)
        self.assertEqual(_source_funnel_stage_total(funnel, "classified"), 0)

    def test_pre_selection_dedup_frees_publication_slots_for_new_vacancies(self):
        jobs = [_vacancy(index) for index in range(1, 6)]
        probed = {}

        async def probe(candidates):
            probed["candidates"] = list(candidates)
            # The ledger already published the three best ranked vacancies.
            return candidates[3:]

        posted = []

        async def post(_bot, job, db=None):
            posted.append(job["title"])
            return True

        cb_delays = dict(channel_bot.DELAYS)

        async def scenario():
            with patch.object(Config, "validate", staticmethod(lambda: True)), patch.object(
                Config, "TELEGRAM_BOT_TOKEN", "123456:fake-token"
            ), patch("channel_bot.Bot") as bot_cls, patch(
                "channel_bot.configure_webshare_proxy", return_value=None
            ), patch(
                "channel_bot.get_api_fetch_functions", return_value=[(lambda: [], "Example")]
            ), patch(
                "channel_bot.safe_fetch_with_retry", return_value=jobs
            ), patch(
                "channel_bot.get_recent_channel_job_hashes", AsyncMock(return_value=set())
            ), patch(
                "channel_bot.preflight_application_urls",
                AsyncMock(return_value={"passed": 5, "excluded": 0, "unknown": 0, "disabled": 0}),
            ), patch(
                "channel_bot.auto_classify_category", return_value="other"
            ), patch(
                "channel_bot.apply_editorial_quality_gate",
                side_effect=lambda job, **kwargs: job.update({"quality_gate_status": "passed"}),
            ), patch(
                "channel_bot.passes_min_salary", return_value=True
            ), patch(
                "channel_bot.passes_channel_tracks", return_value=True
            ), patch(
                "channel_bot.probe_candidates_for_publication", side_effect=probe
            ), patch(
                "channel_bot.post_job_with_bot", side_effect=post
            ), patch(
                "channel_bot.DELAYS", {key: 0 for key in cb_delays}
            ):
                bot_cls.return_value.get_me = AsyncMock(return_value=None)
                return await collect_and_post_once(use_sqlite=False)

        with patch.object(Config, "EMERGENCY_MAX_POSTS_PER_CYCLE", 2):
            result = asyncio.run(scenario())

        self.assertEqual(len(probed["candidates"]), 5)
        self.assertEqual(result["pre_selection_duplicates"], 3)
        self.assertEqual(result["duplicates"], 3)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["posted"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(posted), 2)
        self.assertEqual(result["classified"], 5)
        self.assertEqual(result["suitable_after_freshness"], 5)
        self.assertEqual(
            _source_funnel_stage_total(result["source_funnel"], "pre_selection_duplicate"),
            3,
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
        with patch("channel_bot.requests.head") as head:
            status = check_application_url_status("https://[")
        self.assertIsNone(status)
        head.assert_not_called()

    def test_url_preflight_retries_head_incompatible_ats_with_compact_get(self):
        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code

            def close(self):
                return None

        with patch("channel_bot.requests.head", return_value=FakeResponse(405)) as head:
            with patch("channel_bot.requests.get", return_value=FakeResponse(200)) as get:
                status = check_application_url_status("https://example.test/apply")

        self.assertEqual(status, 200)
        head.assert_called_once()
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=0-1023")

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
