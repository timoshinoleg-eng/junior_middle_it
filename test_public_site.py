import unittest

import public_site


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


if __name__ == "__main__":
    unittest.main()
