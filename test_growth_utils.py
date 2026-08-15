"""Unit tests for growth_utils (no Telegram token required)."""
import unittest

from growth_utils import (
    RAPIDFUZZ_AVAILABLE,
    apply_editorial_quality_gate,
    apply_premium_to_settings,
    build_referral_link,
    build_salary_magnet_report,
    build_specialization_tags,
    classify_thematic_track,
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

    def test_thematic_track_and_specialization(self):
        job = {
            "title": "Junior Backend Developer",
            "category": "development",
            "location": "Remote Worldwide",
            "description": "Build APIs with Python and FastAPI.",
        }
        self.assertEqual(classify_thematic_track(job), "development")
        self.assertIn("backend", build_specialization_tags(job))

        vibe_job = {
            "title": "Junior AI Builder",
            "category": "development",
            "location": "Remote",
            "description": "Build agentic workflows with no-code tools.",
        }
        self.assertEqual(classify_thematic_track(vibe_job), "vibe_coding")

    def test_editorial_gate_passes_explicit_worldwide_role(self):
        job = {
            "title": "Junior Backend Developer",
            "category": "development",
            "location": "Remote Worldwide",
            "description": "A remote role open to candidates from anywhere.",
        }
        apply_editorial_quality_gate(job)
        self.assertEqual(job["quality_gate_status"], "passed")
        self.assertEqual(job["remote_scope"], "worldwide")
        self.assertEqual(job["level_source"], "explicit_title")
        self.assertEqual(job["primary_track"], "development")

    def test_editorial_gate_keeps_geo_restriction_visible(self):
        job = {
            "title": "Junior QA Engineer",
            "category": "qa",
            "location": "Remote - India",
            "description": "API testing for a distributed team.",
        }
        apply_editorial_quality_gate(job)
        self.assertEqual(job["quality_gate_status"], "passed")
        self.assertEqual(job["remote_scope"], "country_restricted")
        self.assertEqual(job["location_restriction"], "Remote - India")

    def test_editorial_gate_quarantines_missing_remote_evidence(self):
        job = {
            "title": "Junior Product Designer",
            "category": "design",
            "location": "Berlin",
            "description": "Design product flows with the team.",
        }
        apply_editorial_quality_gate(job)
        self.assertEqual(job["quality_gate_status"], "quarantine")
        self.assertIn("missing_remote_evidence", job["quarantine_reasons"])

    def test_editorial_gate_quarantines_source_policy_without_role_evidence(self):
        job = {
            "title": "Junior Backend Engineer",
            "category": "development",
            "location": "Berlin",
            "description": "Build internal platform services.",
            "source": "RemoteOK",
        }
        apply_editorial_quality_gate(job, remote_only_sources=("RemoteOK",))
        self.assertEqual(job["quality_gate_status"], "quarantine")
        self.assertIn(
            "source_policy_without_explicit_remote_evidence",
            job["quarantine_reasons"],
        )

    def test_editorial_gate_accepts_explicit_portuguese_remote_with_scope(self):
        job = {
            "title": "Junior Golang Engineer",
            "category": "development",
            "location": "Remoto - Brazil",
            "description": "A remote engineering role for Brazil.",
        }
        apply_editorial_quality_gate(job)
        self.assertEqual(job["quality_gate_status"], "passed")
        self.assertEqual(job["remote_scope"], "country_restricted")

    def test_editorial_gate_excludes_experienced_conflict(self):
        job = {
            "title": "Junior Performance Engineer",
            "category": "development",
            "location": "Remote",
            "description": "Remote role requiring deep experience with distributed systems.",
        }
        apply_editorial_quality_gate(job)
        self.assertEqual(job["quality_gate_status"], "excluded")
        self.assertIn("seniority_conflict", job["quarantine_reasons"])

    def test_editorial_gate_excludes_hybrid_and_senior_conflicts(self):
        hybrid = {
            "title": "Junior QA Engineer",
            "category": "qa",
            "location": "Remote hybrid Warsaw",
            "description": "QA for a distributed product.",
        }
        apply_editorial_quality_gate(hybrid)
        self.assertEqual(hybrid["quality_gate_status"], "excluded")
        self.assertIn("hybrid_or_onsite_signal", hybrid["quarantine_reasons"])

        senior = {
            "title": "Senior Backend Engineer",
            "category": "development",
            "location": "Remote",
            "description": "Remote role for a senior engineer.",
            "level": "Junior",
        }
        apply_editorial_quality_gate(senior)
        self.assertEqual(senior["quality_gate_status"], "excluded")
        self.assertIn("seniority_conflict", senior["quarantine_reasons"])

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
