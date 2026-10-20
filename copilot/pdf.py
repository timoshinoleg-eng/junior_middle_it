"""Tailored CV export and ATS validation for the Copilot flow.

Two independent jobs live here:

* ``validate_pdf_bytes`` inspects a resume the user already sent and reports
  what an applicant tracking system will see: page count, a readable text
  layer, broken glyphs, contacts and keyword coverage.
* ``build_cv_pdf`` renders a short tailored one-pager from the profile and the
  match result. The resume text is never persisted: it stays in memory for the
  lifetime of the request and only the resulting Telegram file id is cached.
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from resume_matcher import extract_skills

logger = logging.getLogger(__name__)

MAX_PAGES = 2
MIN_TEXT_LAYER_CHARS = 40
FONT_CANDIDATES = ("DejaVuSans.ttf", "DejaVuSansCondensed.ttf")
MAX_SKILLS = 12

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?|\d{2,4}[\s-])\d{3}[\s-]?\d{2}[\s-]?\d{2}")
_TELEGRAM_LINK_RE = re.compile(r"(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})\b")
_TELEGRAM_AT_RE = re.compile(r"(?<![\w.])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
_GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9-]{2,39})")
_BROKEN_GLYPH_RE = re.compile(r"cid:\d+|�")


@dataclass(frozen=True)
class AtsReport:
    """What an applicant tracking system will actually read."""

    pages: int
    has_text_layer: bool
    broken_glyphs: bool
    contacts: Dict[str, str] = field(default_factory=dict)
    keyword_coverage_pct: int = 0
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        """Short, honest summary for the chat."""
        if self.ok:
            return "✅ ATS-проверка: всё в порядке."
        return "⚠️ ATS-проверка: " + "; ".join(self.problems[:3])


@dataclass(frozen=True)
class CvContact:
    name: str = ""
    email: str = ""
    phone: str = ""
    telegram: str = ""
    github: str = ""


def extract_contacts(text: str) -> CvContact:
    """Pick contacts out of resume text without storing the text itself."""
    source = str(text or "")
    email = ""
    phone = ""
    telegram = ""
    github = ""

    match = _EMAIL_RE.search(source)
    if match:
        email = match.group(0)
    phone_match = _PHONE_RE.search(source)
    if phone_match:
        phone = phone_match.group(0).strip()
    for pattern in (_TELEGRAM_LINK_RE, _TELEGRAM_AT_RE):
        for match in pattern.finditer(source):
            candidate = match.group(1)
            if candidate.lower() not in {"github", "gitlab", "telegram"}:
                telegram = candidate
                break
        if telegram:
            break
    match = _GITHUB_RE.search(source)
    if match:
        github = match.group(1)

    name = ""
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line or len(line) > 60:
            continue
        if "@" in line or "http" in line or line.lower() in {"resume", "cv", "резюме"}:
            continue
        if re.fullmatch(r"[\d\s()+-]+", line):
            continue
        if len(line.split()) > 6:
            continue
        name = line
        break

    return CvContact(name=name, email=email, phone=phone, telegram=telegram, github=github)


def validate_pdf_bytes(data: bytes, *, job: Optional[Dict] = None) -> AtsReport:
    """Report what an ATS will see in a resume PDF."""
    problems: List[str] = []
    pages = 0
    text = ""

    try:
        from pypdf import PdfReader

        pypdf_logger = logging.getLogger("pypdf")
        previous_level = pypdf_logger.level
        pypdf_logger.setLevel(logging.CRITICAL)
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = len(reader.pages)
            chunks = []
            for page in reader.pages:
                try:
                    chunks.append(page.extract_text() or "")
                except Exception:
                    chunks.append("")
            text = "\n".join(chunks)
        finally:
            pypdf_logger.setLevel(previous_level)
    except Exception:
        return AtsReport(
            pages=0,
            has_text_layer=False,
            broken_glyphs=False,
            problems=["Не удалось прочитать PDF — файл повреждён или это не PDF."],
        )

    has_text_layer = len(text.strip()) >= MIN_TEXT_LAYER_CHARS
    broken_glyphs = bool(_BROKEN_GLYPH_RE.search(text))

    if pages == 0:
        problems.append("В PDF нет страниц.")
    elif pages > MAX_PAGES:
        problems.append(f"{pages} страниц — для junior/middle лучше уложиться в {MAX_PAGES}.")
    if not has_text_layer:
        problems.append("Нет текстового слоя — ATS прочитает пустоту (похоже на скан).")
    if broken_glyphs:
        problems.append("Есть битые глифы (cid:*) — часть текста не извлекается.")

    contacts = extract_contacts(text)
    found = [value for value in (contacts.email, contacts.phone, contacts.telegram, contacts.github) if value]
    if not found:
        problems.append("Не найдено ни одного контакта (почта, телефон, Telegram или GitHub).")

    coverage = 0
    if job:
        job_text = " ".join(
            str(job.get(key) or "") for key in ("title", "description", "requirements", "tags")
        )
        required = extract_skills(job_text)
        if required:
            present = extract_skills(text)
            coverage = int(round(100 * len(required & present) / len(required)))
            if coverage < 40:
                problems.append(f"Покрытие ключевых слов вакансии — {coverage}%.")

    return AtsReport(
        pages=pages,
        has_text_layer=has_text_layer,
        broken_glyphs=broken_glyphs,
        contacts={
            key: value
            for key, value in (
                ("email", contacts.email),
                ("phone", contacts.phone),
                ("telegram", contacts.telegram),
                ("github", contacts.github),
            )
            if value
        },
        keyword_coverage_pct=coverage,
        problems=problems,
    )


def _font_path() -> Optional[str]:
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    for name in FONT_CANDIDATES:
        candidate = os.path.join(fonts_dir, name)
        if os.path.exists(candidate):
            return candidate
    return None


def build_cv_pdf(
    *,
    contact: CvContact,
    role: str,
    company: str = "",
    skills: Optional[List[str]] = None,
    note: str = "",
) -> bytes:
    """Render a short tailored one-pager for the vacancy."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    font_path = _font_path()
    if font_path:
        pdf.add_font("cv", "", font_path)
        pdf.set_font("cv", size=11)
    else:
        # Latin-only fallback: the layout still works, non-Latin text is dropped.
        pdf.set_font("Helvetica", size=11)

    pdf.add_page()
    if contact.name:
        pdf.set_font_size(16)
        pdf.cell(0, 9, contact.name, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font_size(11)

    contacts = [value for value in (contact.email, contact.phone, contact.telegram, contact.github) if value]
    if contacts:
        pdf.cell(0, 6, " · ".join(contacts), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    target = role if not company else f"{role} @ {company}"
    pdf.set_font_size(12)
    pdf.cell(0, 7, f"Отклик на вакансию: {target}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font_size(11)
    pdf.ln(2)

    skill_list = [str(item) for item in (skills or []) if str(item).strip()][:MAX_SKILLS]
    if skill_list:
        pdf.cell(0, 7, "Стек:", new_x="LMARGIN", new_y="NEXT")
        pdf.multi_cell(0, 6, ", ".join(skill_list))
        pdf.ln(2)

    if note:
        pdf.multi_cell(0, 6, note)
        pdf.ln(2)

    pdf.set_font_size(9)
    pdf.multi_cell(
        0,
        5,
        "Документ собран автоматически из профиля и совпадения с вакансией. "
        "Полное резюме и проекты — в исходном файле.",
    )

    output = pdf.output(dest="S")
    return bytes(output)
