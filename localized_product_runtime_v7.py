"""Final latency-hardened product layer with setup category parity."""
from __future__ import annotations

import channel_bot as core
import localized_product_runtime_v6 as base
from product_i18n import CATEGORY_NAMES


DatabaseConnection = base.DatabaseConnection


class JobBot(base.JobBot):
    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

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

            # Preserve the mature setup contract: an empty category set means
            # "all categories" rather than persisting an unusable empty profile.
            if not enabled:
                enabled = list(CATEGORY_NAMES[lang].keys())

            self.db.save_user_settings(user_id, {"enabled_categories": enabled})
            await query.edit_message_reply_markup(
                reply_markup=self._category_keyboard_from_enabled(lang, enabled)
            )
            return

        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
