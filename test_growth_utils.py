"""Unit tests for growth_utils (no Telegram token required)."""
import unittest

from growth_utils import (
    RAPIDFUZZ_AVAILABLE,
    build_referral_link,
    enrich_job_salary_fields,
    fuzzy_is_near_duplicate,
    job_fingerprint,
    job_matches_profile,
    parse_salary_to_usd_min,
    parse_start_payload,
    passes_channel_tracks,
    passes_min_salary,
    serialize_job_payload,
)


class GrowthUtilsTests(unittest.TestCase):
    def test_fingerprint(self):
        fp = job_fingerprint({"title": "Python Developer", "company": "Acme"})
        self.assertIn("python", fp)
        self.assertIn("acme", fp)

    def test_fuzzy_exact(self):
        job = {"title": "Junior Python Dev", "company": "X Corp"}
        fps = [job_fingerprint(job)]
        self.assertTrue(fuzzy_is_near_duplicate(job, fps, threshold=90))

    def test_fuzzy_near(self):
        if not RAPIDFUZZ_AVAILABLE:
            self.skipTest("rapidfuzz not installed")
        job = {"title": "Junior Python Developer", "company": "Acme Inc"}
        fps = ["junior python developer::acme"]
        self.assertTrue(fuzzy_is_near_duplicate(job, fps, threshold=85))

    def test_salary_rub_monthly(self):
        job = {"salary": "от 150000 ₽"}
        amount = parse_salary_to_usd_min(job)
        self.assertIsNotNone(amount)
        self.assertGreater(amount, 1000)

    def test_salary_usd_range(self):
        job = {"salary": "$3000-5000"}
        amount = parse_salary_to_usd_min(job)
        self.assertIsNotNone(amount)
        self.assertGreaterEqual(amount, 3000)

    def test_min_salary_filter(self):
        job = enrich_job_salary_fields({"salary": "$5000"})
        self.assertTrue(passes_min_salary(job, 1000))
        self.assertFalse(passes_min_salary(job, 50_000))
        # unknown salary kept
        self.assertTrue(passes_min_salary({"salary": "Не указана"}, 1000))

    def test_referral_link(self):
        self.assertEqual(
            build_referral_link("my_job_bot", 42),
            "https://t.me/my_job_bot?start=ref_42",
        )

    def test_start_payload(self):
        kind, rid = parse_start_payload(["ref_99"])
        self.assertEqual(kind, "ref")
        self.assertEqual(rid, 99)
        kind, rid = parse_start_payload([])
        self.assertIsNone(kind)

    def test_profile_match_category(self):
        job = {"title": "Python Dev", "category": "development", "level": "Junior", "tags": ["python"]}
        settings = {
            "enabled_categories": ["qa"],
            "min_salary_filter": 0,
            "skills": "",
            "hide_senior": True,
        }
        self.assertFalse(job_matches_profile(job, settings))
        settings["enabled_categories"] = ["development"]
        self.assertTrue(job_matches_profile(job, settings))

    def test_profile_match_skills(self):
        job = {"title": "Backend", "category": "development", "description": "Go microservices", "tags": []}
        settings = {
            "enabled_categories": ["development"],
            "skills": "python, django",
            "min_salary_filter": 0,
            "hide_senior": True,
        }
        self.assertFalse(job_matches_profile(job, settings))
        job["description"] = "Python FastAPI"
        self.assertTrue(job_matches_profile(job, settings))

    def test_channel_tracks(self):
        job = {"category": "marketing"}
        self.assertFalse(passes_channel_tracks(job, ["development", "qa"]))
        self.assertTrue(passes_channel_tracks(job, ["all"]))
        self.assertTrue(passes_channel_tracks({"category": "qa"}, ["development", "qa"]))

    def test_serialize_payload(self):
        p = serialize_job_payload({"title": "T", "tags": ["a"], "description": "x" * 2000})
        self.assertEqual(p["title"], "T")
        self.assertLessEqual(len(p["description"]), 800)


if __name__ == "__main__":
    unittest.main()
