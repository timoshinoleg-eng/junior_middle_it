import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from copilot.forward import is_forward_candidate, looks_like_resume
import copilot_webhook_runtime as webhook
import localized_product_runtime as product
from resume_matcher import analyze_resume_match, format_resume_match_report


class PrivacyAndEvidenceTests(unittest.TestCase):
    def test_forwarded_employer_headings_route_to_analysis(self):
        samples = [
            'Требования:\nОпыт работы: от 3 лет.\nОбразование: высшее.\nИщем Python разработчика для REST API и Docker.',
            'Hiring Software Engineer\nWork experience: 3 years\nEducation: degree\nRequirements: Python, Docker and REST APIs.',
        ]
        for text in samples:
            with self.subTest(text=text):
                msg = SimpleNamespace(text=text, document=None, forward_date=1, reply_text=AsyncMock())
                update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=42))
                db = SimpleNamespace(get_user_settings=lambda _: {}, log_event=lambda *args: None)
                bot = SimpleNamespace(setup_steps={}, resume_sessions={}, db=db)
                store = SimpleNamespace(save_forwarded_job=unittest.mock.Mock())
                self.assertFalse(looks_like_resume(text))
                self.assertFalse(webhook._unmatched_resume_text(bot, update))
                with patch.object(webhook, 'copilot_enabled', return_value=True), \
                     patch.object(webhook, 'forward_enabled', return_value=True), \
                     patch.object(webhook.CopilotStore, 'from_database', return_value=store), \
                     patch.object(webhook, '_pdf_requests', return_value={}), \
                     patch.object(webhook.feedback_flow, 'capture_if_pending', new=AsyncMock(return_value=False)), \
                     patch.object(webhook.resume_profile_flow, 'save_and_report', new=AsyncMock(side_effect=AssertionError('CV route'))):
                    asyncio.run(webhook._copilot_handle_setup_text(bot, update, None))
                store.save_forwarded_job.assert_called_once()
                self.assertTrue(msg.reply_text.await_args.kwargs.get('disable_web_page_preview'))

    def test_candidate_profiles_never_saved_as_forwarded_jobs(self):
        samples = [
            'Опыт работы: разрабатывал Python сервисы.\nОбразование: высшее.\nПортфолио https://example.com/me',
            'Work experience: I built Python services.\nEducation: degree.\nPortfolio https://example.com/me',
            'Work experience: built services. Education: degree. Requirements: Python. Hiring engineers was part of my job. https://example.com/me',
            'Ищу работу. Вакансия: разработчик. Опыт работы: 3 года. Образование: высшее. https://example.com/me',
        ]
        for text in samples:
            with self.subTest(text=text):
                msg = SimpleNamespace(text=text, document=None, forward_date=1, reply_text=AsyncMock())
                update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=42))
                bot = SimpleNamespace(setup_steps={}, resume_sessions={}, db=object())
                self.assertTrue(looks_like_resume(text))
                self.assertTrue(webhook._unmatched_resume_text(bot, update))
                with patch.object(webhook, 'copilot_enabled', return_value=True), \
                     patch.object(webhook, 'forward_enabled', return_value=True), \
                     patch.object(webhook.CopilotStore, 'from_database', side_effect=AssertionError('unexpected storage')):
                    self.assertTrue(asyncio.run(webhook._maybe_handle_forward(bot, update)))

    def test_short_resume_gets_guidance_without_storage(self):
        msg = SimpleNamespace(text='Python developer', document=None, forward_date=None, reply_text=AsyncMock())
        update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=42))
        bot = SimpleNamespace(setup_steps={}, resume_sessions={})
        with patch.object(webhook, '_pdf_requests', return_value={}), \
             patch.object(webhook.feedback_flow, 'capture_if_pending', new=AsyncMock(return_value=False)), \
             patch.object(webhook.CopilotStore, 'from_database', side_effect=AssertionError('unexpected storage')):
            asyncio.run(webhook._copilot_handle_setup_text(bot, update, None))
        self.assertIn('вакансия не выбрана', msg.reply_text.await_args.args[0])

    def test_short_messages_distinguish_greetings_from_resume_attempts(self):
        bot = SimpleNamespace(setup_steps={}, resume_sessions={})
        for text, expected in [('Привет', False), ('спасибо', False), ('hello', False),
                               ('Python developer', True), ('2 года Python', True),
                               ('QA engineer', True), ('разработчик', True),
                               ('Python https://example.com/me', True),
                               ('hello https://example.com', False)]:
            with self.subTest(text=text):
                msg = SimpleNamespace(text=text, document=None, forward_date=None)
                update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=42))
                self.assertEqual(webhook._unmatched_resume_text(bot, update), expected)

    def test_unscored_reports_describe_missing_technical_evidence_not_sparse_vacancy(self):
        bot = object.__new__(product.JobBot)
        bot._lang = lambda _: 'en'
        jobs = [
            {'title': 'Sales Manager', 'description': 'Manage sales, CRM pipelines, negotiations, customer relationships and quarterly revenue targets.'},
            {'title': 'Software Engineer II - Full Stack', 'company': 'Smartsheet'},
        ]
        for job in jobs:
            with self.subTest(job=job):
                report = analyze_resume_match('Python developer', job)
                self.assertFalse(report.required_skills)
                ru = format_resume_match_report(report, job)
                en = bot._format_resume_report(42, report, job)
                self.assertNotIn('%', ru)
                self.assertNotIn('%', en)
                self.assertIn('Не удалось выделить конкретные технические требования', ru)
                self.assertIn('Это НЕ означает, что резюме не подходит', ru)
                self.assertIn('could not identify specific technical requirements', en)
                self.assertIn('does NOT mean', en)
                self.assertNotIn('lacks reliable information', en)

    def test_unbound_unlabelled_resumes_with_links_never_become_jobs(self):
        samples = [
            'Software engineer with three years building Python services. Portfolio https://example.com/me',
            'Разработчик, три года создавал сервисы на Python и работал в команде. https://example.com/me',
        ]
        for text in samples:
            for _ in range(2):
                with self.subTest(text=text):
                    self.assertFalse(is_forward_candidate(text))
                    msg = SimpleNamespace(text=text, document=None, forward_date=None, reply_text=AsyncMock())
                    update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=42))
                    bot = SimpleNamespace(setup_steps={}, resume_sessions={}, db=object())
                    with patch.object(webhook, 'copilot_enabled', return_value=True), \
                         patch.object(webhook, 'forward_enabled', return_value=True), \
                         patch.object(webhook.CopilotStore, 'from_database', side_effect=AssertionError('unexpected storage')):
                        self.assertFalse(asyncio.run(webhook._maybe_handle_forward(bot, update)))

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
        self.assertEqual(set(report.matched_skills), {'python'})
        self.assertEqual(set(report.missing_skills), {'docker', 'sql'})
        self.assertIn('%', format_resume_match_report(report, job))
        self.assertIn('Resume Match:', format_resume_match_report(report, job))
        self.assertNotIn('redis', report.missing_skills)


if __name__ == '__main__':
    unittest.main()
