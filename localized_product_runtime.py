"""RU/EN novice-friendly product surface layered on top of the stable P8 runtime."""
from __future__ import annotations

import json
import re
from typing import Dict

import channel_bot as core
import durable_product_state as p8
from product_i18n import CATEGORY_NAMES, category_name, normalize_language, t
from resume_documents import MAX_FILE_BYTES, ResumeDocumentError, extract_resume_document
from resume_matcher import analyze_resume_match
from saved_searches import suggested_search_from_resume


class DatabaseConnection(p8.DatabaseConnection):
    """P8 storage plus a persistent language preference stored as a product event."""

    def get_user_language(self, user_id: int) -> str:
        if not user_id:
            return ""
        try:
            if self._growth_store is not None:
                with self._growth_store._ensure_conn().cursor() as cur:
                    cur.execute(
                        """
                        SELECT props->>'language'
                        FROM growth_events
                        WHERE user_id=%s AND name='language_selected'
                        ORDER BY ts DESC LIMIT 1
                        """,
                        (int(user_id),),
                    )
                    row = cur.fetchone()
                return normalize_language(row[0] if row else "")
            row = self.fetchone(
                "SELECT props FROM events WHERE user_id=? AND name='language_selected' ORDER BY ts DESC LIMIT 1",
                (int(user_id),),
            )
            if not row:
                return ""
            try:
                props = json.loads(row[0] or "{}")
            except Exception:
                props = {}
            return normalize_language(props.get("language"))
        except Exception:
            return ""

    def set_user_language(self, user_id: int, language: str) -> bool:
        lang = normalize_language(language)
        if not user_id or not lang:
            return False
        self.log_event(int(user_id), "language_selected", {"language": lang})
        return True


class JobBot(p8.JobBot):
    """Human product copy; internal P8 terms never leak into the first-time UX."""

    def _lang(self, user_id: int) -> str:
        return self.db.get_user_language(user_id) or "ru"

    @staticmethod
    def _language_keyboard(payload: str = ""):
        suffix = str(payload or "")[:52]
        ru_data = "p9_lang_ru" + (":" + suffix if suffix else "")
        en_data = "p9_lang_en" + (":" + suffix if suffix else "")
        return core.InlineKeyboardMarkup([
            [
                core.InlineKeyboardButton("🇷🇺 Русский", callback_data=ru_data),
                core.InlineKeyboardButton("🇬🇧 English", callback_data=en_data),
            ]
        ])

    def _category_keyboard(self, user_id: int):
        lang = self._lang(user_id)
        settings = self.db.get_user_settings(user_id)
        enabled = set(settings.get("enabled_categories") or [])
        categories = list(CATEGORY_NAMES[lang].keys())
        rows = []
        for index in range(0, len(categories), 2):
            row = []
            for category in categories[index:index + 2]:
                mark = "✅" if category in enabled else "▫️"
                row.append(core.InlineKeyboardButton(
                    f"{mark} {category_name(category, lang)}",
                    callback_data=f"toggle_cat:{category}",
                ))
            rows.append(row)
        rows.append([core.InlineKeyboardButton(t(lang, "next_salary"), callback_data="setup_next_salary")])
        return core.InlineKeyboardMarkup(rows)

    def _retention_keyboard(self, user_id: int):
        lang = self._lang(user_id)
        settings = self.db.get_user_settings(user_id)
        searches = self.db.list_saved_searches(user_id)
        rows = [
            [core.InlineKeyboardButton(t(lang, "find3"), callback_data="growth_quick_fresh")],
            [
                core.InlineKeyboardButton(
                    t(lang, "alerts_on" if settings.get("alerts_enabled") else "alerts_off"),
                    callback_data="p8_alerts_toggle",
                ),
                core.InlineKeyboardButton(
                    t(lang, "daily_on" if settings.get("digest_enabled") else "daily_off"),
                    callback_data="p8_digest_toggle",
                ),
            ],
            [
                core.InlineKeyboardButton(t(lang, "my_searches", count=len(searches)), callback_data="p8_saved_searches"),
                core.InlineKeyboardButton(t(lang, "preferences"), callback_data="growth_quick_setup"),
            ],
            [core.InlineKeyboardButton(t(lang, "language"), callback_data="p9_switch_language")],
        ]
        channel = str(core.Config.CHANNEL_ID or "")
        if channel.startswith("@"):
            rows.append([core.InlineKeyboardButton(t(lang, "channel"), url=f"https://t.me/{channel.lstrip('@')}")])
        return core.InlineKeyboardMarkup(rows)

    async def _send_retention_hub(self, message, user_id: int, *, title: str = ""):
        lang = self._lang(user_id)
        settings = self.db.get_user_settings(user_id)
        searches = self.db.list_saved_searches(user_id)
        onboarding = bool(settings.get("onboarding_done"))
        intro = title or t(lang, "welcome_ready" if onboarding else "welcome_new")
        status = t(
            lang,
            "status",
            profile=t(lang, "ready" if onboarding else "not_ready"),
            alerts=t(lang, "on" if settings.get("alerts_enabled") else "off"),
            daily=t(lang, "on" if settings.get("digest_enabled") else "off"),
            searches=len(searches),
        )
        explainer = t(lang, "alerts_explain") + "\n\n" + t(lang, "daily_explain")
        await message.reply_text(
            f"{intro}\n\n{status}\n\n{explainer}",
            reply_markup=self._retention_keyboard(user_id),
            disable_web_page_preview=True,
        )
        self.db.log_event(user_id, "p9_hub_view", {"language": lang, "searches": len(searches)})

    async def _send_saved_searches(self, message, user_id: int):
        lang = self._lang(user_id)
        searches = self.db.list_saved_searches(user_id)
        lines = [t(lang, "searches_title"), "", t(lang, "searches_explain"), ""]
        rows = []
        if searches:
            for search in searches[:8]:
                details = search.skills or ", ".join(category_name(c, lang) for c in search.categories) or ("все направления" if lang == "ru" else "all job areas")
                lines.append(f"• {search.name} — {details}")
                rows.append([core.InlineKeyboardButton(f"🗑 {search.name[:30]}", callback_data=f"p8_saved_search_delete:{search.id}")])
        else:
            lines.append(t(lang, "searches_empty"))
        rows.append([core.InlineKeyboardButton(t(lang, "save_profile"), callback_data="saved_search_from_profile")])
        rows.append([core.InlineKeyboardButton(t(lang, "back"), callback_data="p8_retention_hub")])
        await message.reply_text("\n".join(lines), reply_markup=core.InlineKeyboardMarkup(rows))

    def _job_keyboard(self, job: Dict, lang: str, *, full: bool = False):
        job_hash = str(job.get("hash") or job.get("content_hash") or "")[:40]
        rows = []
        first = []
        url = str(job.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            first.append(core.InlineKeyboardButton(t(lang, "apply"), url=url))
        if job_hash:
            first.append(core.InlineKeyboardButton(t(lang, "check_resume"), callback_data=f"resume_match:{job_hash}"))
        if first:
            rows.append(first)
        second = []
        if job_hash:
            second.append(core.InlineKeyboardButton(t(lang, "save"), callback_data=f"save:{job_hash}"))
            second.append(core.InlineKeyboardButton(
                t(lang, "collapse" if full else "details"),
                callback_data=f"{'compact' if full else 'expand'}:{job_hash}",
            ))
        if second:
            rows.append(second)
        category = str(job.get("category") or "other")
        rows.append([core.InlineKeyboardButton(
            t(lang, "hide", category=category_name(category, lang)),
            callback_data=f"hide_cat:{category}",
        )])
        return core.InlineKeyboardMarkup(rows)

    def _job_text(self, job: Dict, lang: str, *, full: bool = False) -> str:
        title = str(job.get("title") or ("Вакансия" if lang == "ru" else "Job"))
        company = str(job.get("company") or "").strip()
        location = str(job.get("location_restriction") or job.get("location") or "Remote").strip()
        salary = str(job.get("salary") or "").strip()
        level = str(job.get("level") or "Junior/Middle")
        lines = [f"💼 {title}"]
        if company:
            lines.append(f"🏢 {company}")
        lines.append(f"🌍 {location}")
        lines.append(f"🎯 {level}")
        if salary and salary.lower() not in {"не указана", "not specified", "none"}:
            lines.append(f"💰 {salary}")
        if full:
            category = category_name(str(job.get("category") or "other"), lang)
            lines.append(f"📂 {category}")
            description = str(job.get("description") or "").strip()
            if description:
                lines.extend(["", ("О вакансии:" if lang == "ru" else "About the job:"), description[:900]])
            tags = job.get("tags") or []
            if tags:
                lines.extend(["", ("Навыки: " if lang == "ru" else "Skills: ") + ", ".join(str(x) for x in tags[:10])])
        return "\n".join(lines)[:3500]

    async def _send_first_value_preview(self, user_id: int, *, source: str) -> int:
        _settings, matched = self._select_first_value_jobs(user_id)
        if not matched:
            return 0
        lang = self._lang(user_id)
        try:
            await self.application.bot.send_message(
                chat_id=user_id,
                text=t(lang, "preview_header", count=len(matched)),
            )
        except Exception:
            return 0
        sent = 0
        for job in matched:
            try:
                await self.application.bot.send_message(
                    chat_id=user_id,
                    text=self._job_text(job, lang),
                    reply_markup=self._job_keyboard(job, lang),
                    disable_web_page_preview=True,
                )
                sent += 1
            except Exception:
                continue
        self.db.log_event(user_id, "first_value_preview_sent", {"count": sent, "source": source, "language": lang})
        return sent

    async def cmd_start(self, update, context):
        user = update.effective_user
        user_id = user.id if user else None
        if not user_id:
            return await super().cmd_start(update, context)
        payload = str(context.args[0] if context.args else "")[:52]
        if not self.db.get_user_language(user_id):
            self.db.log_event(user_id, "start", {"payload": payload, "language_pending": True})
            await update.message.reply_text(t("ru", "choose_language"), reply_markup=self._language_keyboard(payload))
            return
        if not payload:
            self.db.log_event(user_id, "start", {"payload": None})
            self.db.maybe_unlock_premium(user_id)
            await self._send_retention_hub(update.message, user_id)
            return
        if payload == "web_home":
            self.db.log_event(user_id, "start", {"payload": payload})
            self.db.log_event(user_id, "web_landing_start", {"surface": "public_site"})
            await self._send_retention_hub(update.message, user_id)
            return
        if payload.startswith("resume_"):
            job_hash = payload[7:47]
            job = self.db.get_job_payload(job_hash)
            lang = self._lang(user_id)
            self.db.log_event(user_id, "start", {"payload": payload})
            if not job:
                text = "Эта вакансия уже устарела. Открой свежую вакансию и повтори проверку." if lang == "ru" else "This job is no longer current. Open a fresh job and try the resume check again."
                await update.message.reply_text(text)
                return
            self.resume_sessions[user_id] = {"job_hash": job_hash, "job": job}
            self.db.log_event(user_id, "resume_match_started", {"hash": job_hash, "source": "deeplink"})
            await update.message.reply_text(self._resume_prompt_for_user(user_id, job))
            return
        if payload.startswith("web_") or payload.startswith("share_"):
            job_hash = payload.split("_", 1)[1]
            job = self.db.get_job_payload(job_hash)
            lang = self._lang(user_id)
            self.db.log_event(user_id, "start", {"payload": payload})
            if not job:
                await update.message.reply_text(t(lang, "stale"))
                await self._send_retention_hub(update.message, user_id)
                return
            await update.message.reply_text(self._job_text(job, lang), reply_markup=self._job_keyboard(job, lang))
            return
        return await super().cmd_start(update, context)

    async def cmd_setup(self, update, context):
        user_id = update.effective_user.id
        if not self.db.get_user_language(user_id):
            await update.message.reply_text(t("ru", "choose_language"), reply_markup=self._language_keyboard())
            return
        self.setup_steps[user_id] = "categories"
        self.db.log_event(user_id, "setup_start", {"source": "command"})
        lang = self._lang(user_id)
        await update.message.reply_text(t(lang, "setup1"), reply_markup=self._category_keyboard(user_id))

    async def cmd_profile(self, update, context):
        user_id = update.effective_user.id
        lang = self._lang(user_id)
        s = self.db.get_user_settings(user_id)
        cats = ", ".join(category_name(c, lang) for c in (s.get("enabled_categories") or [])[:12]) or "—"
        text = (
            f"{t(lang, 'profile_title')}\n\n"
            f"{t(lang, 'profile_categories')}: {cats}\n"
            f"{t(lang, 'profile_salary')}: ${int(s.get('min_salary_filter') or 0)}\n"
            f"{t(lang, 'profile_skills')}: {s.get('skills') or '—'}\n"
            f"{t(lang, 'profile_alerts')}: {t(lang, 'on' if s.get('alerts_enabled') else 'off')}\n"
            f"{t(lang, 'profile_daily')}: {t(lang, 'on' if s.get('digest_enabled') else 'off')}\n"
            f"{t(lang, 'profile_language')}: {'Русский' if lang == 'ru' else 'English'}"
        )
        await update.message.reply_text(text, reply_markup=self._retention_keyboard(user_id))

    async def cmd_alerts(self, update, context):
        user_id = update.effective_user.id
        lang = self._lang(user_id)
        arg = str(context.args[0] if context.args else "").lower()
        if arg in {"on", "1", "true", "yes", "да"}:
            self.db.save_user_settings(user_id, {"alerts_enabled": True})
            self.db.log_event(user_id, "alerts_on", {"source": "command"})
            await update.message.reply_text(t(lang, "alerts_enabled"))
            return
        if arg in {"off", "0", "false", "no", "нет"}:
            self.db.save_user_settings(user_id, {"alerts_enabled": False})
            self.db.log_event(user_id, "alerts_off", {"source": "command"})
            await update.message.reply_text(t(lang, "alerts_disabled"))
            return
        await self._send_retention_hub(update.message, user_id)

    async def cmd_digest(self, update, context):
        user_id = update.effective_user.id
        lang = self._lang(user_id)
        arg = str(context.args[0] if context.args else "").lower()
        if arg == "now":
            count = await self.send_personal_digest(user_id)
            if not count:
                await update.message.reply_text(t(lang, "no_matches"))
            return
        if arg in {"on", "1", "true", "yes", "да"}:
            self.db.save_user_settings(user_id, {"digest_enabled": True})
            self.db.log_event(user_id, "digest_on", {"source": "command"})
            await update.message.reply_text(t(lang, "daily_enabled"))
            return
        if arg in {"off", "0", "false", "no", "нет"}:
            self.db.save_user_settings(user_id, {"digest_enabled": False})
            self.db.log_event(user_id, "digest_off", {"source": "command"})
            await update.message.reply_text(t(lang, "daily_disabled"))
            return
        await self._send_retention_hub(update.message, user_id)

    async def cmd_categories(self, update, context):
        user_id = update.effective_user.id
        lang = self._lang(user_id)
        await update.message.reply_text(t(lang, "setup1"), reply_markup=self._category_keyboard(user_id))

    async def cmd_favorites(self, update, context):
        user_id = update.effective_user.id
        lang = self._lang(user_id)
        favorites = self.db.get_user_favorites(user_id)
        if not favorites:
            await update.message.reply_text("💾 Сохранённых вакансий пока нет." if lang == "ru" else "💾 You have no saved jobs yet.")
            return
        heading = f"💾 Сохранённые вакансии ({len(favorites)})" if lang == "ru" else f"💾 Saved jobs ({len(favorites)})"
        lines = [heading, ""]
        for job in favorites[:10]:
            lines.append(f"• {job.get('title', '')} — {job.get('company', '')}")
        await update.message.reply_text("\n".join(lines))

    def _resume_prompt_for_user(self, user_id: int, job: Dict) -> str:
        lang = self._lang(user_id)
        title = str(job.get("title") or ("вакансия" if lang == "ru" else "job"))
        company = str(job.get("company") or "").strip()
        target = f"{title} @ {company}" if company else title
        return t(lang, "resume_prompt", target=target)

    def _format_resume_report(self, user_id: int, report, job: Dict) -> str:
        lang = self._lang(user_id)
        title = str(job.get("title") or ("вакансия" if lang == "ru" else "job"))
        company = str(job.get("company") or "").strip()
        heading = f"{title} · {company}" if company else title
        if lang == "ru":
            lines = [f"📄 Совпадение резюме: {report.score}%", heading, "", "Оценка ориентировочная и не является официальным ATS-результатом."]
            if report.matched_skills:
                lines.append("✅ Уже подходит: " + ", ".join(report.matched_skills[:8]))
            if report.missing_skills:
                lines.append("⚠️ Не нашёл в резюме: " + ", ".join(report.missing_skills[:8]))
            lines.append(f"🔎 Совпадение ключевых слов: {report.keyword_overlap_pct}%")
            lines.extend(["", "Что улучшить перед откликом:"])
            for item in report.recommendations[:5]:
                lines.append("• " + item)
            lines.extend(["", "Текст резюме не сохранялся."])
            return "\n".join(lines)[:3900]
        lines = [f"📄 Resume fit: {report.score}%", heading, "", "This is a practical text-based estimate, not an official ATS score."]
        if report.matched_skills:
            lines.append("✅ Skills found in your resume: " + ", ".join(report.matched_skills[:8]))
        if report.missing_skills:
            lines.append("⚠️ Mentioned in the job but not found in your resume: " + ", ".join(report.missing_skills[:8]))
        lines.append(f"🔎 Keyword overlap: {report.keyword_overlap_pct}%")
        lines.extend(["", "Before you apply:"])
        if report.missing_skills:
            lines.append("• If you genuinely have the missing skills, show them through a concrete project or task. Do not add keywords you cannot support.")
        if report.matched_skills:
            lines.append("• Move your most relevant experience higher and make the matching skills easy to see.")
        if not report.role_overlap:
            lines.append("• Make the target role clear in your resume headline or summary.")
        if report.keyword_overlap_pct < 35:
            lines.append("• Rewrite a few achievement bullets using the same concepts and responsibilities as the job description.")
        lines.extend(["", "Your resume text was not stored."])
        return "\n".join(lines)[:3900]

    async def _complete_resume(self, update, user_id: int, text: str, kind: str):
        session = self.resume_sessions.pop(user_id)
        job = session["job"]
        report = analyze_resume_match(text, job)
        self.pending_saved_searches[user_id] = suggested_search_from_resume(job, report.matched_skills)
        self.db.log_event(user_id, "resume_match_completed", {
            "hash": session["job_hash"], "score": report.score,
            "required_skills": len(report.required_skills), "matched_skills": len(report.matched_skills),
            "input_type": kind, "language": self._lang(user_id),
        })
        lang = self._lang(user_id)
        await update.message.reply_text(
            self._format_resume_report(user_id, report, job),
            reply_markup=core.InlineKeyboardMarkup([
                [core.InlineKeyboardButton(
                    "🔔 Сохранить похожие вакансии" if lang == "ru" else "🔔 Save similar jobs",
                    callback_data="saved_search_from_resume",
                )],
                [core.InlineKeyboardButton(t(lang, "preferences"), callback_data="growth_quick_setup")],
            ]),
        )

    async def handle_setup_text(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        lang = self._lang(user_id)
        step = self.setup_steps.get(user_id)
        if step == "salary":
            raw = (update.message.text or "").strip()
            digits = re.sub(r"[^\d]", "", raw)
            if not digits:
                await update.message.reply_text(t(lang, "bad_salary"))
                return
            self.db.save_user_settings(user_id, {"min_salary_filter": int(digits)})
            self.setup_steps[user_id] = "skills"
            await update.message.reply_text(t(lang, "setup3"))
            return
        if step == "skills":
            raw = (update.message.text or "").strip()
            skills = "" if raw in {"-", "—", "skip", "нет"} else raw[:200]
            self.db.save_user_settings(user_id, {"skills": skills})
            self.setup_steps[user_id] = "digest"
            await update.message.reply_text(
                t(lang, "setup4"),
                reply_markup=core.InlineKeyboardMarkup([[
                    core.InlineKeyboardButton(t(lang, "yes_daily"), callback_data="setup_digest_on"),
                    core.InlineKeyboardButton(t(lang, "no_daily"), callback_data="setup_digest_off"),
                ]]),
            )
            return
        if user_id in self.resume_sessions:
            message = update.message
            document = getattr(message, "document", None)
            if document is not None:
                if document.file_size and int(document.file_size) > MAX_FILE_BYTES:
                    await message.reply_text("Файл слишком большой: максимум 5 МБ." if lang == "ru" else "The file is too large. Maximum size is 5 MB.")
                    return
                try:
                    tg_file = await context.bot.get_file(document.file_id)
                    payload = await tg_file.download_as_bytearray()
                    parsed = extract_resume_document(payload, filename=document.file_name or "", mime_type=document.mime_type or "")
                except ResumeDocumentError as exc:
                    await message.reply_text(str(exc) if lang == "ru" else "I could not read this file. Please send a text-based PDF/DOCX or paste the resume text.")
                    return
                except Exception:
                    await message.reply_text("Не удалось обработать файл. Попробуй снова или вставь текст." if lang == "ru" else "I could not process the file. Try again or paste the resume text.")
                    return
                await self._complete_resume(update, user_id, parsed.text, parsed.kind)
                return
            text = (message.text or "").strip()
            if len(text) < 120:
                await message.reply_text("Пришли резюме целиком: опыт, проекты и навыки." if lang == "ru" else "Please send more of your resume: experience, projects, and skills.")
                return
            await self._complete_resume(update, user_id, text[:30000], "text")
            return
        return await super().handle_setup_text(update, context)

    async def _start_setup_from_callback(self, query, user_id: int) -> None:
        self.setup_steps[user_id] = "categories"
        self.db.log_event(user_id, "setup_start", {"source": "button", "language": self._lang(user_id)})
        await query.message.reply_text(t(self._lang(user_id), "setup1"), reply_markup=self._category_keyboard(user_id))

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

        if data.startswith("p9_lang_ru") or data.startswith("p9_lang_en"):
            lang = "ru" if data.startswith("p9_lang_ru") else "en"
            parts = data.split(":", 1)
            payload = parts[1] if len(parts) > 1 else ""
            self.db.set_user_language(user_id, lang)
            await query.answer("Русский" if lang == "ru" else "English")
            if payload:
                old_args = list(getattr(context, "args", []) or [])
                try:
                    context.args = [payload]
                    return await self.cmd_start(update, context)
                finally:
                    context.args = old_args
            await self._send_retention_hub(query.message, user_id)
            return
        if data == "p9_switch_language":
            await query.answer()
            await query.message.reply_text(t(self._lang(user_id), "choose_language"), reply_markup=self._language_keyboard())
            return

        lang = self._lang(user_id)
        if data == "growth_quick_setup":
            await query.answer()
            await self._start_setup_from_callback(query, user_id)
            return
        if data == "toggle_cat:":
            return
        if data.startswith("toggle_cat:"):
            category = data.split(":", 1)[1]
            settings = self.db.get_user_settings(user_id)
            enabled = list(settings.get("enabled_categories") or [])
            if category in enabled:
                enabled.remove(category)
            else:
                enabled.append(category)
            self.db.update_user_categories(user_id, enabled)
            await query.answer()
            await query.edit_message_reply_markup(reply_markup=self._category_keyboard(user_id))
            return
        if data == "setup_next_salary":
            await query.answer()
            self.setup_steps[user_id] = "salary"
            await query.edit_message_text(t(lang, "setup2"))
            return
        if data in {"setup_digest_on", "setup_digest_off"}:
            enabled = data == "setup_digest_on"
            self.db.save_user_settings(user_id, {"digest_enabled": enabled, "onboarding_done": True})
            self.setup_steps.pop(user_id, None)
            self.db.log_event(user_id, "setup_done", {"daily_roundup": enabled, "language": lang})
            await query.answer(t(lang, "profile_ready"))
            await query.edit_message_text(t(lang, "profile_ready"))
            await self._send_retention_hub(query.message, user_id)
            return
        if data == "p8_alerts_toggle":
            current = self.db.get_user_settings(user_id)
            enabled = not bool(current.get("alerts_enabled"))
            self.db.save_user_settings(user_id, {"alerts_enabled": enabled})
            self.db.log_event(user_id, "alerts_on" if enabled else "alerts_off", {"source": "hub"})
            await query.answer(t(lang, "alerts_enabled" if enabled else "alerts_disabled"))
            await self._send_retention_hub(query.message, user_id)
            return
        if data == "p8_digest_toggle":
            current = self.db.get_user_settings(user_id)
            enabled = not bool(current.get("digest_enabled"))
            self.db.save_user_settings(user_id, {"digest_enabled": enabled})
            self.db.log_event(user_id, "digest_on" if enabled else "digest_off", {"source": "hub"})
            await query.answer(t(lang, "daily_enabled" if enabled else "daily_disabled"))
            await self._send_retention_hub(query.message, user_id)
            return
        if data == "p8_retention_hub":
            await query.answer()
            await self._send_retention_hub(query.message, user_id)
            return
        if data == "p8_saved_searches":
            await query.answer()
            await self._send_saved_searches(query.message, user_id)
            return
        if data.startswith("p8_saved_search_delete:"):
            try:
                search_id = int(data.split(":", 1)[1])
            except ValueError:
                await query.answer()
                return
            deleted = self.db.delete_saved_search(user_id, search_id)
            if deleted:
                self.db.log_event(user_id, "saved_search_deleted", {"search_id": search_id, "source": "hub"})
            await query.answer(t(lang, "deleted" if deleted else "already_deleted"))
            await self._send_saved_searches(query.message, user_id)
            return
        if data == "growth_quick_fresh":
            await query.answer(t(lang, "looking3"))
            n = await self._send_first_value_preview(user_id, source="localized_hub")
            self.db.log_event(user_id, "fresh_preview", {"count": n, "source": "localized_hub", "language": lang})
            if not n:
                await query.message.reply_text(t(lang, "no_matches"), reply_markup=core.InlineKeyboardMarkup([[core.InlineKeyboardButton(t(lang, "preferences"), callback_data="growth_quick_setup")]]))
            return
        if data.startswith("resume_match:"):
            job_hash = data.split(":", 1)[1]
            job = self.db.get_job_payload(job_hash)
            if not job:
                await query.answer(t(lang, "stale"), show_alert=True)
                return
            self.resume_sessions[user_id] = {"job_hash": job_hash, "job": job}
            self.db.log_event(user_id, "resume_match_started", {"hash": job_hash, "source": "button", "language": lang})
            await query.answer()
            await query.message.reply_text(self._resume_prompt_for_user(user_id, job))
            return
        if data.startswith("expand:") or data.startswith("compact:"):
            full = data.startswith("expand:")
            job_hash = data.split(":", 1)[1]
            job = self.db.get_job_payload(job_hash)
            if not job:
                await query.answer(t(lang, "stale"), show_alert=True)
                return
            await query.answer()
            await query.edit_message_text(self._job_text(job, lang, full=full), reply_markup=self._job_keyboard(job, lang, full=full), disable_web_page_preview=True)
            return
        if data.startswith("hide_cat:"):
            category = data.split(":", 1)[1]
            self.db.hide_category_for_user(user_id, category)
            await query.answer(("Скрыто: " if lang == "ru" else "Hidden: ") + category_name(category, lang))
            return
        if data.startswith("save:"):
            job_hash = data.split(":", 1)[1]
            if not self.db.add_favorite(user_id, job_hash):
                await query.answer(t(lang, "stale"), show_alert=True)
                return
            self.db.log_event(user_id, "save_job", {"hash": job_hash, "source": "localized_card"})
            await query.answer(t(lang, "saved"))
            await context.bot.send_message(
                chat_id=user_id,
                text=t(lang, "saved_next"),
                reply_markup=core.InlineKeyboardMarkup([
                    [core.InlineKeyboardButton(t(lang, "check_resume"), callback_data=f"resume_match:{job_hash}")],
                    [core.InlineKeyboardButton(t(lang, "manage_new"), callback_data="p8_retention_hub")],
                ]),
            )
            return
        if data in {"saved_search_from_profile", "saved_search_from_resume"}:
            result = await super().handle_callback(update, context)
            await self._send_retention_hub(query.message, user_id)
            return result
        return await super().handle_callback(update, context)


# Final extension points for the request-driven production runtime.
core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = p8.main
