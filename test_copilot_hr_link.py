import unittest

from copilot import hr_link
from copilot import fit


class HrHandleTests(unittest.TestCase):
    def test_contact_in_recruiting_context_is_found(self):
        text = "Вопросы по вакансии — пишите в Telegram @hr_ivan"
        self.assertEqual(hr_link.extract_hr_handle(text), "hr_ivan")

    def test_framework_handles_are_never_treated_as_contacts(self):
        text = "Стек: @nestjs, @typescript, @angular. Пишите hr в личку."
        self.assertIsNone(hr_link.extract_hr_handle(text))

    def test_bot_handles_are_ignored(self):
        text = "Напишите в @junior_jobs_channel_bot за деталями"
        self.assertIsNone(hr_link.extract_hr_handle(text))

    def test_ignored_handles_are_skipped(self):
        text = "Вопросы по вакансии пишите @recruiter_one"
        self.assertIsNone(hr_link.extract_hr_handle(text, ignore=["recruiter_one"]))

    def test_handle_without_contact_context_is_ignored(self):
        text = "Стек @python и ещё немного текста про вакансию"
        self.assertIsNone(hr_link.extract_hr_handle(text))

    def test_empty_text_is_safe(self):
        self.assertIsNone(hr_link.extract_hr_handle(""))

    def test_link_is_encoded_and_bounded(self):
        link = hr_link.build_hr_link("hr_ivan", "Здравствуйте! " + "a" * 2000)
        self.assertTrue(link.startswith("https://t.me/hr_ivan?text="))
        self.assertLess(len(link), 900)

    def test_link_without_message_has_no_query(self):
        self.assertEqual(hr_link.build_hr_link("hr_ivan"), "https://t.me/hr_ivan")

    def test_link_without_handle_is_empty(self):
        self.assertEqual(hr_link.build_hr_link(""), "")


class ContactMessageTests(unittest.TestCase):
    def test_message_uses_vacancy_facts_only(self):
        job = {"title": "Junior Python Developer", "company": "Acme"}
        report = fit.compute_fit({"skills": "python, fastapi"}, job)
        message = hr_link.build_contact_message(job, report)
        self.assertIn("Junior Python Developer", message)
        self.assertIn("Acme", message)
        self.assertIn("python", message)

    def test_message_does_not_invent_experience(self):
        job = {"title": "Junior Python Developer"}
        report = fit.compute_fit({"skills": ""}, job)
        message = hr_link.build_contact_message(job, report)
        self.assertNotIn("у меня 5 лет", message.lower())
        self.assertLess(len(message), 900)


if __name__ == "__main__":
    unittest.main()
