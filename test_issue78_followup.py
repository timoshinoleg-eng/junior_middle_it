import unittest

from copilot.forward import is_forward_candidate, looks_like_resume
from resume_matcher import analyze_resume_match, format_resume_match_report


class PrivacyAndEvidenceTests(unittest.TestCase):
    def test_unbound_unlabelled_resumes_with_links_never_become_jobs(self):
        samples = [
            'Software engineer with three years building Python services. Portfolio https://example.com/me',
            'Разработчик, три года создавал сервисы на Python и работал в команде. https://example.com/me',
        ]
        for text in samples:
            for _ in range(2):
                with self.subTest(text=text):
                    self.assertFalse(is_forward_candidate(text))

    def test_forwarded_vacancy_is_still_supported(self):
        self.assertTrue(is_forward_candidate('Hiring Python developer. Apply at https://example.com/jobs', forwarded=True))

    def test_forwarded_unlabelled_candidate_profile_is_recognized(self):
        self.assertTrue(looks_like_resume('Work experience: built Python services. Education: computer science. https://example.com/portfolio'))

    def test_no_evidence_is_not_zero_percent_fit(self):
        job = {'title': 'Opportunity', 'description': ''}
        report = analyze_resume_match('Python developer building applications', job)
        text = format_resume_match_report(report, job)
        self.assertIn('Недостаточно данных для оценки соответствия', text)
        self.assertNotIn('Resume Match: 0%', text)
        self.assertNotIn('Что мешает отклику', text)
        self.assertEqual(report.score, 0)

    def test_no_skills_with_role_and_keyword_overlap_has_no_percentage(self):
        job = {'title': 'Software Engineer II - Full Stack', 'company': 'Smartsheet'}
        report = analyze_resume_match('Software engineer full stack building software applications', job)
        self.assertTrue(report.role_overlap)
        self.assertGreater(report.keyword_overlap_pct, 0)
        self.assertNotIn('%', format_resume_match_report(report, job))

    def test_explicit_requirements_are_evidence(self):
        job = {'title': 'Developer', 'requirements': 'Python and Docker', 'skills': ['SQL']}
        report = analyze_resume_match('Python developer', job)
        self.assertEqual(set(report.required_skills), {'python', 'docker', 'sql'})
        self.assertIn('Resume Match:', format_resume_match_report(report, job))
        self.assertNotIn('redis', report.missing_skills)


if __name__ == '__main__':
    unittest.main()
