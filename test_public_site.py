import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import public_site
from api.site import _is_fresh_public_job, _public_site_url


class PublicSiteTests(unittest.TestCase):
    def _job(self, **overrides):
        job = {
            "hash": "abc123",
            "title": "Junior Python Developer",
            "company": "Example <script>alert(1)</script>",
            "url": "https://example.com/jobs/1",
            "level": "Junior",
            "category": "development",
            "location": "Remote",
            "salary": "$1500–2200",
            "source": "ATS",
            "quality_gate_status": "passed",
            "url_preflight_status": "ok",
            "tags": ["Python", "FastAPI"],
        }
        job.update(overrides)
        return job

    def test_deep_links_are_sanitized_and_attributable(self):
        self.assertEqual(
            public_site.bot_deep_link("@junior_jobs_channel_bot", "web_abc-123"),
            "https://t.me/junior_jobs_channel_bot?start=web_abc-123",
        )
        self.assertEqual(public_site.bot_deep_link("bad name", "web_abc"), "")
        self.assertEqual(
            public_site.bot_deep_link("valid_bot", "../../evil"),
            "https://t.me/valid_bot?start=evil",
        )

    def test_only_junior_middle_quality_jobs_are_public(self):
        self.assertTrue(public_site.is_public_job(self._job()))
        self.assertFalse(public_site.is_public_job(self._job(level="Senior")))
        self.assertFalse(public_site.is_public_job(self._job(quality_gate_status="failed")))
        self.assertFalse(public_site.is_public_job(self._job(url="javascript:alert(1)")))

    def test_public_landing_requires_verified_fresh_source_date(self):
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(
            _is_fresh_public_job(
                self._job(source_published_at="2026-09-10T18:30:00Z"),
                max_age_days=7,
                now=now,
            )
        )
        self.assertFalse(_is_fresh_public_job(self._job(), max_age_days=7, now=now))
        self.assertFalse(
            _is_fresh_public_job(
                self._job(source_published_at="2026-09-01T11:00:00Z"),
                max_age_days=7,
                now=now,
            )
        )
        self.assertFalse(
            _is_fresh_public_job(
                self._job(source_published_at="2026-09-12T12:00:00Z"),
                max_age_days=7,
                now=now,
            )
        )

    def test_public_landing_accepts_epoch_and_rfc2822_source_dates(self):
        now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(
            _is_fresh_public_job(
                self._job(source_published_at="Thu, 10 Sep 2026 12:00:00 +0000"),
                now=now,
            )
        )
        epoch_ms = int(datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertTrue(
            _is_fresh_public_job(
                self._job(source_published_at=str(epoch_ms)),
                now=now,
            )
        )

    def test_selection_applies_filters_and_company_diversity(self):
        jobs = [self._job(hash=f"a{i}") for i in range(4)]
        jobs.append(self._job(hash="qa1", company="QA Co", category="qa", title="Junior QA"))
        selected = public_site.select_public_jobs(jobs, limit=10)
        self.assertEqual(sum(1 for j in selected if j["company"].startswith("Example")), 2)
        qa = public_site.select_public_jobs(jobs, category="qa", limit=10)
        self.assertEqual([j["hash"] for j in qa], ["qa1"])

    def test_render_escapes_content_and_exposes_expected_ctas(self):
        page = public_site.render_landing(
            [self._job()],
            bot_username="junior_jobs_channel_bot",
            channel_id="@junior_jobs",
            public_site_url="https://jobs.example.com/",
        )
        self.assertIn("web_abc123", page)
        self.assertIn("resume_abc123", page)
        self.assertIn("web_home", page)
        self.assertIn("https://example.com/jobs/1", page)
        self.assertIn("Example &lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn('rel="canonical" href="https://jobs.example.com/"', page)
        self.assertIn('meta name="robots" content="index,follow', page)

    def test_empty_state_still_has_bot_acquisition_cta(self):
        page = public_site.render_landing(
            [],
            bot_username="junior_jobs_channel_bot",
        )
        self.assertIn("web_home", page)
        self.assertIn("Подборка обновляется", page)

    def test_runtime_public_url_prefers_explicit_override(self):
        with patch.dict(
            os.environ,
            {
                "PUBLIC_SITE_URL": "https://jobs.example.com",
                "VERCEL_PROJECT_PRODUCTION_URL": "junior-middle-it.vercel.app",
            },
            clear=True,
        ):
            self.assertEqual(_public_site_url(), "https://jobs.example.com")

    def test_runtime_public_url_falls_back_to_vercel_production_domain(self):
        with patch.dict(
            os.environ,
            {"VERCEL_PROJECT_PRODUCTION_URL": "junior-middle-it.vercel.app"},
            clear=True,
        ):
            self.assertEqual(
                _public_site_url(),
                "https://junior-middle-it.vercel.app",
            )

    def test_runtime_public_url_is_empty_without_explicit_or_vercel_domain(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_public_site_url(), "")


if __name__ == "__main__":
    unittest.main()
