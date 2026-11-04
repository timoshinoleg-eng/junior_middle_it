"""Saved-resume profile flow.

The user can save their own resume text (opt-in, explicit) so every vacancy
analysis reuses it without re-uploading the document. Integrated into the
webhook runtime with the same monkey-patch style as ``onboarding_flow``:

- unmatched resume text / resume documents land in ``save_and_report()``
  instead of the old "choose a vacancy first" refusal;
- parsed skills are written into the durable settings profile, so the
  existing ``compute_fit``-based analyses pick them up automatically;
- ``/resume`` shows the saved profile and offers deletion.

Privacy contract: the resume is stored only in the user's own row of
``growth_copilot_resumes``, used solely for fit/match analysis, and is
removed completely on delete. Nothing is sent to third parties.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import channel_bot as core
from copilot.store import CopilotStore
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

CB_RESUME_DELETE = "obn:resume_delete"

MAX_RESUME_BYTES = 5 * 1024 * 1024
MIN_RESUME_TEXT_LEN = 80

_RESUME_SOURCE = "resume_saved"

_SKILL_CATEGORY_HINTS = {
    "development": ("python", "javascript", "java ", "golang", "backend", "frontend", "react", "django", "sql", "c++", "php", "swift", "kotlin", ".net"),
    "qa": ("тестиров", "qa", "autotest", "pytest", "selenium", "test cases", "тест-кейс"),
    "devops": ("devops", "kubernetes", "docker", "ansible", "terraform", "ci/cd", "jenkins"),
    "data": ("data ", "аналитик", "ml", "machine learning", "pandas", "numpy", "etl", "bi ", "power bi", "tableau", "airflow"),
    "pm": ("продакт", "проджект", "product manager", "project manager", "scrum", "jira", "roadmap"),
    "design": ("figma", "ux", "ui", "дизайн", "photoshop", "illustrator"),
    "security": ("security", "пентест", "siem", "osint", "кибербез"),
    "marketing": ("seo", "контент", "таргет", "performance", "маркетинг"),
    "sales": ("продаж", "b2b", "crm", "лиды", "sdR"),
    "support": ("поддержк", "helpdesk", "sla", "клиентский сервис"),
}


def detect_resume_grade(resume_text: str, skills: List[str]) -> str:
    """Keyword-first, years-of-experience fallback grade detection."""
    sample = str(resume_text or "").lower()
    if "senior" in sample or "сеньор" in sample or "lead" in sample:
        return "Senior"
    if "middle" in sample:
        return "Middle"
    if "junior" in sample:
        return "Junior"
    try:
        from copilot.fit import detect_years_range

        low, high = detect_years_range(resume_text)
        years = high if high is not None else low
        if years is None and "месяц" in sample:
            return "Junior"
        if years is not None:
            if years >= 6:
                return "Senior"
            if years >= 3:
                return "Middle"
            if years >= 1:
                return "Junior+"
            return "Junior"
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("detect_years_range failed: %s", type(exc).__name__)
    return "Junior / Middle"


def detect_category(resume_text: str, skills: List[str]) -> str:
    sample = " ".join([str(resume_text or "").lower(), " ".join(skills).lower()])
    for category, hints in _SKILL_CATEGORY_HINTS.items():
        for hint in hints:
            if hint in sample:
                return category
    return "other"


def build_saved_search(resume_text: str, skills: List[str]) -> Dict[str, Any]:
    """Suggested saved-search filter via the real ``suggested_search_from_resume``."""
    from saved_searches import suggested_search_from_resume

    pseudo_job = {
        "category": detect_category(resume_text, skills),
        "title": "Похожие на моё резюме",
    }
    try:
        return dict(suggested_search_from_resume(pseudo_job, list(skills)[:5]) or {})
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("suggested_search_from_resume failed: %s", type(exc).__name__)
        return {"name": "Мой поиск", "categories": ["other"], "skills": ", ".join(skills[:5])}


def parse_resume(resume_text: str) -> Dict[str, Any]:
    """Extract skills / grade / suggested search from raw resume text."""
    from resume_matcher import extract_skills

    skills = sorted(extract_skills(resume_text))
    return {
        "skills": skills,
        "skills_str": ", ".join(skills),
        "grade": detect_resume_grade(resume_text, skills),
        "category": detect_category(resume_text, skills),
        "search": build_saved_search(resume_text, skills),
    }


def format_saved_report(parsed: Dict[str, Any]) -> str:
    skills = parsed.get("skills") or []
    skills_badge = ", ".join(str(s) for s in skills[:8]) if skills else "—"
    search = parsed.get("search") or {}
    search_name = search.get("name") or "Мой поиск"
    lines = [
        "✅ <b>Резюме сохранено в твоём профиле Копилота.</b>",
        "",
        f"📊 <b>Определённый уровень:</b> <b>{parsed.get('grade') or '—'}</b>",
        f"🛠 <b>Распознанный стек:</b> <code>{skills_badge}</code>",
        "",
        f"🔔 <b>Рекомендованный поиск:</b> «{search_name}».",
        "",
        "Теперь разбор любой вакансии из канала автоматически учитывает "
        "твой стек. Обновить резюме можно в любой момент — просто пришли "
        "его ещё раз файлом или текстом.",
    ]
    return "\n".join(lines)


def _report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Удалить сохранённое резюме", callback_data=CB_RESUME_DELETE),
    ]])


def _store(job_bot) -> Optional[CopilotStore]:
    return CopilotStore.from_database(job_bot.db)


async def save_and_report(job_bot, update, resume_text: str) -> bool:
    """Persist the resume and reply with the parsed profile. False — not saved."""
    message = getattr(update, "message", None)
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    if message is None or not user_id:
        return False
    resume_text = str(resume_text or "").strip()
    if len(resume_text) < MIN_RESUME_TEXT_LEN:
        return False
    store = _store(job_bot)
    if store is None:
        await message.reply_text(
            "Сохранение резюме сейчас недоступно (база профилей не подключена). "
            "Попробуй позже или используй «Проверить резюме» под конкретной вакансией."
        )
        return True
    try:
        parsed = parse_resume(resume_text)
        store.save_resume(
            int(user_id),
            resume_text,
            skills=parsed["skills_str"],
            grade=parsed["grade"],
        )
        store.save_profile(
            int(user_id),
            grade=parsed["grade"],
            skills=parsed["skills_str"],
            source=_RESUME_SOURCE,
        )
        # Enrich the durable settings profile so compute_fit-based analyses
        # (vacancy cards, HR pitch, upskill) reuse the saved stack.
        job_bot.db.save_user_settings(int(user_id), {"skills": parsed["skills_str"]})
        job_bot.db.log_event(int(user_id), "resume_saved", {
            "grade": parsed["grade"],
            "category": parsed["category"],
            "skills_count": len(parsed["skills"]),
        })
    except Exception as exc:  # pragma: no cover - defensive, store failures logged
        logger.info("resume save failed: %s", type(exc).__name__)
        await message.reply_text("Не получилось сохранить резюме — попробуй ещё раз чуть позже.")
        return True
    await message.reply_text(format_saved_report(parsed), parse_mode="HTML", reply_markup=_report_keyboard())
    return True


async def handle_document(job_bot, update, context) -> bool:
    """Save an unmatched resume document (no active CV build). True — handled."""
    message = getattr(update, "message", None)
    document = getattr(message, "document", None) if message else None
    if document is None or getattr(message, "caption", None):
        return False
    if getattr(message, "forward_date", None):
        return False
    file_id = getattr(document, "file_id", "")
    if not file_id:
        return False
    file_size = int(getattr(document, "file_size", 0) or 0)
    if file_size > MAX_RESUME_BYTES:
        return False
    try:
        tg_file = await context.bot.get_file(file_id)
        payload = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        logger.info("resume document download failed: %s", type(exc).__name__)
        return False
    try:
        from resume_documents import extract_resume_document

        parsed = extract_resume_document(
            payload,
            filename=getattr(document, "file_name", "") or "",
            mime_type=getattr(document, "mime_type", "") or "",
        )
        resume_text = str(getattr(parsed, "text", "") or "").strip()
    except Exception as exc:
        logger.info("extract_resume_document failed: %s", type(exc).__name__)
        resume_text = ""
    if len(resume_text) < MIN_RESUME_TEXT_LEN:
        await message.reply_text(
            "⚠️ Не удалось извлечь текст из документа (похоже на скан-картинку). "
            "Скопируй и пришли текст резюме сообщением."
        )
        return True
    return await save_and_report(job_bot, update, resume_text)


async def cmd_resume(job_bot, update, context) -> None:
    """``/resume`` — show the saved resume profile or explain how to save one."""
    message = getattr(update, "message", None)
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    if message is None or not user_id:
        return
    store = _store(job_bot)
    row = store.get_resume(int(user_id)) if store is not None else None
    if not row or not str(row.get("resume_text") or "").strip():
        await message.reply_text(
            "📄 <b>Сохранённого резюме пока нет.</b>\n\n"
            "Пришли резюме файлом (PDF/DOCX с выделяемым текстом) или вставь "
            "текст сообщением — я сохраню профиль и буду учитывать твой стек "
            "в разборах вакансий.",
            parse_mode="HTML",
        )
        return
    skills = str(row.get("skills") or "") or "—"
    updated_at = row.get("updated_at")
    stamp = updated_at.strftime("%d.%m.%Y") if hasattr(updated_at, "strftime") else str(updated_at or "")
    await message.reply_text(
        "📄 <b>Сохранённое резюме</b>\n\n"
        f"📊 Уровень: <b>{row.get('grade') or '—'}</b>\n"
        f"🛠 Стек: <code>{skills[:400]}</code>\n"
        f"🕐 Обновлено: {stamp}\n\n"
        "Чтобы обновить — просто пришли новое резюме файлом или текстом.",
        parse_mode="HTML",
        reply_markup=_report_keyboard(),
    )


async def handle_callback_extras(job_bot, update, context) -> bool:
    """``obn:resume_*`` callbacks. True — handled."""
    query = getattr(update, "callback_query", None)
    data = str(getattr(query, "data", "") or "") if query else ""
    if data != CB_RESUME_DELETE:
        return False
    if query is not None and hasattr(query, "answer"):
        try:
            await query.answer()
        except Exception:  # pragma: no cover - acknowledge best effort
            pass
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    message = getattr(query, "message", None) if query else None
    if not user_id:
        return True
    store = _store(job_bot)
    removed = bool(store is not None and store.delete_resume(int(user_id)))
    if store is not None:
        try:
            store.save_profile(int(user_id), grade="", skills="", source="resume_deleted")
        except Exception as exc:  # pragma: no cover
            logger.debug("profile clear after resume delete: %s", type(exc).__name__)
    if removed:
        job_bot.db.log_event(int(user_id), "resume_deleted", {})
    if message is not None and hasattr(message, "reply_text"):
        await message.reply_text("🗑 Сохранённое резюме удалено. Пришли новое в любой момент.")
    return True


def register_handlers(application, job_bot) -> None:
    from telegram.ext import CommandHandler

    async def _on_resume(update, context):
        await cmd_resume(job_bot, update, context)

    application.add_handler(CommandHandler("resume", _on_resume))
