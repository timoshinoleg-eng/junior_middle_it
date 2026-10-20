import unittest

from copilot import forward


VACANCY = (
    "Junior Python Developer\n"
    "Acme, remote\n"
    "Python, FastAPI, PostgreSQL. Опыт 1-2 года. "
    "Вопросы по вакансии пишите @hr_ivan https://example.com/jobs/1"
)


class CandidateTests(unittest.TestCase):
    def test_forwarded_vacancy_is_a_candidate(self):
        self.assertTrue(forward.is_forward_candidate(VACANCY, forwarded=True))

    def test_command_is_never_a_candidate(self):
        self.assertFalse(forward.is_forward_candidate("/setup python", forwarded=True))
        self.assertFalse(forward.is_forward_candidate("/copilot abc", forwarded=False))

    def test_document_is_never_a_candidate(self):
        self.assertFalse(
            forward.is_forward_candidate(VACANCY, forwarded=True, has_document=True)
        )

    def test_short_text_is_not_a_candidate(self):
        self.assertFalse(forward.is_forward_candidate("ok", forwarded=True))

    def test_plain_text_without_a_link_is_not_a_candidate(self):
        self.assertFalse(forward.is_forward_candidate("Просто сообщение без ссылки"))

    def test_link_without_forward_is_a_candidate(self):
        text = "Вакансия Junior Python, ссылка https://example.com/jobs/2"
        self.assertTrue(forward.is_forward_candidate(text))

    def test_empty_text_is_not_a_candidate(self):
        self.assertFalse(forward.is_forward_candidate("", forwarded=True))


class ResumeGuardTests(unittest.TestCase):
    def test_resume_text_is_detected(self):
        self.assertTrue(forward.looks_like_resume("Резюме\nPython, 3 года опыта"))

    def test_resume_looking_text_with_signals_is_detected(self):
        text = "#resume\nО себе: делаю тесты\nЗарплатные ожидания: 2000 USD"
        self.assertTrue(forward.looks_like_resume(text))

    def test_vacancy_is_not_a_resume(self):
        self.assertFalse(forward.looks_like_resume(VACANCY))

    def test_plain_vacancy_words_are_not_a_resume(self):
        text = "Требуется Python разработчик в команду, ищем junior"
        self.assertFalse(forward.looks_like_resume(text))


class HashTests(unittest.TestCase):
    def test_same_text_gives_the_same_hash(self):
        first = forward.job_hash_for(VACANCY)
        second = forward.job_hash_for(VACANCY + " ")
        self.assertEqual(first, second)
        self.assertEqual(len(first), forward.HASH_CHARS)

    def test_hash_ignores_case_and_line_breaks(self):
        first = forward.job_hash_for("Junior Python Developer\nAcme")
        second = forward.job_hash_for("junior python developer   acme")
        self.assertEqual(first, second)

    def test_different_text_gives_a_different_hash(self):
        self.assertNotEqual(
            forward.job_hash_for(VACANCY),
            forward.job_hash_for(VACANCY + " другой текст"),
        )


class JobBuildingTests(unittest.TestCase):
    def test_job_is_built_from_the_text(self):
        job = forward.build_job(VACANCY)
        self.assertEqual(job["title"], "Junior Python Developer")
        self.assertEqual(job["url"], "https://example.com/jobs/1")
        self.assertEqual(job["source"], "forwarded")
        self.assertIn("Python", job["description"])
        self.assertEqual(job["hash"], forward.job_hash_for(VACANCY))

    def test_url_is_cleaned_from_punctuation(self):
        job = forward.build_job("Вакансия Python, подробности https://example.com/jobs/3.")
        self.assertEqual(job["url"], "https://example.com/jobs/3")

    def test_text_without_a_link_has_no_url(self):
        job = forward.build_job("Junior Python Developer, опыт 1 год, удалённо")
        self.assertEqual(job["url"], "")

    def test_row_is_rebuilt_into_a_job(self):
        row = {
            "job_hash": "abc",
            "title": "Junior QA",
            "company": "Acme",
            "url": "https://example.com/jobs/4",
            "text": "Junior QA Engineer, тесты",
        }
        job = forward.job_payload_from_row(row)
        self.assertEqual(job["hash"], "abc")
        self.assertEqual(job["title"], "Junior QA")
        self.assertEqual(job["source"], "forwarded")

    def test_missing_row_is_none(self):
        self.assertIsNone(forward.job_payload_from_row(None))


if __name__ == "__main__":
    unittest.main()
