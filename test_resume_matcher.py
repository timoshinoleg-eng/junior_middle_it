import unittest

from resume_matcher import analyze_resume_match, extract_skills, format_resume_match_report


class ResumeMatcherTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "title": "Junior Python Backend Developer",
            "company": "Example Labs",
            "description": (
                "Build REST APIs with Python, FastAPI and PostgreSQL. "
                "Use Docker and Git in a remote product team."
            ),
            "tags": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        }

    def test_extract_skills_uses_explicit_aliases(self):
        skills = extract_skills("Python, FastAPI, Postgres, Docker, k8s and AWS")
        self.assertIn("python", skills)
        self.assertIn("fastapi", skills)
        self.assertIn("postgresql", skills)
        self.assertIn("docker", skills)
        self.assertIn("kubernetes", skills)
        self.assertIn("aws", skills)

    def test_report_only_credits_skills_present_in_resume(self):
        resume = (
            "Junior backend developer. Built two FastAPI services in Python with PostgreSQL. "
            "Handled 10000 requests/day and used GitHub for code review."
        )
        report = analyze_resume_match(resume, self.job)
        self.assertIn("python", report.matched_skills)
        self.assertIn("fastapi", report.matched_skills)
        self.assertIn("postgresql", report.matched_skills)
        self.assertIn("docker", report.missing_skills)
        self.assertNotIn("docker", report.matched_skills)
        self.assertGreater(report.score, 40)
        self.assertLess(report.score, 100)

    def test_strong_resume_scores_higher_than_irrelevant_resume(self):
        strong = analyze_resume_match(
            "Python backend developer: FastAPI, PostgreSQL, Docker, Git, REST API. "
            "Built 4 production APIs for 20000 users.",
            self.job,
        )
        weak = analyze_resume_match(
            "Graphic designer working in Figma on branding, illustrations and marketing layouts.",
            self.job,
        )
        self.assertGreater(strong.score, weak.score)
        self.assertGreaterEqual(strong.score, 80)

    def test_output_disclaims_official_ats_and_privacy(self):
        report = analyze_resume_match("Python FastAPI PostgreSQL developer " * 10, self.job)
        text = format_resume_match_report(report, self.job)
        self.assertIn("не официальный ATS-score", text)
        self.assertIn("Текст резюме не сохранялся", text)


if __name__ == "__main__":
    unittest.main()
