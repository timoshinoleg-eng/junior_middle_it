"""Setup UX v8: atomic interests, typo-tolerant skills, explicit delivery mode."""
from __future__ import annotations

import json
import re
from typing import Any, Dict

import channel_bot as core
import localized_product_runtime_v7 as base
from product_i18n import CATEGORY_NAMES, t
from skill_normalization import normalize_skills_input
from telegram.error import BadRequest


class DatabaseConnection(base.DatabaseConnection):
    """Single-round-trip setup actions for the request-driven production bot."""

    def fast_start_snapshot(self, user_id: int) -> Dict[str, Any]:
        """Retrieve /start language, menu switches and saved-search count in one query.

        No writes occur before the first visible reply. In the HTTP-backed
        webhook the query costs one Edge round-trip instead of three or more.
        """
        store = getattr(self, "_growth_store", None)
        if store is None:
            settings = self.get_user_settings(user_id)
            return {
                "language": self.get_user_language(user_id),
                "onboarding_done": bool(settings.get("onboarding_done")),
                "alerts_enabled": bool(settings.get("alerts_enabled")),
                "digest_enabled": bool(settings.get("digest_enabled")),
                "search_count": len(self.list_saved_searches(user_id)),
            }

        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE((
                        SELECT props->>'language'
                        FROM growth_events
                        WHERE user_id=%s AND name='language_selected'
                        ORDER BY ts DESC LIMIT 1
                    ), '') AS language,
                    COALESCE(s.onboarding_done, FALSE) AS onboarding_done,
                    COALESCE(s.alerts_enabled, FALSE) AS alerts_enabled,
                    COALESCE(s.digest_enabled, FALSE) AS digest_enabled,
                    (
                        SELECT COUNT(*) FROM growth_saved_searches
                        WHERE user_id=%s AND enabled=TRUE
                    ) AS search_count
                FROM (SELECT %s::bigint AS user_id) incoming
                LEFT JOIN growth_user_settings s ON s.user_id=incoming.user_id
                """,
                (int(user_id), int(user_id), int(user_id)),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("start snapshot returned no row")
        return {
            "language": str(row[0] or ""),
            "onboarding_done": bool(row[1]),
            "alerts_enabled": bool(row[2]),
            "digest_enabled": bool(row[3]),
            "search_count": int(row[4] or 0),
        }


    def fast_toggle_category(self, user_id: int, category: str) -> tuple[str, list[str]]:
        store = getattr(self, "_growth_store", None)
        all_categories = list(getattr(store, "all_categories", []) or CATEGORY_NAMES["ru"].keys())
        if category not in all_categories:
            raise ValueError("unsupported category")

        if store is None:
            lang = self.get_user_language(user_id) or "ru"
            settings = self.get_user_settings(user_id)
            enabled = list(settings.get("enabled_categories") or all_categories)
            if category in enabled:
                enabled.remove(category)
            else:
                enabled.append(category)
            if not enabled:
                enabled = list(all_categories)
            self.save_user_settings(user_id, {"enabled_categories": enabled})
            return lang, enabled

        defaults = ",".join(all_categories)
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH current AS (
                    SELECT COALESCE(NULLIF((
                        SELECT enabled_categories
                        FROM growth_user_settings
                        WHERE user_id=%s
                    ), ''), %s) AS cats
                ),
                toggled AS (
                    SELECT CASE
                        WHEN %s = ANY(string_to_array(cats, ',')) THEN
                            COALESCE(
                                NULLIF(
                                    array_to_string(
                                        array_remove(string_to_array(cats, ','), %s),
                                        ','
                                    ),
                                    ''
                                ),
                                %s
                            )
                        ELSE cats || ',' || %s
                    END AS cats
                    FROM current
                ),
                settings_write AS (
                    INSERT INTO growth_user_settings
                        (user_id, enabled_categories, updated_at)
                    SELECT %s, cats, NOW() FROM toggled
                    ON CONFLICT (user_id) DO UPDATE SET
                        enabled_categories=EXCLUDED.enabled_categories,
                        updated_at=NOW()
                    RETURNING enabled_categories
                )
                SELECT
                    enabled_categories,
                    COALESCE((
                        SELECT props->>'language'
                        FROM growth_events
                        WHERE user_id=%s AND name='language_selected'
                        ORDER BY ts DESC LIMIT 1
                    ), 'ru')
                FROM settings_write
                """,
                (
                    int(user_id), defaults, category, category, defaults,
                    category, int(user_id), int(user_id),
                ),
            )
            row = cur.fetchone()
        if not row:
            return "ru", list(all_categories)
        enabled = [item for item in str(row[0] or "").split(",") if item]
        return str(row[1] or "ru"), (enabled or list(all_categories))

    def fast_advance_setup(self, user_id: int, next_step: str) -> str:
        if next_step not in {"salary", "skills", "digest"}:
            raise ValueError("unsupported setup step")
        store = getattr(self, "_growth_store", None)
        if store is None:
            lang = self.get_user_language(user_id) or "ru"
            self.set_runtime_state(
                user_id,
                "setup_step",
                {"step": next_step},
                ttl_seconds=24 * 60 * 60,
            )
            return lang

        expiry = self._runtime_expiry(24 * 60 * 60)
        state = json.dumps({"step": next_step}, separators=(",", ":"))
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH state_write AS (
                    INSERT INTO growth_runtime_state
                        (user_id, state_key, state, expires_at, updated_at)
                    VALUES (%s, 'setup_step', %s::jsonb, %s, NOW())
                    ON CONFLICT (user_id, state_key) DO UPDATE SET
                        state=EXCLUDED.state,
                        expires_at=EXCLUDED.expires_at,
                        updated_at=NOW()
                    RETURNING user_id
                )
                SELECT COALESCE((
                    SELECT props->>'language'
                    FROM growth_events
                    WHERE user_id=%s AND name='language_selected'
                    ORDER BY ts DESC LIMIT 1
                ), 'ru')
                FROM state_write
                """,
                (int(user_id), state, expiry, int(user_id)),
            )
            row = cur.fetchone()
        return str(row[0] or "ru") if row else "ru"

    def fast_finish_setup_delivery(self, user_id: int, mode: str) -> Dict[str, Any]:
        if mode not in {"instant", "daily", "none"}:
            raise ValueError("unsupported delivery mode")
        alerts_enabled = mode == "instant"
        digest_enabled = mode == "daily"
        store = getattr(self, "_growth_store", None)
        if store is None:
            lang = self.get_user_language(user_id) or "ru"
            self.save_user_settings(
                user_id,
                {
                    "alerts_enabled": alerts_enabled,
                    "digest_enabled": digest_enabled,
                    "onboarding_done": True,
                },
            )
            self.delete_runtime_state(user_id, "setup_step")
            self.log_event(
                user_id,
                "setup_done",
                {
                    "delivery_mode": mode,
                    "instant_alerts": alerts_enabled,
                    "daily_roundup": digest_enabled,
                    "language": lang,
                },
            )
            return {
                "language": lang,
                "onboarding_done": True,
                "alerts_enabled": alerts_enabled,
                "digest_enabled": digest_enabled,
                "search_count": len(self.list_saved_searches(user_id)),
            }

        props = json.dumps(
            {
                "delivery_mode": mode,
                "instant_alerts": alerts_enabled,
                "daily_roundup": digest_enabled,
            },
            separators=(",", ":"),
        )
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH settings_write AS (
                    INSERT INTO growth_user_settings
                        (user_id, alerts_enabled, digest_enabled, onboarding_done, updated_at)
                    VALUES (%s, %s, %s, TRUE, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        alerts_enabled=EXCLUDED.alerts_enabled,
                        digest_enabled=EXCLUDED.digest_enabled,
                        onboarding_done=TRUE,
                        updated_at=NOW()
                    RETURNING user_id
                ),
                state_delete AS (
                    DELETE FROM growth_runtime_state
                    WHERE user_id=%s AND state_key='setup_step'
                    RETURNING user_id
                ),
                language AS (
                    SELECT COALESCE((
                        SELECT props->>'language'
                        FROM growth_events
                        WHERE user_id=%s AND name='language_selected'
                        ORDER BY ts DESC LIMIT 1
                    ), 'ru') AS lang
                ),
                event_write AS (
                    INSERT INTO growth_events (user_id, name, props)
                    SELECT
                        %s,
                        'setup_done',
                        (%s::jsonb || jsonb_build_object('language', language.lang))
                    FROM language
                    RETURNING id
                )
                SELECT
                    language.lang,
                    (SELECT COUNT(*) FROM growth_saved_searches
                     WHERE user_id=%s AND enabled=TRUE)
                FROM language
                """,
                (
                    int(user_id), alerts_enabled, digest_enabled,
                    int(user_id), int(user_id), int(user_id), props, int(user_id),
                ),
            )
            row = cur.fetchone()
        lang = str(row[0] or "ru") if row else "ru"
        search_count = int(row[1] or 0) if row else 0
        return {
            "language": lang,
            "onboarding_done": True,
            "alerts_enabled": alerts_enabled,
            "digest_enabled": digest_enabled,
            "search_count": search_count,
        }


class _SearchCount:
    def __init__(self, count: int):
        self.count = max(0, int(count))

    def __len__(self) -> int:
        return self.count


class JobBot(base.JobBot):
    async def cmd_start(self, update, context):
        """One durable read to render the ordinary start menu.

        Deep-links/referrals retain their existing first-touch implementation.
        This fast path never changes persistent settings or skips credit rules.
        """
        user = getattr(update, "effective_user", None)
        user_id = getattr(user, "id", None)
        if not user_id or getattr(context, "args", None):
            return await super().cmd_start(update, context)

        snapshot = self.db.fast_start_snapshot(user_id)
        language = str(snapshot.get("language") or "")
        if not language:
            await update.message.reply_text(
                t("ru", "choose_language"),
                reply_markup=self._language_keyboard(),
            )
            try:
                self.db.log_event(user_id, "start", {"payload": None, "language_pending": True})
            except Exception as exc:
                core.logger.warning("Start analytics failed after reply: %s", type(exc).__name__)
            return

        self._set_cached_language(user_id, language)
        search_count = int(snapshot.get("search_count") or 0)
        settings = {
            "onboarding_done": bool(snapshot.get("onboarding_done")),
            "alerts_enabled": bool(snapshot.get("alerts_enabled")),
            "digest_enabled": bool(snapshot.get("digest_enabled")),
        }
        title = t(language, "welcome_ready" if settings["onboarding_done"] else "welcome_new")
        status = t(
            language,
            "status",
            profile=t(language, "ready" if settings["onboarding_done"] else "not_ready"),
            alerts=t(language, "on" if settings["alerts_enabled"] else "off"),
            daily=t(language, "on" if settings["digest_enabled"] else "off"),
            searches=search_count,
        )
        explanation = t(language, "alerts_explain") + "\n\n" + t(language, "daily_explain")
        await update.message.reply_text(
            f"{title}\n\n{status}\n\n{explanation}",
            reply_markup=self._retention_keyboard_from_state(
                language, settings, _SearchCount(search_count)
            ),
            disable_web_page_preview=True,
        )
        # The visible reply must not wait for optional telemetry or premium
        # bookkeeping; the latter keeps the existing error-handling contract.
        try:
            self.db.log_event(user_id, "p10_hub_view", {"language": language, "searches": search_count})
            self.db.log_event(user_id, "start", {"payload": None})
            self.db.maybe_unlock_premium(user_id)
        except Exception as exc:
            core.logger.warning("Start bookkeeping failed after reply: %s", type(exc).__name__)

    @staticmethod
    def _delivery_prompt(lang: str) -> str:
        if lang == "en":
            return (
                "⚙️ Step 4 of 4. How should I tell you about matching new jobs?\n"
                "Choose one mode. You can change it later."
            )
        return (
            "⚙️ Шаг 4 из 4. Как сообщать о новых подходящих вакансиях?\n"
            "Выбери один режим. Его можно поменять позже."
        )

    @staticmethod
    def _delivery_keyboard(lang: str):
        if lang == "en":
            labels = (
                ("🔔 Send new matches immediately", "setup_delivery_instant"),
                ("📅 One roundup per day", "setup_delivery_daily"),
                ("🔕 No automatic messages", "setup_delivery_none"),
            )
        else:
            labels = (
                ("🔔 Сразу при появлении", "setup_delivery_instant"),
                ("📅 Одна подборка в день", "setup_delivery_daily"),
                ("🔕 Без автоматических сообщений", "setup_delivery_none"),
            )
        return core.InlineKeyboardMarkup([
            [core.InlineKeyboardButton(text, callback_data=callback)]
            for text, callback in labels
        ])

    @staticmethod
    def _delivery_done_text(lang: str, mode: str) -> str:
        if lang == "en":
            return {
                "instant": "✅ Preferences saved. I will message you when a new matching job appears.",
                "daily": "✅ Preferences saved. I will send matching jobs together once a day.",
                "none": (
                    "✅ Preferences saved. Automatic messages are off. "
                    "You can still open fresh matches manually at any time."
                ),
            }[mode]
        return {
            "instant": "✅ Настройки сохранены. Буду писать, когда появится новая подходящая вакансия.",
            "daily": "✅ Настройки сохранены. Подходящие вакансии будут приходить одной подборкой раз в день.",
            "none": (
                "✅ Настройки сохранены. Автоматические сообщения выключены. "
                "Свежие вакансии всегда можно открыть вручную кнопкой «Показать 3 вакансии»."
            ),
        }[mode]

    @staticmethod
    async def _safe_edit_reply_markup(query, *, reply_markup) -> None:
        try:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).casefold():
                raise

    @staticmethod
    async def _safe_edit_message_text(query, text: str, **kwargs) -> None:
        try:
            await query.edit_message_text(text, **kwargs)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).casefold():
                raise

    async def handle_setup_text(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        language, step = self.db.fast_setup_context(user_id)
        lang = language or "ru"
        self._set_cached_language(user_id, lang)

        if step == "salary":
            raw = (update.message.text or "").strip()
            digits = re.sub(r"[^\d]", "", raw)
            if not digits:
                await update.message.reply_text(t(lang, "bad_salary"))
                return
            self.db.fast_setup_value_and_step(
                user_id,
                field="min_salary_filter",
                value=int(digits),
                next_step="skills",
            )
            await update.message.reply_text(t(lang, "setup3"))
            return

        if step == "skills":
            raw = (update.message.text or "").strip()
            if raw.casefold() in {"-", "—", "skip", "нет", "no"}:
                normalized = normalize_skills_input("")
            else:
                normalized = normalize_skills_input(raw[:300])
            self.db.fast_setup_value_and_step(
                user_id,
                field="skills",
                value=normalized.stored,
                next_step="digest",
            )
            text = self._delivery_prompt(lang)
            if normalized.corrections and normalized.display:
                if lang == "en":
                    text = f"I understood your skills as: {normalized.display}.\n\n{text}"
                else:
                    text = f"Понял навыки как: {normalized.display}.\n\n{text}"
            await update.message.reply_text(
                text,
                reply_markup=self._delivery_keyboard(lang),
            )
            return

        return await super().handle_setup_text(update, context)

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

        if data.startswith("toggle_cat:") and data != "toggle_cat:":
            await self._ack_callback(query)
            category = data.split(":", 1)[1]
            lang, enabled = self.db.fast_toggle_category(user_id, category)
            self._set_cached_language(user_id, lang)
            await self._safe_edit_reply_markup(
                query,
                reply_markup=self._category_keyboard_from_enabled(lang, enabled),
            )
            return

        if data == "setup_next_salary":
            await self._ack_callback(query)
            lang = self.db.fast_advance_setup(user_id, "salary")
            self._set_cached_language(user_id, lang)
            await self._safe_edit_message_text(query, t(lang, "setup2"))
            return

        delivery_map = {
            "setup_delivery_instant": "instant",
            "setup_delivery_daily": "daily",
            "setup_delivery_none": "none",
            # Compatibility for already-open v6/v7 setup messages.
            "setup_digest_on": "daily",
            "setup_digest_off": "none",
        }
        if data in delivery_map:
            await self._ack_callback(query)
            mode = delivery_map[data]
            snapshot = self.db.fast_finish_setup_delivery(user_id, mode)
            lang = str(snapshot.get("language") or "ru")
            self._set_cached_language(user_id, lang)
            settings = {
                "onboarding_done": True,
                "alerts_enabled": bool(snapshot.get("alerts_enabled")),
                "digest_enabled": bool(snapshot.get("digest_enabled")),
            }
            await self._safe_edit_message_text(
                query,
                self._delivery_done_text(lang, mode),
                reply_markup=self._retention_keyboard_from_state(
                    lang,
                    settings,
                    _SearchCount(int(snapshot.get("search_count") or 0)),
                ),
                disable_web_page_preview=True,
            )
            return

        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
