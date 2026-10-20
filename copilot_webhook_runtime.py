"""Register the deterministic Copilot surface on the interactive runtime.

The Telegram glue lives here on purpose: ``copilot/`` stays free of Telegram
imports, so the same scoring logic can be reused by serverless endpoints and by
offline tests.

The surface is opt-in: every entry point checks ``COPILOT_ENABLED`` and falls
back to the normal behaviour when the feature is off.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import quote

import channel_bot as core
import durable_product_state as durable
import interactive_webhook_runtime as base
import localized_webhook_runtime as localized
from saved_searches import suggested_search_from_resume
from telegram import WebAppInfo
from telegram.ext import CommandHandler

from copilot import flag_enabled
from copilot import enrich as copilot_enrich
from copilot import fit as copilot_fit
from copilot import forward as copilot_forward
from copilot import grade_audit as copilot_grade
from copilot import hr_link as copilot_hr
from copilot import llm as copilot_llm
from copilot import metrics as copilot_metrics
from copilot import outcome as copilot_outcome
from copilot import pdf as copilot_pdf
from copilot import upskill as copilot_upskill
from copilot.credits import REASON_LLM_CALL, CreditManager
from copilot.store import CopilotStore
from resume_documents import MAX_FILE_BYTES, ResumeDocumentError, extract_resume_document

logger = logging.getLogger(__name__)

DEEPLINK_PREFIX = "copilot_"
CALLBACK_PREFIX = "copilot_"
UPSKILL_PREFIX = "upskill_"
MAX_HASH_CHARS = 40
MAX_ENRICHMENT_CHARS = 3000
CV_FILENAME = "cv.pdf"

_original_cmd_start = durable.JobBot.cmd_start
_original_cmd_stats_growth = durable.JobBot.cmd_stats_growth
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


def ai_enabled() -> bool:
    """The AI layer is off unless it is switched on and a provider is set."""
    return copilot_llm.llm_ready()


def forward_enabled() -> bool:
    """Forward-to-Analyze is a new interception, so it has its own switch."""
    return flag_enabled("COPILOT_FORWARD_ENABLED")


def outcome_enabled() -> bool:
    """The application tracker writes to the database, so it has its own switch."""
    return flag_enabled("COPILOT_OUTCOME_ENABLED")


def upskill_enabled() -> bool:
    """The upskill radar reads the vacancy feed, so it has its own switch."""
    return flag_enabled("COPILOT_UPSKILL_ENABLED")


def webapp_enabled() -> bool:
    """The Mini App needs a registered public URL, so it has its own switch."""
    return flag_enabled("COPILOT_WEBAPP_ENABLED")


def webapp_url(job_hash: str = "") -> str:
    """Public URL of the Mini App, empty while it is not registered."""
    if not webapp_enabled():
        return ""
    base = str(os.getenv("COPILOT_WEBAPP_URL") or "").strip()
    if not base or not base.startswith("https://"):
        return ""
    if not job_hash:
        return base
    return base + ("&" if "?" in base else "?") + "job=" + quote(str(job_hash), safe="")


def followup_days() -> int:
    """How many days pass before the tracker asks about the result."""
    from copilot import flag_int

    return flag_int("COPILOT_FOLLOWUP_DAYS", 7, minimum=1, maximum=30)


def upskill_days() -> int:
    """Window of the vacancy feed the radar looks at."""
    from copilot import flag_int

    return flag_int("COPILOT_UPSKILL_DAYS", 30, minimum=1, maximum=45)


def _load_job(job_bot, job_hash: str) -> Optional[Dict]:
    """Resolve a vacancy from the channel payloads or from the forward cache."""
    job = job_bot.db.get_job_payload(job_hash)
    if job:
        return job
    store = CopilotStore.from_database(job_bot.db)
    if store is None:
        return None
    try:
        row = store.get_forwarded_job(job_hash)
    except Exception as exc:
        logger.info("Copilot forwarded job read failed: %s", type(exc).__name__)
        return None
    if not row:
        return None
    return copilot_forward.job_payload_from_row(row)


def _ai_cache_hours() -> int:
    """How long a cached AI answer stays reusable."""
    from copilot import flag_int

    return flag_int("COPILOT_LLM_CACHE_HOURS", 12, minimum=0, maximum=168)


def _ai_cache_fresh(created_at) -> bool:
    """Whether a stored AI answer may be shown again without a new call."""
    hours = _ai_cache_hours()
    if not hours or created_at is None:
        return False
    try:
        stamp = created_at
        if getattr(stamp, "tzinfo", None) is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - stamp < timedelta(hours=hours)
    except Exception:
        return False


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


def analysis_keyboard(
    job_hash: str,
    analysis: Dict,
    *,
    can_enrich: bool = False,
    can_pdf: bool = False,
    can_ai: bool = False,
    can_outcome: bool = False,
):
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
    if can_ai:
        rows.append(
            [
                core.InlineKeyboardButton(
                    "🤖 Разобрать глубже (AI)",
                    callback_data=f"copilot_ai:{job_hash}",
                )
            ]
        )
    if can_outcome:
        rows.append(
            [
                core.InlineKeyboardButton(
                    "✉️ Отклик отправлен",
                    callback_data=f"copilot_applied:{job_hash}",
                )
            ]
        )
    mini_app = webapp_url(job_hash)
    if mini_app:
        rows.append(
            [
                core.InlineKeyboardButton(
                    "🖥 Открыть Mini App",
                    web_app=WebAppInfo(url=mini_app),
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
    job = _load_job(job_bot, job_hash)
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
    can_ai = bool(ai_enabled())
    can_outcome = bool(outcome_enabled())

    await update.message.reply_text(
        format_analysis(job, analysis),
        reply_markup=analysis_keyboard(
            job_hash,
            analysis,
            can_enrich=can_enrich,
            can_pdf=can_pdf,
            can_ai=can_ai,
            can_outcome=can_outcome,
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
    """Handle the ``/start copilot_<hash>`` and ``/start upskill_<code>`` links."""
    payload = context.args[0] if context.args else None
    if payload and str(payload).startswith(DEEPLINK_PREFIX) and copilot_enabled():
        job_hash = _job_hash(str(payload)[len(DEEPLINK_PREFIX):])
        if job_hash:
            await _send_analysis(self, update, job_hash)
            return
    if payload and str(payload).startswith(UPSKILL_PREFIX) and copilot_enabled():
        if upskill_enabled():
            self.db.log_event(
                update.effective_user.id if update.effective_user else None,
                "upskill_campaign_opened",
                {"campaign": "upskill"},
            )
            await _cmd_upskill(self, update, context)
            return
    return await _original_cmd_start(self, update, context)


async def _handle_hr(job_bot, update, job_hash: str) -> None:
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    job = _load_job(job_bot, job_hash)
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
    job = _load_job(job_bot, job_hash)
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
    job = _load_job(job_bot, job_hash)
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


async def _handle_ai(job_bot, update, job_hash: str) -> None:
    """Run the optional LLM analysis behind the explicit button."""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        await query.answer("Не удалось определить пользователя.", show_alert=True)
        return
    if not copilot_llm.llm_enabled():
        await query.answer("AI-разбор выключен.", show_alert=True)
        return
    if copilot_llm.llm_config() is None:
        await query.answer("AI-разбор временно недоступен.", show_alert=True)
        return

    job = _load_job(job_bot, job_hash)
    if not job:
        await query.answer("Карточка вакансии устарела.", show_alert=True)
        return

    store = CopilotStore.from_database(job_bot.db)
    manager = CreditManager(store) if store is not None else None

    cached = None
    if store is not None:
        try:
            cached = store.get_ai_report(user_id, job_hash)
        except Exception as exc:
            logger.info("Copilot AI cache read failed: %s", type(exc).__name__)
            cached = None
    if cached and cached.get("answer") and _ai_cache_fresh(cached.get("created_at")):
        await query.answer("Показываю сохранённый разбор")
        await query.message.reply_text(
            str(cached["answer"]), disable_web_page_preview=True
        )
        job_bot.db.log_event(user_id, "copilot_ai_cached", {"hash": job_hash})
        return

    if manager is not None:
        try:
            manager.ensure_daily_allowance(user_id)
            if manager.balance(user_id) < 1:
                await query.answer(
                    "Бесплатные AI-разборы на сегодня закончились. "
                    "Пригласи друга — он получит бонус, а ты — +5 кредитов.",
                    show_alert=True,
                )
                return
            if not manager.llm_allowed():
                await query.answer(
                    "Дневной лимит AI-разборов исчерпан. Попробуй позже.",
                    show_alert=True,
                )
                return
        except Exception as exc:
            logger.info("Copilot AI budget check failed: %s", type(exc).__name__)

    settings = job_bot.db.get_user_settings(user_id)
    profile = _profile_from_settings(settings)
    analysis = analyse_job(job, profile)
    report = analysis["report"]
    deterministic = {
        "fit_score": report.score,
        "matched_skills": list(report.matched_skills),
        "missing_skills": list(report.missing_skills),
        "grade": analysis["audit"].declared_grade,
    }

    await query.answer("Разбираю вакансию…")
    result = await _run_in_thread(
        copilot_llm.analyze,
        job,
        str(profile.get("skills") or ""),
        deterministic,
        None,
    )
    if not result.ok:
        await query.message.reply_text(
            "AI-разбор не получился. Ниже — обычный разбор без нейросети.\n\n"
            + format_analysis(job, analysis),
            disable_web_page_preview=True,
        )
        job_bot.db.log_event(
            user_id, "copilot_ai_failed", {"hash": job_hash, "error": result.error}
        )
        return

    text = result.as_text()
    if manager is not None:
        try:
            manager.spend(user_id, 1, REASON_LLM_CALL, job_hash)
        except Exception as exc:
            logger.info("Copilot AI credit spend failed: %s", type(exc).__name__)
    if store is not None:
        try:
            store.save_ai_report(
                user_id,
                job_hash,
                model=result.model,
                answer="" if result.used_resume else text,
                used_resume=result.used_resume,
            )
            store.save_evaluation(
                user_id,
                job_hash,
                fit_score=report.score,
                stack_match_score=report.stack_match_score,
                keyword_overlap_pct=report.keyword_overlap_pct,
                matched_keywords=list(report.matched_skills),
                missing_skills=list(report.missing_skills),
                llm_used=True,
                model=result.model,
            )
        except Exception as exc:
            logger.info("Copilot AI cache write failed: %s", type(exc).__name__)

    await query.message.reply_text(text, disable_web_page_preview=True)
    job_bot.db.log_event(
        user_id, "copilot_ai_viewed", {"hash": job_hash, "model": result.model}
    )


async def _handle_applied(job_bot, update, job_hash: str) -> None:
    """Remember that the user sent an application."""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        await query.answer("Не удалось определить пользователя.", show_alert=True)
        return
    if not outcome_enabled():
        await query.answer("Трекер откликов выключен.", show_alert=True)
        return
    job = _load_job(job_bot, job_hash)
    if not job:
        await query.answer("Карточка вакансии устарела.", show_alert=True)
        return
    store = CopilotStore.from_database(job_bot.db)
    if store is None:
        await query.answer("Трекер откликов доступен только с базой данных.", show_alert=True)
        return
    try:
        store.save_application(
            user_id,
            job_hash,
            company=str(job.get("company") or ""),
            title=str(job.get("title") or ""),
        )
    except Exception as exc:
        logger.info("Copilot application write failed: %s", type(exc).__name__)
        await query.answer("Не удалось сохранить отметку.", show_alert=True)
        return
    await query.answer("Отметил", show_alert=True)
    await query.message.reply_text(
        "Отметил. Через {days} дн. спрошу, что с ответом — отметь результат, "
        "и я перестану напоминать.".format(days=followup_days())
    )
    job_bot.db.log_event(user_id, "copilot_application_marked", {"hash": job_hash})


async def _handle_outcome(job_bot, update, stage: str, job_hash: str) -> None:
    """Close the loop: the user reports what happened to the application."""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        await query.answer("Не удалось определить пользователя.", show_alert=True)
        return
    if not outcome_enabled():
        await query.answer("Трекер откликов выключен.", show_alert=True)
        return
    normalized = copilot_outcome.normalize_stage(stage)
    if normalized not in copilot_outcome.OUTCOME_STAGES:
        await query.answer("Неизвестный результат.", show_alert=True)
        return
    store = CopilotStore.from_database(job_bot.db)
    if store is not None:
        try:
            store.set_application_stage(user_id, job_hash, normalized)
        except Exception as exc:
            logger.info("Copilot application update failed: %s", type(exc).__name__)
    await query.answer("Записал", show_alert=True)
    label = copilot_outcome.STAGE_LABELS.get(normalized, normalized)
    await query.message.reply_text(f"Записал: {label}. Спасибо — больше не напомню про этот отклик.")
    job_bot.db.log_event(
        user_id, "copilot_application_answered", {"hash": job_hash, "stage": normalized}
    )


def _upskill_share_url(user_id: int) -> str:
    """Share link of the radar with the campaign payload of the magnets."""
    username = str(os.getenv("BOT_USERNAME") or "").strip().lstrip("@")
    if not username:
        return ""
    target = f"https://t.me/{username}?start={UPSKILL_PREFIX}{int(user_id)}"
    text = "Мой upskill-радар по junior/middle IT — посчитай свой: " + target
    return (
        "https://t.me/share/url?url="
        + quote(target, safe="")
        + "&text="
        + quote(text, safe="")
    )


def _upskill_keyboard(user_id: int):
    """Keyboard of the radar: a share button and the campaign payload."""
    share_url = _upskill_share_url(user_id)
    if not share_url:
        return None
    return core.InlineKeyboardMarkup(
        [[core.InlineKeyboardButton("📤 Поделиться радаром", url=share_url)]]
    )


async def _cmd_upskill(self, update, context):
    """``/upskill`` - what the current stack covers on the vacancy feed."""
    if not (copilot_enabled() and upskill_enabled()):
        await update.message.reply_text("Upskill-радар выключен.")
        return
    user_id = update.effective_user.id if update.effective_user else None
    settings = self.db.get_user_settings(user_id) if user_id else {}
    profile = _profile_from_settings(settings)
    days = upskill_days()
    try:
        jobs = self.db.recent_jobs_for_digest(hours=days * 24, limit=200)
    except Exception as exc:
        logger.info("Upskill feed read failed: %s", type(exc).__name__)
        jobs = []
    radar = copilot_upskill.build_radar(jobs, profile)
    await update.message.reply_text(
        copilot_upskill.format_radar(radar, days=days),
        reply_markup=_upskill_keyboard(user_id) if user_id else None,
        disable_web_page_preview=True,
    )
    self.db.log_event(
        user_id,
        "copilot_upskill_viewed",
        {"total": radar.total, "covered": radar.covered},
    )


async def run_followup_reminders(job_bot, application, db, ledger=None) -> Dict:
    """Ask about applications that stayed unanswered.

    The step runs inside the existing daily delivery job, so no new scheduler
    is introduced. The receipt ledger keeps the reminder at-most-once.
    """
    result = {"sent": 0, "skipped": 0, "unresolved": 0}
    if not (copilot_enabled() and outcome_enabled()):
        return result
    store = CopilotStore.from_database(db)
    if store is None:
        return result
    try:
        applications = store.list_applications_due(days=followup_days())
    except Exception as exc:
        logger.info("Copilot follow-up read failed: %s", type(exc).__name__)
        return result

    for item in applications:
        user_id = int(item.get("user_id") or 0)
        job_hash = str(item.get("job_id") or "")
        if not user_id or not job_hash:
            continue
        if ledger is not None:
            try:
                if not ledger.claim("copilot_followup", f"app_{job_hash}", user_id):
                    result["skipped"] += 1
                    continue
            except Exception as exc:
                logger.info("Copilot follow-up claim failed: %s", type(exc).__name__)
        keyboard = core.InlineKeyboardMarkup(
            [
                [
                    core.InlineKeyboardButton(
                        label,
                        callback_data=copilot_outcome.outcome_callback(stage, job_hash),
                    )
                    for stage, label in copilot_outcome.OUTCOME_BUTTONS
                ]
            ]
        )
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=copilot_outcome.reminder_text(
                    title=item.get("title"),
                    company=item.get("company"),
                    days=followup_days(),
                ),
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            result["sent"] += 1
        except Exception:
            result["unresolved"] += 1
            continue
        try:
            store.mark_application_reminded(user_id, job_hash)
        except Exception as exc:
            logger.info("Copilot follow-up mark failed: %s", type(exc).__name__)
    return result


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

    job = _load_job(job_bot, job_hash)
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


async def _maybe_handle_forward(job_bot, update) -> bool:
    """Analyze a forwarded vacancy; returns True when the message was consumed.

    Commands and documents are left alone, and a text that looks like a resume
    is refused instead of being analyzed: the product never stores resume
    content.
    """
    if not (copilot_enabled() and forward_enabled()):
        return False
    message = getattr(update, "message", None)
    if message is None:
        return False
    user_id = update.effective_user.id if update.effective_user else None
    setup_steps = getattr(job_bot, "setup_steps", None) or {}
    if user_id and user_id in setup_steps:
        # The setup wizard owns the text input while it is running.
        return False
    document = getattr(message, "document", None)
    text = str(getattr(message, "text", "") or "")
    forwarded = bool(getattr(message, "forward_date", None))
    if not copilot_forward.is_forward_candidate(
        text, forwarded=forwarded, has_document=document is not None
    ):
        return False
    if copilot_forward.looks_like_resume(text):
        await message.reply_text(
            "Это похоже на резюме, а не на вакансию. "
            "Пришли файл (PDF или DOCX) — проверю его как резюме."
        )
        return True

    job = copilot_forward.build_job(text)
    job_hash = job["hash"]

    store = CopilotStore.from_database(job_bot.db)
    if store is not None:
        try:
            store.save_forwarded_job(
                job_hash,
                title=job["title"],
                company=job["company"],
                url=job["url"],
                text=job["description"],
            )
        except Exception as exc:
            logger.info("Copilot forwarded job cache failed: %s", type(exc).__name__)

    settings = job_bot.db.get_user_settings(user_id) if user_id else {}
    profile = _profile_from_settings(settings)
    analysis = analyse_job(job, profile)
    await message.reply_text(
        format_analysis(job, analysis),
        reply_markup=analysis_keyboard(
            job_hash,
            analysis,
            can_pdf=bool(pdf_enabled() and analysis["report"].matched_skills),
            can_ai=bool(ai_enabled()),
            can_outcome=bool(outcome_enabled()),
        ),
        disable_web_page_preview=True,
    )
    job_bot.db.log_event(
        user_id,
        "copilot_forward_analyzed",
        {"hash": job_hash, "score": analysis["report"].score},
    )
    return True


async def _copilot_handle_setup_text(self, update, context):
    """Route a resume upload to the CV build and a forward to the Copilot."""
    user_id = update.effective_user.id if update.effective_user else None
    requests = _pdf_requests(self)
    pending = requests.pop(user_id, None) if user_id else None
    if pending:
        await _build_cv_from_message(self, update, context, pending)
        return
    if await _maybe_handle_forward(self, update):
        return
    return await _original_handle_setup_text(self, update, context)


async def _copilot_handle_callback(self, update, context):
    """Route Copilot callbacks, everything else keeps the existing behaviour."""
    query = update.callback_query
    data = (query.data or "") if query else ""
    if not data.startswith(CALLBACK_PREFIX):
        return await _original_handle_callback(self, update, context)

    action, _, rest = data.partition(":")
    if action == "copilot_outcome":
        stage, _, job_hash = rest.partition(":")
        await _handle_outcome(self, update, stage, job_hash)
        return
    job_hash = rest
    if action == "copilot_hr":
        await _handle_hr(self, update, job_hash)
        return
    if action == "copilot_save":
        await _handle_save(self, update, job_hash)
        return
    if action == "copilot_enrich":
        await _handle_enrich(self, update, job_hash)
        return
    if action == "copilot_ai":
        await _handle_ai(self, update, job_hash)
        return
    if action == "copilot_pdf":
        await _handle_pdf(self, update, job_hash)
        return
    if action == "copilot_applied":
        await _handle_applied(self, update, job_hash)
        return
    if query:
        await query.answer()
    return None


def _copilot_build_runtime():
    """Build the runtime with the Copilot commands registered."""
    application, db, job_bot = _localized_build_runtime()
    application.add_handler(CommandHandler("copilot", job_bot.cmd_copilot))
    application.add_handler(CommandHandler("upskill", job_bot.cmd_upskill))
    return application, db, job_bot


async def _copilot_cmd_stats_growth(self, update, context):
    """The admin statistics also show the counters of the Copilot surfaces."""
    await _original_cmd_stats_growth(self, update, context)
    user_id = update.effective_user.id if update.effective_user else None
    is_admin = bool(core.Config.ADMIN_USER_ID and user_id == core.Config.ADMIN_USER_ID)
    if not is_admin:
        return
    days = int(getattr(core.Config, "GROWTH_STATS_DAYS", 30) or 30)
    if context.args:
        try:
            days = max(1, min(int(context.args[0]), 90))
        except ValueError:
            pass
    store = CopilotStore.from_database(self.db)
    if store is None:
        return
    try:
        counts = store.count_events_by_name(copilot_metrics.all_event_names(), days=days)
    except Exception as exc:
        logger.info("Copilot metrics read failed: %s", type(exc).__name__)
        return
    metrics = copilot_metrics.build_metrics(counts)
    if not metrics.fit_viewed and not metrics.ai_viewed:
        return
    await update.message.reply_text(
        copilot_metrics.format_metrics(metrics, days=days)
    )


durable.JobBot.cmd_start = _copilot_cmd_start
durable.JobBot.cmd_copilot = _cmd_copilot
durable.JobBot.cmd_upskill = _cmd_upskill
durable.JobBot.cmd_stats_growth = _copilot_cmd_stats_growth
durable.JobBot.handle_callback = _copilot_handle_callback
durable.JobBot.handle_setup_text = _copilot_handle_setup_text
base.build_runtime = _copilot_build_runtime

MAX_UPDATE_BYTES = base.MAX_UPDATE_BYTES
process_update_payload = base.process_update_payload
webhook_secret_valid = base.webhook_secret_valid
build_runtime = base.build_runtime
