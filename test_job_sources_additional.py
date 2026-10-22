"""Free, attributable vacancy adapters: local fixtures only; never post during tests."""
import unittest
from unittest.mock import MagicMock, patch

import job_sources_additional as extra
import channel_bot as core
from message_formatter import JobMessageFormatter


class SourceNormalizationTests(unittest.TestCase):
    def test_remotejobs_preserves_original_listing_and_salary_period(self):
        source = {
            "title": "Junior Python Developer",
            "company": {"name": "Sample Labs"},
            "description": "<p>Build Python APIs and integrate tests.</p>",
            "url": "https://remotejobs.org/remote-jobs/sample-junior-python",
            "posted_at": "2026-10-22T10:00:00Z",
            "salary_text": "USD 3,000-4,000/month",
            "location": "Remote (Worldwide)",
            "type": "Full-time",
        }
        job = extra.normalize_remotejobs_org(source)
        self.assertEqual(job["source"], "RemoteJobs.org")
        self.assertEqual(job["salary"], "USD 3,000-4,000/month")
        self.assertEqual(job["published"], source["posted_at"])
        self.assertIn("Build Python APIs", job["description"])
        self.assertNotIn("<p>", job["description"])
        self.assertEqual(job["url"], source["url"])

    def test_remotejobs_rejects_off_domain_or_missing_timestamp(self):
        source = {"title": "Junior Engineer", "company": {"name": "Demo"},
                  "posted_at": "2026-10-22", "url": "https://remotejobs.org.evil.test/job"}
        self.assertIsNone(extra.normalize_remotejobs_org(source))
        source["url"] = "https://remotejobs.org/jobs/demo"
        del source["posted_at"]
        self.assertIsNone(extra.normalize_remotejobs_org(source))

    def test_remotelanders_only_uses_verifiable_structured_facts(self):
        source = {"title": "Mid-level Backend Developer",
                  "company": "Sample Labs", "postedDate": "2026-10-22",
                  "location": "United Kingdom", "level": "Mid-level",
                  "type": "Full-time", "category": "Engineering",
                  "salary": "$60k-70k/year", "subtags": ["Backend", "Python"],
                  "url": "https://remotelanders.com/remote-jobs/mid-backend"}
        job = extra.normalize_remote_landers(source)
        self.assertEqual(job["source"], "Remote Landers")
        self.assertEqual(job["location"], "Remote — United Kingdom")
        self.assertEqual(job["api_level"], "Mid-level")
        self.assertEqual(job["salary"], "$60k-70k/year")
        self.assertIn("Location restriction: United Kingdom", job["description"])
        self.assertNotIn("unlimited vacation", job["description"])
        self.assertEqual(job["url"], source["url"])
        source["location"] = "Not stated"
        self.assertIsNone(extra.normalize_remote_landers(source))

    def test_fetcher_no_double_count_and_uses_public_page_only(self):
        payload = {"data": [
            {"title": "Junior Python Developer", "company": {"name": "Demo"},
             "url": "https://remotejobs.org/jobs/example",
             "posted_at": "2026-10-22", "location": "Remote"},
        ]}
        fake = MagicMock()
        fake.json.return_value = payload
        with patch.object(extra.requests, "get", return_value=fake) as mock_get:
            jobs = extra.fetch_remotejobs_org()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(mock_get.call_count, 4)

    def test_landers_partial_network_failure_keeps_first_page(self):
        item = {"title": "Junior QA", "company": "Acme", "postedDate": "2026-10-22",
                "location": "Worldwide", "url": "https://remotelanders.com/remote-jobs/abc"}
        response = MagicMock()
        response.json.return_value = {"jobs": [item]}
        with patch.object(extra.requests, "get", side_effect=[
            response, extra.requests.Timeout("timeout")
        ]):
            jobs = extra.fetch_remote_landers()
        self.assertEqual(len(jobs), 1)

    def test_himalayas_targeted_preserves_required_attribution_and_salary_period(self):
        item = {
            "title": "Junior QA Engineer", "companyName": "Demo Labs",
            "applicationLink": "https://himalayas.app/companies/demo/jobs/qa?utm_source=api",
            "pubDate": 1792690000, "locationRestrictions": ["United States"],
            "seniority": "Entry Level", "employmentType": "Full-time",
            "description": "<p>Test software applications and maintain QA cases.</p>",
            "categories": None, "parentCategories": ["Testing"],
            "minSalary": 3500, "maxSalary": 4500, "salaryPeriod": "monthly", "currency": "USD",
        }
        job = extra.normalize_himalayas_targeted(item)
        self.assertEqual(job["source"], "Himalayas")
        self.assertEqual(job["url"], item["applicationLink"])
        self.assertEqual(job["salary"], "3,500–4,500 USD/monthly")
        self.assertEqual(job["location"], "Remote — United States")
        self.assertEqual(job["api_level"], "Entry Level")
        self.assertEqual(job["tags"], ["Testing"])

    def test_registry_is_wired_with_existing_filters(self):
        names = [name for _, name in core.get_api_fetch_functions()]
        self.assertEqual(names.count("RemoteJobs.org"), 1)
        self.assertEqual(names.count("Remote Landers"), 1)
        self.assertIn("Himalayas", names)
        self.assertIn("Jobicy", names)
        self.assertEqual(names.count("Himalayas targeted"), 1)


class BetterCardTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "title": "Junior Backend Developer", "company": "Sample Labs",
            "level": "Junior", "category": "development",
            "location": "Remote · United Kingdom",
            "description": "Build services using Python and SQL for sample customers.",
            "salary": "USD 3000-4000/month",
            "source": "Remote Landers",
            "employment_type": "Full-time",
            "tags": ["Python", "SQL", "Python"],
            "url": "https://remotelanders.com/remote-jobs/demo",
            "hash": "abc123",
        }

    def test_compact_card_contains_salary_skills_and_attribution(self):
        msg = JobMessageFormatter().format_job(self.job, "compact")
        self.assertIn("Junior Backend Developer", msg.text)
        self.assertIn("Python", msg.text)
        self.assertIn("Full", msg.text)
        self.assertIn("Job data provided by Remote Landers", msg.text)
        self.assertIn("https://remotelanders.com", msg.text)
        self.assertEqual(msg.parse_mode, "MarkdownV2")

    def test_only_appropriate_sources_get_credit_in_compact(self):
        job = dict(self.job, source="Himalayas")
        msg = JobMessageFormatter().format_job(job, "compact")
        self.assertIn("Himalayas", msg.text)
        job["source"] = "internal"
        msg = JobMessageFormatter().format_job(job, "compact")
        self.assertNotIn("Источник", msg.text)


if __name__ == "__main__":
    unittest.main()
