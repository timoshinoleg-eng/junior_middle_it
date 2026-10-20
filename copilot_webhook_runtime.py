"""Register the deterministic Copilot surface on the interactive runtime.

The Telegram glue lives here on purpose: ``copilot/`` stays free of Telegram
imports, so the same scoring logic can be reused by serverless endpoints and by
offline tests.

The surface is opt-in: every entry point checks ``COPILOT_ENABLED`` and falls
back to the normal behaviour when the feature is off.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import channel_bot as core
import durable_product_state as durable
import interactive_webhook_runtime as base
import localized_webhook_runtime as localized
from saved_searches import suggested_search_from_resume
from telegram.ext import CommandHandler

from copilot import flag_enabled
from copilot import enrich as copilot_enrich
from copilot import fit as copilot_fit
from copilot import grade_audit as copilot_grade
from copilot import hr_link as copilot_hr
from copilot import pdf as copilot_pdf
from copilot.credits import CreditManager
from copilot.store import CopilotStore
from resume_documents import MAX_FILE_BYTES, ResumeDocumentError, extract_resume_document

logger = logging.getLogger(__name__)

DEEPLINK_PREFIX = "copilot_"
CALLBACK_PREFIX = "copilot_"
MAX_HASH_CHARS = 40
MAX_ENRICHMENT_CHARS = 3000
CV_FILENAME = "cv.pdf"

_original_cmd_start = durable.JobBot.cmd_start
_original_handle_callback = durable.JobBot.handle_callback
_original_handle_setup_text = durable.JobBot.handle_setup_text
_localized_build_runtime = localized.build_runtime


def copilot_enabled() -> bool:
    """Master switch for every Copilot entry point."""
    return flag_enabled("COPILOT_ENABLED")


def enrichment_enabled() -> bool:
    """Network enrichment is heavier, so it has its own switch."""
    return flag_enabled("COPILOT_ENRICH_ENABLED")


def pdf_enabled() -> bool:
    """PDF export is a paid action, so it has its own switch."""
    return flag_enabled("COPILOT_PDF_ENABLED")


def _job_hash(raw: str) -> str:
    return str(raw or "").strip()[:MAX_HASH_CHARS]


def _profile_from_settings(settings: Optional[Dict]) -> Dict:
    settings = settings or {}
    categories = settings.get("enabled_categories") or []
    if isinstance(categories, str):
        categories = [item for item in categories.split(",") if item]
    return {
        "skills": str(settings.get("skills") or ""),
        "categories": ", ".join(str(item) for item in categories),
    }


def _source_handles(job: Dict) -> List[str]:
    """Handles that belong to the source channel, not to a recruiter."""
    handles: List[str] = []
    for key in ("source", "channel", "channel_username", "source_channel"):
        value = str(job.get(key) or "")
        if "@" in value:
            handles.append(value.split("@", 1)[1].split()[0])
    return handles


def analyse_job(job: Dict, profile: Dict) -> Dict:
    """Run every deterministic check for one vacancy."""
    report = copilot_fit.compute_fit(profile, job)
    audit = copilot_grade.audit_grade(job)
    text = " ".join(
        str(job.get(key) or "")
        for key in ("title", "description", "requirements", "tags")
    )
    handle = copilot_hr.extract_hr_handle(text, ignore=_source_handles(job))
    return {"report": report, "audit": audit, "hr_handle": handle}


def format_analysis(job: Dict, analysis: Dict) -> str:
    """Human readable summary of the deterministic analysis."""
    report = analysis["report"]
    audit = analysis["audit"]
    handle = analysis.get("hr_handle")

    title = str(job.get("title") or "вакансия").strip()
    company = str(job.get("company") or "").strip()
    heading = f"{title} · {company}" if company else title

    lines = ["🧠 Разбор вакансии", heading, ""]
    lines.append(f"🎯 Предварительный fit: {report.score}%")
    if report.matched_skills:
        lines.append("✅ Совпало со стеком: " + ", ".join(report.matched_skills[:6]))
    if report.missing_skills:
        lines.append("➕ Не хватает: " + ", ".join(report.missing_skills[:6]))
    if audit.is_fake_grade:
        lines.append("⚠️ " + audit.badge_text)
    elif audit.declared_grade != copilot_fit.GRADE_UNKNOWN:
        lines.append("ℹ️ " + audit.badge_text)
    for warning in report.warnings[:2]:
        lines.append("• " + warning)
    if handle:
        lines.append(f"👤 Контакт: @{handle}")
    lines.append("")
    lines.append("Оценка эвристическая — это помощник, а не официальный ATS-score.")
    return "\n".join(lines)


def analysis_keyboard(job_hash: str, analysis: Dict, *, can_enrich: bool = False, can_pdf: bool = False):
    """Buttons for the analysis card."""
    rows: List[List] = []
    if analysis.get("hr_handle"):
        rows.append(
            [
                core.InlineKeyboardButton(
                    "✉️ Написать HR",
                    callback_data=f"copilot_hr:{job_hash}",
                )
            ]
        )
    rows.append(
        [
            core.InlineKeyboardButton(
                "💾 Сохранить такой поиск",
                callback_data=f"copilot_save:{job_hash}",
            ),
            core.InlineKeyboardButton(
                "📄 Проверить резюме",
                callback_data=f"resume_match:{job_hash}",
            ),
        ]
    )
    if can_pdf:
        rows.append(
            [
                core.InlineKeyboardButton(
                    "🧾 Собрать CV под вакансию",
                    callback_data=f"copilot_pdf:{job_hash}",
                )
            ]
        )
    if can_enrich:
        rows.append(
            [
                core.InlineKeyboardButton(
                    "🔍 Полное описание",
                    callback_data=f"copilot_enrich:{job_hash}",
                )
            ]
        )
    return core.InlineKeyboardMarkup(rows)


def _persist_analysis(job_bot, user_id: int, job_hash: str, job: Dict, profile: Dict, analysis: Dict) -> None:
    """Cache the analysis; a failing cache must never break the answer."""
    store = CopilotStore.from_database(job_bot.db)
    if store is None:
        return
    report = analysis["report"]
    try:
        store.save_profile(
            user_id,
            grade="",
            skills=str(profile.get("skills") or ""),
            source="settings",
        )
        store.save_evaluation(
            user_id,
            job_hash,
            fit_score=report.score,
            stack_match_score=report.stack_match_score,
            keyword_overlap_pct=report.keyword_overlap_pct,
            matched_keywords=report.matched_skills,
            missing_skills=report.missing_skills,
            llm_used=False,
            model="",
        )
        store.save_grade_audit(job_hash, analysis["audit"])
    except Exception as exc:
        logger.info("Copilot cache write failed: %s", type(exc).__name__)


async def _send_analysis(job_bot, update, job_hash: str) -> None:
    """Build and deliver the analysis card for one vacancy."""
    user_id = update.effective_user.id if update.effective_user else None
    job = job_bot.db.get_job_payload(job_hash)
    if not job:
        await update.message.reply_text(
            "Карточка вакансии уже устарела. Открой свежую вакансию в канале и снова нажми «Разбор вакансии»."
        )
        return

    settings = job_bot.db.get_user_settings(user_id) if user_id else {}
    profile = _profile_from_settings(settings)
    analysis = analyse_job(job, profile)
    can_enrich = bool(enrichment_enabled() and str(job.get("url") or "").startswith("http"))
    can_pdf = bool(pdf_enabled() and analysis["report"].matched_skills)

    await update.message.reply_text(
        format_analysis(job, analysis),
        reply_markup=analysis_keyboard(
            job_hash,
            analysis,
            can_enrich=can_enrich,
            can_pdf=can_pdf,
        ),
        disable_web_page_preview=True,
    )
    job_bot.db.log_event(
        user_id,
        "copilot_fit_viewed",
        {
            "hash": job_hash,
            "score": analysis["report"].score,
            "grade": analysis["audit"].declared_grade,
            "has_hr": bool(analysis.get("hr_handle")),
        },
    )
    if user_id:
        _persist_analysis(job_bot, user_id, job_hash, job, profile, analysis)


async def _cmd_copilot(self, update, context):
    """``/copilot`` and ``/copilot <job hash>``."""
    if not copilot_enabled():
        await update.message.reply_text("Разбор вакансий пока выключен.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Открой вакансию в канале и нажми «✨ Разбор вакансии» — "
            "здесь я покажу совпадение со стеком, реальный уровень вакансии и контакт HR."
        )
        return
    await _send_analysis(self, update, _job_hash(args[0]))


async def _copilot_cmd_start(self, update, context):
    """Handle the ``/start copilot_<hash>`` deep link."""
    payload = context.args[0] if context.args else None
    if payload and str(payload).startswith(DEEPLINK_PREFIX) and copilot_enabled():
        job_hash = _job_hash(str(payload)[len(DEEPLINK_PREFIX):])
        if job_hash:
            await _send_analysis(self, update, job_hash)
            return
    return await _original_cmd_start(self, update, context)


async def _handle_hr(job_bot, update, job_hash: str) -> None:
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    job = job_bot.db.get_job_payload(job_hash)
    if not job:
        await query.answer("Карточка вакансии устарела.", show_alert=True)
        return
    settings = job_bot.db.get_user_settings(user_id) if user_id else {}
    analysis = analyse_job(job, _profile_from_settings(settings))
    handle = analysis.get("hr_handle")
    if not handle:
        await query.answer("Контакт не найден в тексте вакансии.", show_alert=True)
        return
    message = copilot_hr.build_contact_message(job, analysis["report"])
    link = copilot_hr.build_hr_link(handle, message)
    await query.answer()
    await query.message.reply_text(
        f"Отправь сообщение рекрутеру:\n{link}",
        disable_web_page_preview=True,
    )
    job_bot.db.log_event(user_id, "copilot_hr_link_clicked", {"hash": job_hash})


async def _handle_save(job_bot, update, job_hash: str) -> None:
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        await query.answer("Не удалось определить пользователя.", show_alert=True)
        return
    job = job_bot.db.get_job_payload(job_hash)
    if not job:
        await query.answer("Карточка вакансии устарела.", show_alert=True)
        return
    settings = job_bot.db.get_user_settings(user_id)
    analysis = analyse_job(job, _profile_from_settings(settings))
    spec = suggested_search_from_resume(job, analysis["report"].matched_skills)
    search = job_bot.db.create_saved_search(user_id, spec)
    job_bot.db.save_user_settings(user_id, {"alerts_enabled": True})
    job_bot.db.log_event(
        user_id,
        "copilot_search_saved",
        {"search_id": getattr(search, "id", 0), "source": "copilot"},
    )
    await query.answer("Поиск сохранён, алерты включены", show_alert=True)


async def _handle_enrich(job_bot, update, job_hash: str) -> None:
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not enrichment_enabled():
        await query.answer("Загрузка описания выключена.", show_alert=True)
        return
    job = job_bot.db.get_job_payload(job_hash)
    if not job:
        await query.answer("Карточка вакансии устарела.", show_alert=True)
        return
    url = str(job.get("url") or "")
    await query.answer("Загружаю описание…")
    enrichment = await copilot_enrich.enrich_job(url)
    if not enrichment:
        await query.message.reply_text(
            "Не удалось загрузить полное описание — возможно, страница закрыта или запрещена в robots.txt."
        )
        return
    store = CopilotStore.from_database(job_bot.db)
    if store is not None:
        try:
            store.save_enrichment(job_hash, enrichment.url, enrichment.text)
        except Exception as exc:
            logger.info("Copilot enrichment cache failed: %s", type(exc).__name__)
    await query.message.reply_text(
        enrichment.text[:MAX_ENRICHMENT_CHARS],
        disable_web_page_preview=True,
    )
    job_bot.db.log_event(user_id, "copilot_enrichment_viewed", {"hash": job_hash})


async def _handle_pdf(job_bot, update, job_hash: str) -> None:
    """Send the cached CV or ask for the resume that is needed to build one."""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        await query.answer("Не удалось определить пользователя.", show_alert=True)
        return
    if not pdf_enabled():
        await query.answer("Сборка CV выключена.", show_alert=True)
        return

    store = CopilotStore.from_database(job_bot.db)
    if store is not None:
        try:
            cached = store.get_document(user_id, job_hash)
        except Exception as exc:
            logger.info("Copilot document cache read failed: %s", type(exc).__name__)
            cached = None
        if cached and cached.get("telegram_file_id"):
            await query.answer("Отправляю сохранённый файл")
            try:
                await update.callback_query.message.bot.send_document(
                    chat_id=user_id,
                    document=str(cached["telegram_file_id"]),
                    caption="🧾 Твоё CV под эту вакансию (сохранённая версия).",
                )
            except Exception:
                pass
            job_bot.db.log_event(user_id, "copilot_cv_cached", {"hash": job_hash})
            return

    job = job_bot.db.get_job_payload(job_hash)
    if not job:
        await query.answer("Карточка вакансии устарела.", show_alert=True)
        return

    requests = _pdf_requests(job_bot)
    requests[user_id] = {"job_hash": job_hash, "job": job}
    await query.answer()
    await query.message.reply_text(
        "Пришли резюме файлом (PDF или DOCX) — соберу одностраничное CV под эту вакансию.\n\n"
        "Файл не сохраняется: я читаю его в памяти и удаляю сразу после сборки."
    )


def _pdf_requests(job_bot) -> Dict:
    """Per-request state of pending CV builds."""
    requests = getattr(job_bot, "copilot_pdf_requests", None)
    if requests is None:
        requests = {}
        setattr(job_bot, "copilot_pdf_requests", requests)
    return requests


def _spend_pdf_credit(job_bot, user_id: int, job_hash: str) -> bool:
    """Consume one credit for a CV build; free builds use the daily allowance."""
    store = CopilotStore.from_database(job_bot.db)
    if store is None:
        return True
    try:
        manager = CreditManager(store)
        manager.ensure_daily_allowance(user_id)
        if manager.spend(user_id, 1, "spend", "cv"):
            return True
    except Exception as exc:
        logger.info("Copilot credit handling failed: %s", type(exc).__name__)
        return True
    return False


async def _build_cv_from_message(job_bot, update, context, pending: Dict) -> None:
    """Build the tailored CV from the resume the user just sent."""
    user_id = update.effective_user.id if update.effective_user else None
    message = update.message
    document = getattr(message, "document", None) if message else None
    if not user_id:
        return

    resume_text = ""
    resume_bytes = b""
    if document is not None:
        if document.file_size and int(document.file_size) > MAX_FILE_BYTES:
            await message.reply_text("Файл слишком большой: максимум 5 МБ.")
            return
        try:
            tg_file = await context.bot.get_file(document.file_id)
            payload = await tg_file.download_as_bytearray()
            parsed = extract_resume_document(
                payload,
                filename=document.file_name or "",
                mime_type=document.mime_type or "",
            )
            resume_text = parsed.text
            if (document.mime_type or "").lower() == "application/pdf":
                resume_bytes = bytes(payload)
        except ResumeDocumentError as exc:
            await message.reply_text(str(exc))
            return
        except Exception as exc:
            logger.warning("CV source processing failed: %s", type(exc).__name__)
            await message.reply_text("Не удалось прочитать файл. Попробуй PDF или DOCX.")
            return
    else:
        resume_text = (getattr(message, "text", "") or "").strip()
        if len(resume_text) < 20:
            await message.reply_text("Пришли резюме файлом (PDF или DOCX) или текстом — соберу CV.")
            return

    job = pending["job"]
    job_hash = pending["job_hash"]
    settings = job_bot.db.get_user_settings(user_id)
    profile = _profile_from_settings(settings)
    analysis = analyse_job(job, profile)
    contact = copilot_pdf.extract_contacts(resume_text)

    if not _spend_pdf_credit(job_bot, user_id, job_hash):
        await message.reply_text(
            "Бесплатные сборки на сегодня закончились. Пригласи друга — он получит бонус, а ты — +5 кредитов."
        )
        return

    try:
        pdf_bytes = await _run_in_thread(
            copilot_pdf.build_cv_pdf,
            contact=contact,
            role=str(job.get("title") or "вакансия"),
            company=str(job.get("company") or ""),
            skills=analysis["report"].matched_skills,
            note="Собрано под эту вакансию: совпавшие навыки из твоего профиля.",
        )
    except Exception as exc:
        logger.warning("CV build failed: %s", type(exc).__name__)
        await message.reply_text("Не удалось собрать CV. Попробуй текстом резюме или позже.")
        return

    report = await _run_in_thread(copilot_pdf.validate_pdf_bytes, pdf_bytes, job=job)
    if resume_bytes:
        source_report = await _run_in_thread(copilot_pdf.validate_pdf_bytes, resume_bytes, job=job)
    else:
        source_report = None

    caption = "🧾 CV под вакансию «{}».".format(str(job.get("title") or "вакансия"))
    caption += "\n" + report.summary()
    if source_report is not None and source_report.problems:
        caption += "\nТвой исходный файл: " + "; ".join(source_report.problems[:2])

    try:
        sent = await context.bot.send_document(
            chat_id=user_id,
            document=pdf_bytes,
            filename=CV_FILENAME,
            caption=caption,
        )
        file_id = str(getattr(getattr(sent, "document", None), "file_id", "") or "")
    except Exception as exc:
        logger.warning("CV delivery failed: %s", type(exc).__name__)
        await message.reply_text("Не удалось отправить файл. Попробуй позже.")
        return

    store = CopilotStore.from_database(job_bot.db)
    if store is not None and file_id:
        try:
            store.save_document(
                user_id,
                job_hash,
                telegram_file_id=file_id,
                filename=CV_FILENAME,
                fit_score=analysis["report"].score,
            )
        except Exception as exc:
            logger.info("Copilot document cache write failed: %s", type(exc).__name__)

    job_bot.db.log_event(
        user_id,
        "copilot_cv_built",
        {"hash": job_hash, "pages": report.pages, "problems": len(report.problems)},
    )
    await message.reply_text("Готово. Файл выше — CV под эту вакансию.")


async def _run_in_thread(func, *args, **kwargs):
    """Run CPU-bound work outside the event loop."""
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)


async def _copilot_handle_setup_text(self, update, context):
    """Route a resume upload to the pending CV build when one is waiting."""
    user_id = update.effective_user.id if update.effective_user else None
    requests = _pdf_requests(self)
    pending = requests.pop(user_id, None) if user_id else None
    if pending:
        await _build_cv_from_message(self, update, context, pending)
        return
    return await _original_handle_setup_text(self, update, context)


async def _copilot_handle_callback(self, update, context):
    """Route Copilot callbacks, everything else keeps the existing behaviour."""
    query = update.callback_query
    data = (query.data or "") if query else ""
    if not data.startswith(CALLBACK_PREFIX):
        return await _original_handle_callback(self, update, context)

    action, _, job_hash = data.partition(":")
    if action == "copilot_hr":
        await _handle_hr(self, update, job_hash)
        return
    if action == "copilot_save":
        await _handle_save(self, update, job_hash)
        return
    if action == "copilot_enrich":
        await _handle_enrich(self, update, job_hash)
        return
    if action == "copilot_pdf":
        await _handle_pdf(self, update, job_hash)
        return
    if query:
        await query.answer()
    return None


def _copilot_build_runtime():
    """Build the runtime with the Copilot command registered."""
    application, db, job_bot = _localized_build_runtime()
    application.add_handler(CommandHandler("copilot", job_bot.cmd_copilot))
    return application, db, job_bot


durable.JobBot.cmd_start = _copilot_cmd_start
durable.JobBot.cmd_copilot = _cmd_copilot
durable.JobBot.handle_callback = _copilot_handle_callback
durable.JobBot.handle_setup_text = _copilot_handle_setup_text
base.build_runtime = _copilot_build_runtime

MAX_UPDATE_BYTES = base.MAX_UPDATE_BYTES
process_update_payload = base.process_update_payload
webhook_secret_valid = base.webhook_secret_valid
build_runtime = base.build_runtime
