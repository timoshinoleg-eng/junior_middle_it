"""Tests for the pinned channel navigator post and its admin endpoint."""
import os
import unittest
from unittest.mock import patch

import channel_navigator
from channel_navigator import (
    NAVIGATOR_TITLE,
    build_navigator_post,
    navigator_reply_markup,
    navigator_text,
)


class NavigatorPostTests(unittest.TestCase):
    def test_text_starts_with_title_and_covers_key_features(self):
        text = navigator_text("test_bot")
        self.assertTrue(text.startswith(f"<b>{NAVIGATOR_TITLE}</b>"))
        for snippet in ("Разбор вакансии", "Проверка резюме", "Избранное", "+3 AI-кредита"):
            self.assertIn(snippet, text)

    def test_keyboard_is_url_only_with_expected_deep_links(self):
        markup = navigator_reply_markup("test_bot")
        buttons = [b for row in markup["inline_keyboard"] for b in row]
        self.assertTrue(buttons)
        self.assertTrue(all("url" in b and "callback_data" not in b for b in buttons))
        urls = {b["text"]: b["url"] for b in buttons}
        self.assertEqual(urls["🎯 Мой поиск вакансий"], "https://t.me/test_bot?start=web_home")
        self.assertEqual(urls["🎁 Гайд + 3 кредита"], "https://t.me/test_bot?start=magnet")
        self.assertEqual(urls["💎 Купить AI-кредиты"], "https://t.me/test_bot?start=stars")
        self.assertIn("https://t.me/share/url?", urls["📤 Поделиться каналом"])

    def test_keyboard_without_bot_username_is_empty_but_text_still_works(self):
        self.assertEqual(navigator_reply_markup(""), {"inline_keyboard": []})
        self.assertIn("юзернейм бота", navigator_text(""))

    def test_build_post_returns_text_and_markup(self):
        text, markup = build_navigator_post("@test_bot")
        self.assertIn(NAVIGATOR_TITLE, text)
        self.assertEqual(len(markup["inline_keyboard"]), 2)

    def test_builder_never_imports_message_formatter_chain(self):
        """Vercel's bundler loses the star-import chain at deploy time."""
        from pathlib import Path

        source = Path("channel_navigator.py").read_text(encoding="utf-8")
        self.assertNotIn("import message_formatter", source)
        self.assertNotIn("from message_formatter", source)


class NavigatorAdminTests(unittest.TestCase):
    def _run_apply(self, calls, chat_with_navigator=False, bot_username="test_bot"):
        import api.telegram_navigator_admin as admin

        state = {"pinned": None}
        if chat_with_navigator:
            state["pinned"] = {"message_id": 55, "text": NAVIGATOR_TITLE + "\nold"}

        def fake_call(token, method, payload=None):
            calls.append((method, payload or {}))
            if method == "getChat":
                chat = {}
                if state["pinned"]:
                    chat["pinned_message"] = state["pinned"]
                return chat
            if method == "sendMessage":
                # Real Telegram strips HTML tags from message.text.
                plain = payload["text"].replace("<b>", "").replace("</b>", "")
                state["posted"] = {"message_id": 77, "text": plain}
                return {"message_id": 77}
            if method == "pinChatMessage":
                state["pinned"] = state.get("posted")
                return True
            raise AssertionError(method)

        env = {
            "VERCEL_ENV": "production",
            "TELEGRAM_BOT_TOKEN": "token",
            "CHANNEL_ID": "@chan",
            "BOT_USERNAME": bot_username,
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            admin, "_telegram_call", side_effect=fake_call
        ):
            return admin._apply_navigator()

    def test_first_run_posts_and_pins(self):
        calls = []
        result = self._run_apply(calls)
        methods = [m for m, _ in calls]
        self.assertIn("sendMessage", methods)
        self.assertIn("pinChatMessage", methods)
        self.assertTrue(result["posted_new"])
        self.assertEqual(result["pin_post_status"], "pinned")
        self.assertTrue(result["navigator_post_ok"])
        self.assertTrue(result["automated_ok"])

        sent = next(p for m, p in calls if m == "sendMessage")
        self.assertEqual(sent["chat_id"], "@chan")
        self.assertEqual(sent["parse_mode"], "HTML")
        self.assertIn("inline_keyboard", sent["reply_markup"])
        self.assertTrue(sent["text"].startswith(f"<b>{NAVIGATOR_TITLE}</b>"))

    def test_second_run_is_idempotent(self):
        calls = []
        result = self._run_apply(calls, chat_with_navigator=True)
        methods = [m for m, _ in calls]
        self.assertNotIn("sendMessage", methods)
        self.assertNotIn("pinChatMessage", methods)
        self.assertFalse(result["posted_new"])
        self.assertEqual(result["pin_post_status"], "existing")
        self.assertTrue(result["automated_ok"])
        self.assertEqual(result["pinned_message_id"], 55)

    def test_refuses_outside_production(self):
        import api.telegram_navigator_admin as admin

        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                admin._apply_navigator()
        self.assertEqual(str(ctx.exception), "not_production")


if __name__ == "__main__":
    unittest.main()
