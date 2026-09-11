"""Latency hardening for the request-driven Telegram setup flow.

The interactive Vercel runtime talks to durable state through a restricted
Supabase Edge SQL proxy. This layer keeps the same restart-safe contracts while
collapsing write pairs into single SQL round-trips and avoiding redundant reads
inside the four-step setup wizard.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict

import channel_bot as core
import localized_product_runtime_v5 as base
from product_i18n import CATEGORY_NAMES, t


class DatabaseConnection(base.DatabaseConnection):
    """Production fast paths that preserve the existing local/dev fallback."""

    _SETTING_FIELDS = {
        "enabled_categories",
        "hide_senior",
        "min_salary_filter",
        "skills",
        "digest_enabled",
        "onboarding_done",
        "alerts_enabled",
        "premium_unlocked",
    }

    def _normalized_settings_patch(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        patch: Dict[str, Any] = {}
        for key, value in (settings or {}).items():
            if key not in self._SETTING_FIELDS or value is None:
                continue
            if key == "enabled_categories":
                cats = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
                cats = [str(item).strip() for item in cats if str(item).strip()]
                if not cats:
                    store = getattr(self, "_growth_store", None)
                    cats = list(getattr(store, "all_categories", []) or [])
                patch[key] = ",".join(cats)
            elif key in {"hide_senior", "digest_enabled", "onboarding_done", "alerts_enabled", "premium_unlocked"}:
                patch[key] = bool(value)
            elif key == "min_salary_filter":
                patch[key] = max(0, int(value or 0))
            elif key == "skills":
                patch[key] = str(value or "")[:500]
        return patch

    def save_user_settings(self, user_id: int, settings: Dict[str, Any]) -> bool:
        store = getattr(self, "_growth_store", None)
        if store is None:
            return super().save_user_settings(user_id, settings)

        patch = self._normalized_settings_patch(settings)
        if not patch:
            return True
        columns = list(patch)
        values = [patch[name] for name in columns]
        quoted_columns = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        updates = ", ".join(f"{name}=EXCLUDED.{name}" for name in columns)
        query = (
            "INSERT INTO growth_user_settings "
            f"(user_id, {quoted_columns}, updated_at) "
            f"VALUES (%s, {placeholders}, NOW()) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            f"{updates}, updated_at=NOW()"
        )
        with store._ensure_conn().cursor() as cur:
            cur.execute(query, (int(user_id), *values))
        return True

    def set_runtime_state(
        self,
        user_id: int,
        state_key: str,
        state: dict,
        *,
        ttl_seconds: int,
    ) -> None:
        store = getattr(self, "_growth_store", None)
        if store is None:
            return super().set_runtime_state(
                user_id, state_key, state, ttl_seconds=ttl_seconds
            )
        key = str(state_key or "").strip()[:80]
        if not key or not isinstance(state, dict):
            raise ValueError("invalid runtime state")
        expiry = self._runtime_expiry(ttl_seconds)
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 16_000:
            raise ValueError("runtime state is too large")
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH cleanup AS (
                    DELETE FROM growth_runtime_state
                    WHERE expires_at IS NOT NULL AND expires_at <= NOW()
                    RETURNING user_id
                )
                INSERT INTO growth_runtime_state
                    (user_id, state_key, state, expires_at, updated_at)
                VALUES (%s, %s, %s::jsonb, %s, NOW())
                ON CONFLICT (user_id, state_key) DO UPDATE SET
                    state=EXCLUDED.state,
                    expires_at=EXCLUDED.expires_at,
                    updated_at=NOW()
                """,
                (int(user_id), key, payload, expiry),
            )

    def fast_setup_context(self, user_id: int) -> tuple[str, str]:
        store = getattr(self, "_growth_store", None)
        if store is None:
            language = self.get_user_language(user_id)
            state = self.get_runtime_state(user_id, "setup_step") or {}
            return language, str(state.get("step") or "")
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE((
                        SELECT props->>'language'
                        FROM growth_events
                        WHERE user_id=%s AND name='language_selected'
                        ORDER BY ts DESC LIMIT 1
                    ), ''),
                    COALESCE((
                        SELECT state->>'step'
                        FROM growth_runtime_state
                        WHERE user_id=%s
                          AND state_key='setup_step'
                          AND (expires_at IS NULL OR expires_at > NOW())
                        LIMIT 1
                    ), '')
                """,
                (int(user_id), int(user_id)),
            )
            row = cur.fetchone()
        return (str(row[0] or ""), str(row[1] or "")) if row else ("", "")

    def fast_begin_setup(self, user_id: int, language: str) -> list[str]:
        store = getattr(self, "_growth_store", None)
        if store is None:
            self.set_runtime_state(
                user_id, "setup_step", {"step": "categories"}, ttl_seconds=24 * 60 * 60
            )
            self.log_event(user_id, "setup_start", {"source": "button", "language": language})
            return list(self.get_user_settings(user_id).get("enabled_categories") or [])

        expiry = self._runtime_expiry(24 * 60 * 60)
        props = json.dumps(
            {"source": "button", "language": str(language or "ru")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH state_write AS (
                    INSERT INTO growth_runtime_state
                        (user_id, state_key, state, expires_at, updated_at)
                    VALUES (%s, 'setup_step', '{"step":"categories"}'::jsonb, %s, NOW())
                    ON CONFLICT (user_id, state_key) DO UPDATE SET
                        state=EXCLUDED.state,
                        expires_at=EXCLUDED.expires_at,
                        updated_at=NOW()
                    RETURNING user_id
                ),
                event_write AS (
                    INSERT INTO growth_events (user_id, name, props)
                    VALUES (%s, 'setup_start', %s::jsonb)
                    RETURNING id
                )
                SELECT COALESCE((
                    SELECT enabled_categories
                    FROM growth_user_settings
                    WHERE user_id=%s
                ), '')
                """,
                (int(user_id), expiry, int(user_id), props, int(user_id)),
            )
            row = cur.fetchone()
        raw = str(row[0] or "") if row else ""
        cats = [item for item in raw.split(",") if item]
        return cats or list(getattr(store, "all_categories", []) or [])

    def fast_setup_value_and_step(
        self,
        user_id: int,
        *,
        field: str,
        value: Any,
        next_step: str,
    ) -> None:
        if field not in {"min_salary_filter", "skills"}:
            raise ValueError("unsupported setup field")
        if next_step not in {"skills", "digest"}:
            raise ValueError("unsupported setup step")
        store = getattr(self, "_growth_store", None)
        if store is None:
            self.save_user_settings(user_id, {field: value})
            self.set_runtime_state(
                user_id,
                "setup_step",
                {"step": next_step},
                ttl_seconds=24 * 60 * 60,
            )
            return

        normalized = self._normalized_settings_patch({field: value})[field]
        expiry = self._runtime_expiry(24 * 60 * 60)
        state = json.dumps({"step": next_step}, separators=(",", ":"))
        query = f"""
            WITH settings_write AS (
                INSERT INTO growth_user_settings (user_id, {field}, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    {field}=EXCLUDED.{field},
                    updated_at=NOW()
                RETURNING user_id
            ),
            cleanup AS (
                DELETE FROM growth_runtime_state
                WHERE expires_at IS NOT NULL AND expires_at <= NOW()
                RETURNING user_id
            )
            INSERT INTO growth_runtime_state
                (user_id, state_key, state, expires_at, updated_at)
            VALUES (%s, 'setup_step', %s::jsonb, %s, NOW())
            ON CONFLICT (user_id, state_key) DO UPDATE SET
                state=EXCLUDED.state,
                expires_at=EXCLUDED.expires_at,
                updated_at=NOW()
        """
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                query,
                (int(user_id), normalized, int(user_id), state, expiry),
            )

    def fast_finish_setup(self, user_id: int, enabled: bool, language: str) -> None:
        store = getattr(self, "_growth_store", None)
        if store is None:
            self.save_user_settings(
                user_id, {"digest_enabled": enabled, "onboarding_done": True}
            )
            self.delete_runtime_state(user_id, "setup_step")
            self.log_event(
                user_id,
                "setup_done",
                {"daily_roundup": bool(enabled), "language": language},
            )
            return

        props = json.dumps(
            {"daily_roundup": bool(enabled), "language": str(language or "ru")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH settings_write AS (
                    INSERT INTO growth_user_settings
                        (user_id, digest_enabled, onboarding_done, updated_at)
                    VALUES (%s, %s, TRUE, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
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
                event_write AS (
                    INSERT INTO growth_events (user_id, name, props)
                    VALUES (%s, 'setup_done', %s::jsonb)
                    RETURNING id
                )
                SELECT 1
                """,
                (int(user_id), bool(enabled), int(user_id), int(user_id), props),
            )
            cur.fetchone()

    def fast_hub_snapshot(self, user_id: int, language: str) -> Dict[str, Any]:
        store = getattr(self, "_growth_store", None)
        if store is None:
            settings = self.get_user_settings(user_id)
            count = len(self.list_saved_searches(user_id))
            self.log_event(
                user_id, "p10_hub_view", {"language": language, "searches": count}
            )
            return {
                "onboarding_done": bool(settings.get("onboarding_done")),
                "alerts_enabled": bool(settings.get("alerts_enabled")),
                "digest_enabled": bool(settings.get("digest_enabled")),
                "search_count": count,
            }

        with store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                WITH snapshot AS (
                    SELECT
                        COALESCE((
                            SELECT onboarding_done
                            FROM growth_user_settings WHERE user_id=%s
                        ), FALSE) AS onboarding_done,
                        COALESCE((
                            SELECT alerts_enabled
                            FROM growth_user_settings WHERE user_id=%s
                        ), FALSE) AS alerts_enabled,
                        COALESCE((
                            SELECT digest_enabled
                            FROM growth_user_settings WHERE user_id=%s
                        ), FALSE) AS digest_enabled,
                        (
                            SELECT COUNT(*)
                            FROM growth_saved_searches
                            WHERE user_id=%s AND enabled=TRUE
                        ) AS search_count
                ),
                event_write AS (
                    INSERT INTO growth_events (user_id, name, props)
                    SELECT
                        %s,
                        'p10_hub_view',
                        jsonb_build_object(
                            'language', %s::text,
                            'searches', snapshot.search_count
                        )
                    FROM snapshot
                    RETURNING id
                )
                SELECT onboarding_done, alerts_enabled, digest_enabled, search_count
                FROM snapshot
                """,
                (
                    int(user_id),
                    int(user_id),
                    int(user_id),
                    int(user_id),
                    int(user_id),
                    str(language or "ru"),
                ),
            )
            row = cur.fetchone()
        if not row:
            return {
                "onboarding_done": False,
                "alerts_enabled": False,
                "digest_enabled": False,
                "search_count": 0,
            }
        return {
            "onboarding_done": bool(row[0]),
            "alerts_enabled": bool(row[1]),
            "digest_enabled": bool(row[2]),
            "search_count": int(row[3] or 0),
        }


class _SearchCount:
    def __init__(self, count: int):
        self.count = max(0, int(count))

    def __len__(self) -> int:
        return self.count


class JobBot(base.JobBot):
    def _category_keyboard_from_enabled(self, lang: str, enabled_categories):
        enabled = set(enabled_categories or [])
        categories = list(CATEGORY_NAMES[lang].keys())
        rows = []
        for index in range(0, len(categories), 2):
            row = []
            for category in categories[index:index + 2]:
                mark = "✅" if category in enabled else "▫️"
                row.append(
                    core.InlineKeyboardButton(
                        f"{mark} {CATEGORY_NAMES[lang].get(category, category)}",
                        callback_data=f"toggle_cat:{category}",
                    )
                )
            rows.append(row)
        rows.append(
            [core.InlineKeyboardButton(t(lang, "next_salary"), callback_data="setup_next_salary")]
        )
        return core.InlineKeyboardMarkup(rows)

    async def _send_retention_hub(self, message, user_id: int, *, title: str = ""):
        lang = self._lang(user_id)
        snapshot = self.db.fast_hub_snapshot(user_id, lang)
        search_count = int(snapshot.get("search_count") or 0)
        settings = {
            "onboarding_done": bool(snapshot.get("onboarding_done")),
            "alerts_enabled": bool(snapshot.get("alerts_enabled")),
            "digest_enabled": bool(snapshot.get("digest_enabled")),
        }
        onboarding = settings["onboarding_done"]
        intro = title or t(lang, "welcome_ready" if onboarding else "welcome_new")
        status = t(
            lang,
            "status",
            profile=t(lang, "ready" if onboarding else "not_ready"),
            alerts=t(lang, "on" if settings["alerts_enabled"] else "off"),
            daily=t(lang, "on" if settings["digest_enabled"] else "off"),
            searches=search_count,
        )
        explainer = t(lang, "alerts_explain") + "\n\n" + t(lang, "daily_explain")
        await message.reply_text(
            f"{intro}\n\n{status}\n\n{explainer}",
            reply_markup=self._retention_keyboard_from_state(
                lang, settings, _SearchCount(search_count)
            ),
            disable_web_page_preview=True,
        )

    async def _start_setup_from_callback(self, query, user_id: int) -> None:
        lang = self._lang(user_id)
        enabled = self.db.fast_begin_setup(user_id, lang)
        await query.message.reply_text(
            t(lang, "setup1"),
            reply_markup=self._category_keyboard_from_enabled(lang, enabled),
        )

    async def cmd_setup(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        language = self.db.get_user_language(user_id)
        if not language:
            await update.message.reply_text(
                t("ru", "choose_language"), reply_markup=self._language_keyboard()
            )
            return
        self._set_cached_language(user_id, language)
        enabled = self.db.fast_begin_setup(user_id, language)
        await update.message.reply_text(
            t(language, "setup1"),
            reply_markup=self._category_keyboard_from_enabled(language, enabled),
        )

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
            skills = "" if raw in {"-", "—", "skip", "нет"} else raw[:200]
            self.db.fast_setup_value_and_step(
                user_id,
                field="skills",
                value=skills,
                next_step="digest",
            )
            await update.message.reply_text(
                t(lang, "setup4"),
                reply_markup=core.InlineKeyboardMarkup(
                    [[
                        core.InlineKeyboardButton(
                            t(lang, "yes_daily"), callback_data="setup_digest_on"
                        ),
                        core.InlineKeyboardButton(
                            t(lang, "no_daily"), callback_data="setup_digest_off"
                        ),
                    ]]
                ),
            )
            return

        return await super().handle_setup_text(update, context)

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

        if data == "growth_quick_setup":
            await self._ack_callback(query)
            await self._start_setup_from_callback(query, user_id)
            return

        if data.startswith("toggle_cat:") and data != "toggle_cat:":
            await self._ack_callback(query)
            lang = self._lang(user_id)
            category = data.split(":", 1)[1]
            settings = self.db.get_user_settings(user_id)
            enabled = list(settings.get("enabled_categories") or [])
            if category in enabled:
                enabled.remove(category)
            else:
                enabled.append(category)
            self.db.save_user_settings(user_id, {"enabled_categories": enabled})
            await query.edit_message_reply_markup(
                reply_markup=self._category_keyboard_from_enabled(lang, enabled)
            )
            return

        if data == "setup_next_salary":
            await self._ack_callback(query)
            lang = self._lang(user_id)
            self.db.set_runtime_state(
                user_id,
                "setup_step",
                {"step": "salary"},
                ttl_seconds=24 * 60 * 60,
            )
            await query.edit_message_text(t(lang, "setup2"))
            return

        if data in {"setup_digest_on", "setup_digest_off"}:
            await self._ack_callback(query)
            lang = self._lang(user_id)
            enabled = data == "setup_digest_on"
            self.db.fast_finish_setup(user_id, enabled, lang)
            await query.edit_message_text(t(lang, "profile_ready"))
            await self._send_retention_hub(query.message, user_id)
            return

        if data == "p8_retention_hub":
            await self._ack_callback(query)
            await self._send_retention_hub(query.message, user_id)
            return

        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
