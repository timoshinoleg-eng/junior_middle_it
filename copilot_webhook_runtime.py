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

from durable_runtime_state import RuntimeStateMap
from urllib.parse import quote

import channel_bot as core
import durable_product_state as durable
import interactive_webhook_runtime as base
import localized_webhook_runtime as localized
from saved_searches import suggested_search_from_resume
from telegram import WebAppInfo
from telegram.ext import CommandHandler

from copilot import flag_enabled, paid_operations_enabled
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
from copilot.credit_operations import (
    DELIVERY_DELIVERED,
    DELIVERY_REJECTED,
    DELIVERY_UNKNOWN,
    CreditStoreUnavailable,
    InsufficientCredits,
    cv_operation,
    llm_operation,
    settle_delivery_safely,
)
from copilot.credits import CreditManager
from copilot.store import CopilotStore, CopilotStoreUnavailable
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
    return paid_operations_enabled(pdf=True)


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


def _release_ai_claim(store, user_id: int, job_hash: str, claim: str) -> None:
    """Release the analysis slot of an attempt that produced no answer.

    The slot has to be free again, otherwise the vacancy would be blocked for
    the next fifteen minutes. A release that fails is not fatal: the claim
    goes stale on its own and a later attempt takes it over.
    """
    if store is None or not claim:
        return
    try:
        store.release_ai_report_claim(user_id, job_hash, claim)
    except Exception as exc:
        logger.info("Copilot AI claim release failed: %s", type(exc).__name__)


async def _handle_ai(job_bot, update, job_hash: str) -> None:
    """Run the optional LLM analysis behind the explicit button."""
    if not paid_operations_enabled():
        logger.info("Copilot AI refused: maintenance")
        await update.callback_query.answer("Технические работы, попробуй позже.", show_alert=True)
        return
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

    if manager is None:
        # The credit store is unavailable. A paid call to the model must not
        # be started at all, because the user could not be charged for it.
        job_bot.db.log_event(user_id, "copilot_ai_no_store", {"hash": job_hash})
        await query.answer("Показываю обычный разбор")
        await query.message.reply_text(
            "AI-разбор недоступен: не удалось проверить кредиты. "
            "Ниже — обычный разбор без нейросети.\n\n" + format_analysis(job, analysis),
            disable_web_page_preview=True,
        )
        return

    try:
        operation = llm_operation(store, user_id, job_hash)
        operation_state = operation.state()
        if operation_state == "released":
            # A refunded AI request must not silently start another paid attempt.
            await query.answer("Показываю обычный разбор")
            await query.message.reply_text(
                "AI-разбор был прерван. Ниже — обычный разбор без нейросети.\n\n"
                + format_analysis(job, analysis), disable_web_page_preview=True,
            )
            return
        if not operation.paid:
            manager.ensure_daily_allowance(user_id)
        if not operation.paid and manager.balance(user_id) < 1:
            await query.answer(
                "Бесплатные AI-разборы на сегодня закончились. "
                "Пригласи друга — он получит бонус, а ты — +5 кредитов.",
                show_alert=True,
            )
            return
    except Exception as exc:
        # The balance could not be confirmed, so the paid analysis is not
        # started at all.
        logger.info("Copilot AI budget check failed: %s", type(exc).__name__)
        await query.answer("Не удалось проверить кредиты. Попробуй позже.", show_alert=True)
        return

    # The analysis slot of this vacancy is claimed before the call. Without
    # the claim two parallel taps would call the model twice, would burn two
    # slots of the global budget and could charge the user twice for one
    # vacancy: the credit of the operation is reserved by a statement that
    # cannot see the parallel transaction yet.
    try:
        claim = store.claim_ai_report(user_id, job_hash)
    except Exception as exc:
        logger.warning("Copilot AI claim failed: %s", type(exc).__name__)
        await query.answer("Не удалось проверить кредиты. Попробуй позже.", show_alert=True)
        return
    if not claim:
        await query.answer(
            "Этот разбор уже выполняется. Попробуй через минуту.",
            show_alert=True,
        )
        return

    # The claim is owned by this request from here on. Every exit below
    # releases the slot, so a failure of the model, of the cache or of
    # the delivery never blocks the next request. Release deletes an empty
    # placeholder or clears this token while keeping a cached answer.
    # A newer attempt's token and report are never changed.
    try:
        operation.bind_claim(claim)
        if operation_state == "reserved":
            try:
                operation.reserve()
            except (InsufficientCredits, CreditStoreUnavailable) as exc:
                logger.warning("Copilot AI reservation adoption refused: %s", type(exc).__name__)
                await query.message.reply_text(
                    "AI-разбор уже выполняется или был прерван. Попробуй позже.\n\n"
                    + format_analysis(job, analysis), disable_web_page_preview=True,
                )
                return
        # The slot of the global budget is taken before the call. A call of the
        # model costs the operator even when the user is not charged for it
        # again, and repeated requests for the same vacancy must not pass the
        # budget.
        try:
            slot = manager.reserve_llm_call(user_id, job_hash)
        except Exception as exc:
            logger.warning("Copilot AI budget reservation failed: %s", type(exc).__name__)
            _release_ai_claim(store, user_id, job_hash, claim)
            await query.answer(
                "Не удалось проверить лимит AI-разборов. Попробуй позже.", show_alert=True
            )
            return
        if not slot:
            _release_ai_claim(store, user_id, job_hash, claim)
            await query.answer(
                "Дневной лимит AI-разборов исчерпан. Попробуй позже.", show_alert=True
            )
            await query.message.reply_text(
                "AI-разбор временно недоступен. Ниже — обычный разбор без нейросети.\n\n"
                + format_analysis(job, analysis), disable_web_page_preview=True,
            )
            return

        await query.answer("Разбираю вакансию…")
        result = await _run_in_thread(
            copilot_llm.analyze,
            job,
            str(profile.get("skills") or ""),
            deterministic,
            None,
        )
        if not result.ok:
            _release_ai_claim(store, user_id, job_hash, claim)
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
            # The analysis is paid for. It is only shown when the credit is
            # confirmed, otherwise the free deterministic report is shown instead.
            try:
                operation.reserve()
            except (InsufficientCredits, CreditStoreUnavailable) as exc:
                logger.info("Copilot AI credit reservation failed: %s", type(exc).__name__)
                _release_ai_claim(store, user_id, job_hash, claim)
                job_bot.db.log_event(user_id, "copilot_ai_not_charged", {"hash": job_hash})
                await query.message.reply_text(
                    "AI-разбор не показан: не удалось списать кредит.\n\n"
                    + format_analysis(job, analysis),
                    disable_web_page_preview=True,
                )
                return
        if (operation is not None and not operation.can_deliver()) or (
            operation is None and not store.owns_claim(user_id, job_hash, claim, "llm_call")
        ):
            _release_ai_claim(store, user_id, job_hash, claim)
            await query.message.reply_text(
                "AI-разбор уже выполняется или был прерван. Попробуй позже.\n\n"
                + format_analysis(job, analysis),
                disable_web_page_preview=True,
            )
            return

        try:
            await query.message.reply_text(text, disable_web_page_preview=True)
        except Exception as exc:
            logger.warning("Copilot AI delivery failed: %s", type(exc).__name__)
            if operation is not None:
                # Only a proven Telegram rejection permits a refund.
                settle_delivery_safely(operation, _telegram_delivery_outcome(exc))
            raise
        if operation is not None:
            settle_delivery_safely(operation, DELIVERY_DELIVERED)
        if store is not None:
            try:
                saved = store.save_ai_report(
                    user_id,
                    job_hash,
                    claim=claim,
                    model=result.model,
                    answer="" if result.used_resume else text,
                    used_resume=result.used_resume,
                )
            except Exception as exc:
                logger.info("Copilot AI report cache failed: %s", type(exc).__name__)
            else:
                if not saved:
                    # The write was refused: the claim belongs to another
                    # attempt now, so this answer is not cached. The credit
                    # is paid for all the same and the slot of the other
                    # attempt stays untouched.
                    logger.info("Copilot AI report cache write was refused")
            try:
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

        job_bot.db.log_event(
            user_id, "copilot_ai_viewed", {"hash": job_hash, "model": result.model}
        )

    finally:
        # The claim is given back on every path that did not keep it.
        _release_ai_claim(store, user_id, job_hash, claim)


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
    # A Vercel webhook creates a fresh runtime for EVERY Telegram update.
    # Persist only the vacancy hash, never the resume or the file bytes.
    requests[user_id] = {"job_hash": job_hash}
    await query.answer()
    await query.message.reply_text(
        "Пришли резюме файлом (PDF или DOCX) — соберу одностраничное CV под эту вакансию.\n\n"
        "Файл не сохраняется: я читаю его в памяти и удаляю сразу после сборки."
    )


def _pdf_requests(job_bot):
    """Restart-safe pending CV selection; store only the public vacancy hash."""
    requests = getattr(job_bot, "copilot_pdf_requests", None)
    if requests is None:
        db = job_bot.db
        if all(callable(getattr(db, method, None)) for method in
               ("get_runtime_state", "set_runtime_state", "delete_runtime_state")):
            def decode(state):
                job_hash = str(state.get("job_hash") or "")
                if len(job_hash) != MAX_HASH_CHARS or any(
                    char not in "0123456789abcdef" for char in job_hash.lower()
                ):
                    return None
                return {"job_hash": job_hash}

            requests = RuntimeStateMap(
                db, "copilot_cv_pending", ttl_seconds=60 * 60,
                encode=lambda state: {"job_hash": str(state["job_hash"])[:MAX_HASH_CHARS]},
                decode=decode,
            )
        else:
            # Lightweight test/local fake databases keep the old dict shape.
            requests = {}
        setattr(job_bot, "copilot_pdf_requests", requests)
    return requests


def _telegram_delivery_outcome(exc: BaseException) -> str:
    """Whether Telegram rejected the document or only lost the answer.

    A rejected document never reached the user, so the credit is given back.
    A lost answer leaves the outcome open: Telegram may have stored the file
    anyway, so the credit stays spent.
    """
    try:
        from telegram import error as telegram_error
    except Exception:
        return DELIVERY_UNKNOWN
    rejected = tuple(
        error
        for error in (
            telegram_error.BadRequest,
            telegram_error.Forbidden,
            telegram_error.InvalidToken,
            # A flood control answer means the request was not processed at
            # all, so the document is provably not delivered. The Mini App
            # treats the same 429 as a rejection.
            getattr(telegram_error, "RetryAfter", None),
        )
        if error is not None
    )
    if isinstance(exc, rejected):
        return DELIVERY_REJECTED
    return DELIVERY_UNKNOWN


def _release_pdf_claim(store, user_id: int, job_hash: str, claim: str) -> None:
    """Give the document slot back after a build that produced nothing.

    Only the owner of the claim releases the slot: a build that took over a
    stale claim keeps it.
    """
    if store is None or not claim:
        return
    try:
        store.release_document_claim(user_id, job_hash, claim)
    except Exception as exc:
        logger.info("Copilot document claim release failed: %s", type(exc).__name__)


async def _build_cv_from_message(job_bot, update, context, pending: Dict, requests=None) -> None:
    """Build the tailored CV from the resume the user just sent."""
    if not paid_operations_enabled(pdf=True):
        logger.info("Copilot PDF refused: maintenance")
        await update.message.reply_text("Технические работы, попробуй позже.")
        return
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

    # Consume the pending selection only after a valid resume has been parsed.
    # Unsupported or corrupt uploads can be replaced without pressing the
    # vacancy button again; no image text or file data is ever saved.
    if requests is not None:
        requests.pop(user_id, None)
    job = pending["job"]
    job_hash = pending["job_hash"]
    settings = job_bot.db.get_user_settings(user_id)
    profile = _profile_from_settings(settings)
    analysis = analyse_job(job, profile)
    contact = copilot_pdf.extract_contacts(resume_text)

    store = CopilotStore.from_database(job_bot.db)
    if store is None:
        await message.reply_text("Сборка CV доступна только с базой данных.")
        return

    # One build of one vacancy at a time. The slot is the primary key of the
    # document cache, so a second tap cannot assemble the same document in
    # parallel and cannot be charged for it either.
    try:
        claimed = store.claim_document(user_id, job_hash, CV_FILENAME)
    except Exception as exc:
        logger.warning("Copilot document claim failed: %s", type(exc).__name__)
        await message.reply_text("Не удалось начать сборку. Попробуй позже.")
        return
    if not claimed:
        # The slot is taken. Either the document is already delivered, or
        # another build of the same vacancy is still running.
        cached = None
        try:
            cached = store.get_document(user_id, job_hash)
        except Exception as exc:
            logger.info("Copilot document cache read failed: %s", type(exc).__name__)
        if cached and cached.get("telegram_file_id"):
            try:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=str(cached["telegram_file_id"]),
                    caption="🧾 Твоё CV под эту вакансию (сохранённая версия).",
                )
            except Exception as exc:
                logger.warning("CV resend failed: %s", type(exc).__name__)
                await message.reply_text("Не удалось отправить файл. Попробуй позже.")
                return
            job_bot.db.log_event(user_id, "copilot_cv_cached", {"hash": job_hash})
            await message.reply_text("Этот CV уже собран — вот он.")
            return
        await message.reply_text("Сборка CV по этой вакансии уже идёт. Попробуй через минуту.")
        return

    # The claim is owned by this request from here on. Every exit below
    # releases the slot, so a failure of the build, of the credit check
    # or of the delivery never blocks the vacancy. Releasing the claim
    # of a delivered document is a no-op: the release only touches a row
    # that still holds this claim and no file.
    try:
        try:
            pdf_bytes = await _run_in_thread(
                copilot_pdf.build_cv_pdf,
                contact=contact,
                role=str(job.get("title") or "вакансия"),
                company=str(job.get("company") or ""),
                skills=analysis["report"].matched_skills,
                note="Собрано под эту вакансию: совпавшие навыки из твоего профиля.",
            )
            report = await _run_in_thread(copilot_pdf.validate_pdf_bytes, pdf_bytes, job=job)
            if resume_bytes:
                source_report = await _run_in_thread(
                    copilot_pdf.validate_pdf_bytes, resume_bytes, job=job
                )
            else:
                source_report = None
        except Exception as exc:
            logger.warning("CV build failed: %s", type(exc).__name__)
            _release_pdf_claim(store, user_id, job_hash, claimed)
            await message.reply_text("Не удалось собрать CV. Попробуй текстом резюме или позже.")
            return

        caption = "🧾 CV под вакансию «{}».".format(str(job.get("title") or "вакансия"))
        caption += "\n" + report.summary()
        if source_report is not None and source_report.problems:
            caption += "\nТвой исходный файл: " + "; ".join(source_report.problems[:2])

        # The credit is only taken once the document exists, and only for the
        # attempt that delivers it. A repeated request of the same vacancy keeps
        # the state the operation already has.
        operation = cv_operation(store, user_id, job_hash).bind_claim(claimed)
        try:
            CreditManager(store).ensure_daily_allowance(user_id)
            operation.reserve()
        except InsufficientCredits:
            _release_pdf_claim(store, user_id, job_hash, claimed)
            await message.reply_text(
                "Бесплатные сборки на сегодня закончились. Пригласи друга — он получит бонус, а ты — +5 кредитов."
            )
            return
        except (CreditStoreUnavailable, CopilotStoreUnavailable) as exc:
            # The store is unavailable. Nothing was reserved, so the claim is
            # released and the user is told that the check failed, not that the
            # balance is empty.
            logger.warning("Copilot credit reservation failed: %s", type(exc).__name__)
            _release_pdf_claim(store, user_id, job_hash, claimed)
            await message.reply_text("Не удалось проверить кредиты. Попробуй позже.")
            return
        except Exception as exc:
            # Any other failure of the credit check leaves the operation in the
            # same state: no credit was reserved, the claim is released.
            logger.warning("Copilot credit reservation failed: %s", type(exc).__name__)
            _release_pdf_claim(store, user_id, job_hash, claimed)
            await message.reply_text("Не удалось проверить кредиты. Попробуй позже.")
            return

        if not operation.can_deliver():
            _release_pdf_claim(store, user_id, job_hash, claimed)
            await message.reply_text("Сборка уже выполняется или была прервана. Попробуй позже.")
            return

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
            # Telegram may have stored the file even though the answer was lost,
            # so a proven refusal pays the credit back and an open outcome does
            # not.
            settle_delivery_safely(operation, _telegram_delivery_outcome(exc))
            _release_pdf_claim(store, user_id, job_hash, claimed)
            await message.reply_text("Не удалось отправить файл. Попробуй позже.")
            return

        settle_delivery_safely(operation, DELIVERY_DELIVERED)

        if file_id:
            try:
                saved = store.save_document(
                    user_id,
                    job_hash,
                    telegram_file_id=file_id,
                    filename=CV_FILENAME,
                    fit_score=analysis["report"].score,
                    claim=claimed,
                )
            except Exception as exc:
                logger.info("Copilot document cache write failed: %s", type(exc).__name__)
                # The document was delivered, but the cache does not know it. The
                # slot is released, so the next request builds it again and stays
                # free: the operation is already paid for.
                _release_pdf_claim(store, user_id, job_hash, claimed)
            else:
                if not saved:
                    # The write was refused: the claim belongs to another build
                    # now, so this file is not cached. The document was delivered
                    # and the operation is paid for.
                    logger.info("Copilot document cache write was refused")

        job_bot.db.log_event(
            user_id,
            "copilot_cv_built",
            {"hash": job_hash, "pages": report.pages, "problems": len(report.problems)},
        )
        await message.reply_text("Готово. Файл выше — CV под эту вакансию.")

    finally:
        # The claim is given back on every path that did not keep it.
        _release_pdf_claim(store, user_id, job_hash, claimed)


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
            "Получил текст резюме. Чтобы проверить его, сначала открой "
            "конкретную вакансию и нажми «📄 Проверить резюме» или «Собрать CV». "
            "Затем пришли текст ещё раз либо PDF/DOCX. "
            "Без выбранной вакансии я не могу сделать корректное сравнение. "
            "Текст этого сообщения не сохранял."
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


def _unmatched_resume_text(self, update) -> bool:
    """Detect a resume pasted without an active vacancy-specific workflow.

    Examine text in memory only. Never store it, forward it as a vacancy or
    start a paid operation. Do not intercept ongoing setup or Resume Match.
    """
    message = getattr(update, "message", None)
    if not message or getattr(message, "document", None) is not None:
        return False
    text = str(getattr(message, "text", "") or "").strip()
    if len(text) < 80 or text.startswith("/"):
        return False
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return False
    if user_id in (getattr(self, "setup_steps", None) or {}):
        return False
    if user_id in (getattr(self, "resume_sessions", None) or {}):
        return False
    if copilot_forward.looks_like_resume(text):
        return True
    # A pasted resume without literal #resume, CV or section markers
    # is still a user text that deserves an actionable reply. Preserve
    # vacancy-forward handling for messages that are actually forwarded
    # or contain a link; do not persist or analyze their content as a CV.
    if not copilot_forward.is_forward_candidate(
        text,
        forwarded=bool(getattr(message, "forward_date", None)),
        has_document=False,
    ):
        return True
    sample = text.casefold()
    signals = (
        "опыт работы", "мои навыки", "образование", "контактная информация",
        "профессиональный опыт", "work experience", "my skills",
        "education", "employment history",
    )
    return sum(signal in sample for signal in signals) >= 2


async def _reply_choose_vacancy_for_resume(message):
    await message.reply_text(
        "Текст резюме получил, но вакансия не выбрана. "
        "Сначала открой вакансию и нажми «📄 Проверить резюме» "
        "или «Собрать CV», затем отправь текст заново. "
        "Сейчас не списывал кредиты и не сохранял резюме.",
        reply_markup=core.InlineKeyboardMarkup([[
            core.InlineKeyboardButton(
                "Найти вакансии", callback_data="growth_quick_fresh"
            )
        ]]),
    )


async def _copilot_handle_setup_text(self, update, context):
    """Route a resume upload to the CV build and a forward to the Copilot."""
    user_id = update.effective_user.id if update.effective_user else None
    requests = _pdf_requests(self)
    pending = requests.get(user_id) if user_id else None
    if pending:
        job = _load_job(self, str(pending["job_hash"]))
        if not job:
            requests.pop(user_id, None)
            await update.message.reply_text(
                "Вакансия больше недоступна. Открой её карточку и нажми «Собрать CV» заново."
            )
            return
        await _build_cv_from_message(
            self, update, context,
            {"job_hash": pending["job_hash"], "job": job}, requests=requests,
        )
        return
    if _unmatched_resume_text(self, update):
        await _reply_choose_vacancy_for_resume(update.message)
        return
    if await _maybe_handle_forward(self, update):
        return
    return await _original_handle_setup_text(self, update, context)


async def _resume_image_guidance(self, update, context):
    """Always acknowledge an image sent during a resume workflow.

    OCR is not implemented; never treat an image as empty resume text and
    never download it or spend a credit. Both Telegram photos and image
    documents reach this handler before generic text/document handlers.
    """
    message = update.message
    if message is None:
        return
    await message.reply_text(
        "Получил изображение, но PNG/JPG пока не умею распознавать. "
        "Пришли резюме в DOCX или PDF с выделяемым текстом (не скан) "
        "или вставь текст резюме сообщением. "
        "Если выбрана вакансия, повторно выбирать её не нужно."
    )


async def _copilot_handle_callback(self, update, context):
    """Route Copilot callbacks, everything else keeps the existing behaviour."""
    query = update.callback_query
    data = (query.data or "") if query else ""
    if not data.startswith(CALLBACK_PREFIX):
        return await _original_handle_callback(self, update, context)

    action, _, rest = data.partition(":")
    if action in ("copilot_ai", "copilot_pdf") and not paid_operations_enabled(pdf=action == "copilot_pdf"):
        logger.info("Copilot paid callback refused: maintenance")
        await query.answer("Технические работы, попробуй позже.", show_alert=True)
        return
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
durable.JobBot.handle_resume_image = _resume_image_guidance
base.build_runtime = _copilot_build_runtime

MAX_UPDATE_BYTES = base.MAX_UPDATE_BYTES
process_update_payload = base.process_update_payload
webhook_secret_valid = base.webhook_secret_valid
build_runtime = base.build_runtime
