import io
import unittest

from copilot import pdf as copilot_pdf


RESUME_TEXT = """
Иван Петров
Разработчик Python
ivan.petrov@example.com
+7 999 123-45-67
Telegram: @ivan_dev
GitHub: github.com/ivan-dev
Опыт 3 года. Python, FastAPI, PostgreSQL, Docker, pytest.
Проект: сервис заказов на 12 000 запросов в сутки.
"""


class ContactExtractionTests(unittest.TestCase):
    def test_contacts_are_extracted_from_resume_text(self):
        contact = copilot_pdf.extract_contacts(RESUME_TEXT)
        self.assertEqual(contact.email, "ivan.petrov@example.com")
        self.assertIn("999", contact.phone)
        self.assertEqual(contact.telegram, "ivan_dev")
        self.assertEqual(contact.github, "ivan-dev")
        self.assertEqual(contact.name, "Иван Петров")

    def test_empty_text_yields_no_contacts(self):
        contact = copilot_pdf.extract_contacts("")
        self.assertEqual(contact.email, "")
        self.assertEqual(contact.name, "")


class CvBuildTests(unittest.TestCase):
    def _contact(self):
        return copilot_pdf.extract_contacts(RESUME_TEXT)

    def test_cv_is_a_readable_pdf_with_cyrillic(self):
        data = copilot_pdf.build_cv_pdf(
            contact=self._contact(),
            role="Junior Python Developer",
            company="Acme",
            skills=["python", "fastapi", "postgresql"],
        )
        self.assertTrue(data.startswith(b"%PDF"))
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        self.assertLessEqual(len(reader.pages), copilot_pdf.MAX_PAGES)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        self.assertIn("Иван Петров", text)
        self.assertIn("Junior Python Developer", text)
        self.assertIn("python", text.lower())

    def test_generated_cv_passes_its_own_ats_check(self):
        data = copilot_pdf.build_cv_pdf(
            contact=self._contact(),
            role="Junior Python Developer",
            company="Acme",
            skills=["python"],
        )
        report = copilot_pdf.validate_pdf_bytes(data)
        self.assertTrue(report.has_text_layer)
        self.assertFalse(report.broken_glyphs)
        self.assertLessEqual(report.pages, copilot_pdf.MAX_PAGES)
        self.assertEqual(report.problems, [])

    def test_cv_never_contains_the_whole_resume(self):
        data = copilot_pdf.build_cv_pdf(
            contact=self._contact(),
            role="Junior Python Developer",
            skills=["python"],
        )
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        self.assertNotIn("12 000 запросов", text)


class AtsValidationTests(unittest.TestCase):
    def test_broken_file_is_reported_instead_of_crashing(self):
        report = copilot_pdf.validate_pdf_bytes(b"not a pdf at all")
        self.assertFalse(report.ok)
        self.assertTrue(report.problems)

    def test_page_count_rule_is_enforced(self):
        from fpdf import FPDF

        pdf = FPDF()
        for _ in range(3):
            pdf.add_page()
            pdf.set_font("Helvetica", size=11)
            pdf.cell(0, 6, "page")
        data = bytes(pdf.output(dest="S"))
        report = copilot_pdf.validate_pdf_bytes(data)
        self.assertEqual(report.pages, 3)
        self.assertTrue(any("страниц" in problem for problem in report.problems))

    def test_keyword_coverage_is_measured_against_the_vacancy(self):
        data = copilot_pdf.build_cv_pdf(
            contact=copilot_pdf.extract_contacts(RESUME_TEXT),
            role="Python Developer",
            skills=["python", "fastapi"],
        )
        job = {"title": "Python Developer", "description": "Python, FastAPI, Kafka, Clickhouse"}
        report = copilot_pdf.validate_pdf_bytes(data, job=job)
        self.assertGreater(report.keyword_coverage_pct, 0)
        self.assertLess(report.keyword_coverage_pct, 100)

    def test_summary_is_honest(self):
        report = copilot_pdf.validate_pdf_bytes(b"broken")
        self.assertIn("ATS", report.summary())


if __name__ == "__main__":
    unittest.main()
