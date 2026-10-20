import unittest

from copilot import upskill


PROFILE = {"skills": "python, fastapi"}

PYTHON_JOB = {
    "title": "Junior Python Developer",
    "description": "Python и FastAPI, удалённо",
    "salary_min_usd": 1200,
}

DEVOPS_JOB = {
    "title": "DevOps Engineer",
    "description": "Kubernetes, Terraform, Docker",
    "salary_min_usd": 3000,
}

MIXED_JOB = {
    "title": "Backend Developer",
    "description": "Python, FastAPI, PostgreSQL",
}

HALF_JOB = {
    "title": "Backend Developer",
    "description": "Python, FastAPI, PostgreSQL, Docker",
}


class RadarTests(unittest.TestCase):
    def test_empty_feed_gives_an_empty_radar(self):
        radar = upskill.build_radar([], PROFILE)
        self.assertEqual(radar.total, 0)
        self.assertEqual(radar.covered, 0)
        self.assertEqual(radar.coverage_pct, 0)

    def test_covered_vacancy_is_counted(self):
        radar = upskill.build_radar([PYTHON_JOB], PROFILE)
        self.assertEqual(radar.total, 1)
        self.assertEqual(radar.covered, 1)
        self.assertEqual(radar.coverage_pct, 100)

    def test_unrelated_vacancy_is_not_counted(self):
        radar = upskill.build_radar([DEVOPS_JOB], PROFILE)
        self.assertEqual(radar.total, 1)
        self.assertEqual(radar.covered, 0)
        self.assertEqual(radar.coverage_pct, 0)

    def test_mixed_vacancy_is_partially_covered(self):
        radar = upskill.build_radar([MIXED_JOB], PROFILE)
        self.assertEqual(radar.total, 1)
        self.assertEqual(radar.covered, 1)
        self.assertEqual(radar.coverage_pct, 100)

    def test_half_covered_vacancy_is_not_counted(self):
        radar = upskill.build_radar([HALF_JOB], PROFILE)
        self.assertEqual(radar.total, 1)
        self.assertEqual(radar.covered, 0)
        self.assertEqual(radar.coverage_pct, 0)

    def test_coverage_is_aggregated_over_the_feed(self):
        radar = upskill.build_radar([PYTHON_JOB, DEVOPS_JOB, PYTHON_JOB], PROFILE)
        self.assertEqual(radar.total, 3)
        self.assertEqual(radar.covered, 2)
        self.assertEqual(radar.coverage_pct, 67)

    def test_missing_skills_are_ranked(self):
        radar = upskill.build_radar([DEVOPS_JOB, DEVOPS_JOB], PROFILE)
        names = [name for name, _count in radar.top_missing]
        self.assertIn("docker", names)
        self.assertIn("kubernetes", names)
        counts = dict(radar.top_missing)
        self.assertEqual(counts["docker"], 2)

    def test_matched_skills_are_ranked(self):
        radar = upskill.build_radar([PYTHON_JOB, PYTHON_JOB], PROFILE)
        names = [name for name, _count in radar.top_matched]
        self.assertIn("python", names)

    def test_salary_median_is_computed_for_covered_vacancies(self):
        radar = upskill.build_radar([PYTHON_JOB, DEVOPS_JOB], PROFILE)
        self.assertEqual(radar.salary_jobs, 1)
        self.assertEqual(radar.salary_median, 1200)

    def test_salary_is_parsed_from_text(self):
        job = dict(PYTHON_JOB)
        job.pop("salary_min_usd")
        job["salary"] = "от 1500 USD"
        radar = upskill.build_radar([job], PROFILE)
        self.assertEqual(radar.salary_median, 1500)

    def test_radar_is_serializable(self):
        payload = upskill.build_radar([PYTHON_JOB], PROFILE).as_dict()
        self.assertEqual(payload["total"], 1)
        self.assertIn("top_missing", payload)


class FormatTests(unittest.TestCase):
    def test_empty_radar_explains_itself(self):
        text = upskill.format_radar(upskill.build_radar([], PROFILE))
        self.assertIn("Upskill-радар", text)
        self.assertIn("Пока нет вакансий", text)

    def test_radar_reports_coverage_and_gaps(self):
        radar = upskill.build_radar([PYTHON_JOB, DEVOPS_JOB], PROFILE)
        text = upskill.format_radar(radar, days=30)
        self.assertIn("50%", text)
        self.assertIn("30 дн", text)
        self.assertIn("docker", text.lower())
        self.assertIn("эвристика", text.lower())

    def test_radar_mentions_the_salary_when_it_is_known(self):
        radar = upskill.build_radar([PYTHON_JOB], PROFILE)
        text = upskill.format_radar(radar)
        self.assertIn("$1200", text)


if __name__ == "__main__":
    unittest.main()
