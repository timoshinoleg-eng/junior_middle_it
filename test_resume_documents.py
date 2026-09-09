import unittest
from io import BytesIO

from docx import Document

from resume_documents import ResumeDocumentError, extract_resume_document


def build_docx(text: str) -> bytes:
    doc = Document()
    doc.add_heading("Resume", level=1)
    doc.add_paragraph(text)
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Skills"
    table.cell(0, 1).text = "Python, FastAPI, PostgreSQL, Docker"
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def build_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode())
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(data)


class ResumeDocumentTests(unittest.TestCase):
    def setUp(self):
        self.resume = (
            "Junior Python backend developer with commercial project experience. "
            "Built REST APIs with FastAPI and PostgreSQL, containerized services with Docker, "
            "used Git and CI/CD, wrote tests, reviewed pull requests and worked with product teams. "
            "Implemented integrations, database migrations and monitoring for production services."
        )

    def test_extracts_docx_paragraphs_and_tables(self):
        result = extract_resume_document(
            build_docx(self.resume),
            filename="resume.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(result.kind, "docx")
        self.assertIn("Junior Python backend developer", result.text)
        self.assertIn("FastAPI", result.text)

    def test_extracts_text_pdf(self):
        result = extract_resume_document(
            build_pdf(self.resume),
            filename="resume.pdf",
            mime_type="application/pdf",
        )
        self.assertEqual(result.kind, "pdf")
        self.assertIn("Python backend developer", result.text)
        self.assertIn("PostgreSQL", result.text)

    def test_rejects_fake_pdf_signature(self):
        with self.assertRaisesRegex(ResumeDocumentError, "не похоже на PDF"):
            extract_resume_document(
                b"not a pdf at all" * 20,
                filename="resume.pdf",
                mime_type="application/pdf",
            )

    def test_rejects_scanned_or_empty_pdf(self):
        # A valid blank PDF is intentionally treated as a scan/no-text case.
        blank = build_pdf("short")
        with self.assertRaisesRegex(ResumeDocumentError, "почти нет извлекаемого текста"):
            extract_resume_document(blank, filename="scan.pdf", mime_type="application/pdf")

    def test_rejects_unsupported_type(self):
        with self.assertRaisesRegex(ResumeDocumentError, "только PDF и DOCX"):
            extract_resume_document(b"plain resume text" * 20, filename="resume.txt", mime_type="text/plain")


if __name__ == "__main__":
    unittest.main()
