import unittest

from copilot import fit


JOB = {
    "title": "Junior Python Developer",
    "company": "Acme",
    "description": "Python, FastAPI, PostgreSQL, Docker. Опыт 1-2 года. Наш senior mentor поможет.",
    "tags": ["python", "fastapi"],
    "category": "development",
}


class FitScoringTests(unittest.TestCase):
    def test_score_is_deterministic(self):
        profile = {"skills": "python, fastapi, postgresql"}
        first = fit.compute_fit(profile, JOB)
        second = fit.compute_fit(profile, JOB)
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_matched_and_missing_skills_are_reported(self):
        report = fit.compute_fit({"skills": "python, fastapi"}, JOB)
        self.assertIn("python", report.matched_skills)
        self.assertIn("docker", report.missing_skills)
        self.assertGreater(report.score, 0)
        self.assertLessEqual(report.score, 100)

    def test_empty_profile_is_flagged_and_scored_low(self):
        report = fit.compute_fit({"skills": ""}, JOB)
        self.assertLess(report.score, 50)
        self.assertTrue(any("профил" in warning for warning in report.warnings))

    def test_vacancy_without_detectable_stack_is_not_scored_high(self):
        report = fit.compute_fit({"skills": "python"}, {"title": "Вакансия", "description": "Обязанности"})
        self.assertTrue(any("стек" in warning for warning in report.warnings))

    def test_senior_mention_in_a_junior_post_is_not_treated_as_senior(self):
        report = fit.compute_fit({"skills": "python"}, JOB)
        self.assertIsNone(fit.grade_conflict(JOB))

    def test_junior_title_with_senior_requirements_is_flagged(self):
        job = {
            "title": "Junior Backend Developer",
            "description": "Python, Go. Опыт 5+ лет обязателен.",
        }
        self.assertIsNotNone(fit.grade_conflict(job))
        report = fit.compute_fit({"skills": "python"}, job)
        self.assertTrue(any("senior" in warning for warning in report.warnings))

    def test_grade_detection_reads_the_title_only(self):
        self.assertEqual(fit.detect_declared_grade({"title": "Middle QA Engineer"}), fit.GRADE_MIDDLE)
        self.assertEqual(fit.detect_declared_grade({"title": "QA Engineer"}), fit.GRADE_UNKNOWN)

    def test_years_detection_handles_ranges_and_plurals(self):
        self.assertEqual(fit.detect_years_required("Опыт 1-2 года"), 2)
        self.assertEqual(fit.detect_years_required("Опыт от 3 лет"), 3)
        self.assertEqual(fit.detect_years_required("Опыт 5+ years"), 5)
        self.assertIsNone(fit.detect_years_required("Без опыта"))

    def test_report_contains_only_derived_data(self):
        report = fit.compute_fit({"skills": "python"}, JOB)
        payload = report.as_dict()
        self.assertNotIn("resume", str(payload).lower())


if __name__ == "__main__":
    unittest.main()
