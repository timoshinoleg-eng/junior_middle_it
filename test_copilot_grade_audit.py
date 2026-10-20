import unittest

from copilot import fit
from copilot import grade_audit


class GradeAuditTests(unittest.TestCase):
    def test_junior_title_with_senior_requirements_is_flagged(self):
        job = {
            "title": "Junior Python Developer",
            "company": "Acme",
            "description": "Python, FastAPI. Требуется опыт 5+ лет, вы будете lead а команды.",
        }
        audit = grade_audit.audit_grade(job)
        self.assertEqual(audit.declared_grade, fit.GRADE_JUNIOR)
        self.assertEqual(audit.real_grade, fit.GRADE_SENIOR)
        self.assertTrue(audit.is_fake_grade)
        self.assertLess(audit.realism_score, 50)
        self.assertIn("senior", audit.badge_text)

    def test_honest_junior_vacancy_is_not_flagged(self):
        job = {
            "title": "Junior Python Developer",
            "description": "Python, FastAPI, PostgreSQL. Опыт 1-2 года. Наш senior mentor поможет.",
        }
        audit = grade_audit.audit_grade(job)
        self.assertFalse(audit.is_fake_grade)
        self.assertEqual(audit.real_grade, fit.GRADE_JUNIOR)
        self.assertEqual(audit.realism_score, 100)

    def test_middle_vacancy_asking_for_five_years_is_flagged(self):
        job = {"title": "Middle Backend Engineer", "description": "Go, Postgres. Опыт 5+ лет."}
        audit = grade_audit.audit_grade(job)
        self.assertTrue(audit.is_fake_grade)
        self.assertEqual(audit.declared_grade, fit.GRADE_MIDDLE)

    def test_vacancy_without_level_in_the_title_is_not_flagged(self):
        job = {"title": "Backend Engineer", "description": "Python, Postgres. Опыт 6+ лет."}
        audit = grade_audit.audit_grade(job)
        self.assertEqual(audit.declared_grade, fit.GRADE_UNKNOWN)
        self.assertFalse(audit.is_fake_grade)

    def test_leadership_requirement_is_a_senior_signal(self):
        job = {"title": "Backend Engineer", "description": "Python. You will lead a team of 5."}
        self.assertEqual(fit.detect_real_grade(job), fit.GRADE_SENIOR)

    def test_audit_is_deterministic_and_serialisable(self):
        job = {"title": "Junior QA Engineer", "description": "Python, pytest. Опыт 2 года."}
        first = grade_audit.audit_grade(job)
        second = grade_audit.audit_grade(job)
        self.assertEqual(first.as_dict(), second.as_dict())
        restored = grade_audit.GradeAudit.from_dict(first.as_dict())
        self.assertEqual(restored, first)

    def test_signals_are_collected_for_the_verdict(self):
        job = {"title": "Junior DevOps Engineer", "description": "Kubernetes, Terraform. Опыт 5+ лет."}
        audit = grade_audit.audit_grade(job)
        self.assertTrue(audit.signals)

    def test_badge_does_not_claim_that_the_title_has_no_level(self):
        job = {
            "title": "Junior Backend Engineer (Python) - Remote",
            "description": "Python, FastAPI, Docker, PostgreSQL.",
        }
        audit = grade_audit.audit_grade(job)
        self.assertEqual(audit.declared_grade, fit.GRADE_JUNIOR)
        self.assertEqual(audit.real_grade, fit.GRADE_UNKNOWN)
        self.assertNotIn("не указан", audit.badge_text)
        self.assertIn("Junior", audit.badge_text)

    def test_badge_reports_the_requirements_when_the_title_has_no_level(self):
        job = {"title": "Backend Engineer", "description": "Python, Postgres. Опыт 4+ лет."}
        audit = grade_audit.audit_grade(job)
        self.assertEqual(audit.declared_grade, fit.GRADE_UNKNOWN)
        self.assertEqual(audit.real_grade, fit.GRADE_MIDDLE)
        self.assertIn("middle", audit.badge_text)

    def test_unclear_requirements_are_reported_as_a_signal(self):
        job = {"title": "Junior Python Developer", "description": "Python, FastAPI."}
        audit = grade_audit.audit_grade(job)
        self.assertIn("По требованиям уровень не уточнён", audit.signals)

    def test_every_grade_pair_has_its_own_badge(self):
        grades = (fit.GRADE_JUNIOR, fit.GRADE_MIDDLE, fit.GRADE_SENIOR, fit.GRADE_UNKNOWN)
        for declared in grades:
            for real in grades:
                self.assertIn((declared, real), grade_audit._BADGES)


if __name__ == "__main__":
    unittest.main()
