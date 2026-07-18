"""Unit tests for growth_utils (no Telegram token required)."""
import unittest

from growth_utils import (
    RAPIDFUZZ_AVAILABLE,
    build_referral_link,
    enrich_job_salary_fields,
    fuzzy_is_near_duplicate,
    job_fingerprint,
    parse_salary_to_usd_min,
    parse_start_payload,
    passes_min_salary,
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


if __name__ == "__main__":
    unittest.main()
