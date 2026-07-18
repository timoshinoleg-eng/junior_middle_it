"""Unit tests for growth_utils (no Telegram token required)."""
import unittest

from growth_utils import (
    RAPIDFUZZ_AVAILABLE,
    apply_premium_to_settings,
    build_referral_link,
    build_salary_magnet_report,
    compute_publish_score,
    enrich_job_salary_fields,
    fuzzy_is_near_duplicate,
    job_fingerprint,
    job_matches_profile,
    normalize_job_title_company,
    parse_channel_routes,
    parse_salary_to_usd_min,
    parse_start_payload,
    passes_channel_tracks,
    passes_min_salary,
    resolve_channels_for_job,
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

    def test_salary_magnet_report(self):
        jobs = [
            {"category": "development", "level": "Junior", "salary_min_usd": 30000},
            {"category": "development", "level": "Junior", "salary_min_usd": 40000},
            {"category": "qa", "level": "Middle", "salary_min_usd": 50000},
        ]
        text = build_salary_magnet_report(jobs, category_names={"development": "Разработка", "qa": "QA"})
        self.assertIn("salary", text.lower())
        self.assertIn("Junior", text)
        empty = build_salary_magnet_report([])
        self.assertIn("мало", empty.lower())

    def test_premium_settings(self):
        s = apply_premium_to_settings({"hide_senior": True}, True)
        self.assertFalse(s["hide_senior"])
        self.assertTrue(s["premium_unlocked"])

    def test_parse_channel_routes(self):
        raw = "development,qa,devops:@dev;data:@data;*:@main"
        routes = parse_channel_routes(raw)
        self.assertEqual(len(routes), 3)
        self.assertIn("development", routes[0][0])
        self.assertEqual(routes[0][1], "@dev")
        self.assertEqual(routes[2][1], "@main")

    def test_resolve_channels_specialty(self):
        routes = parse_channel_routes(
            "development,devops:@dev;qa:@qa;data:@data;*:@main"
        )
        ch = resolve_channels_for_job(
            {"category": "development"},
            routes,
            default_channel="@main",
            enabled=True,
            mirror_main=False,
        )
        self.assertEqual(ch, ["@dev"])
        ch_m = resolve_channels_for_job(
            {"category": "qa"},
            routes,
            default_channel="@main",
            enabled=True,
            mirror_main=True,
        )
        self.assertEqual(ch_m, ["@qa", "@main"])

    def test_resolve_channels_fallback(self):
        routes = parse_channel_routes("development:@dev;*:@main")
        ch = resolve_channels_for_job(
            {"category": "marketing"},
            routes,
            default_channel="@main",
            enabled=True,
        )
        self.assertEqual(ch, ["@main"])
        # multi-track off
        ch2 = resolve_channels_for_job(
            {"category": "development"},
            routes,
            default_channel="@main",
            enabled=False,
        )
        self.assertEqual(ch2, ["@main"])

    def test_normalize_title_company(self):
        job = {
            "title": "Proxify AB: Senior Fullstack Developer (React)",
            "company": "WWR Full-Stack",
        }
        normalize_job_title_company(job)
        self.assertEqual(job["company"], "Proxify AB")
        self.assertIn("Fullstack", job["title"])
        # non-generic company kept
        job2 = {"title": "Acme: Role Title Here", "company": "RealCorp"}
        normalize_job_title_company(job2)
        self.assertEqual(job2["company"], "RealCorp")

    def test_publish_score_junior_bias(self):
        junior = {
            "level": "Junior",
            "title": "Junior Python Developer",
            "url": "https://example.com/j",
            "salary": "$50k-$70k",
            "description": "x" * 300,
            "location": "Remote",
            "tags": ["python", "django"],
        }
        seniorish = {
            "level": "Middle",
            "title": "Senior Staff Engineer",
            "url": "",
            "salary": "Не указана",
            "description": "short",
            "location": "Office",
        }
        self.assertGreater(compute_publish_score(junior), compute_publish_score(seniorish))


if __name__ == "__main__":
    unittest.main()
