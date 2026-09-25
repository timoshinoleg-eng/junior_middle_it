import os
import unittest
from unittest.mock import patch

from api import telegram_branding_admin as branding


class TelegramBrandingSafetyTests(unittest.TestCase):
    def test_branding_never_posts_intro_when_pin_is_missing(self):
        calls = []

        def fake_call(_token, method, _payload=None):
            calls.append(method)
            if method == "getChat":
                return True, {"pinned_message": {"message_id": 4174, "text": "legacy"}}, ""
            if method == "getMyDescription":
                return True, {"description": branding.BOT_DESCRIPTION}, ""
            if method == "getMyShortDescription":
                return True, {"short_description": branding.BOT_SHORT_DESCRIPTION}, ""
            return True, {}, ""

        env = {
            "VERCEL_ENV": "production",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "CHANNEL_ID": "@junior_middle_it",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            branding, "_try_call", side_effect=fake_call
        ):
            result = branding._apply_branding()

        self.assertNotIn("sendMessage", calls)
        self.assertNotIn("pinChatMessage", calls)
        self.assertEqual(result["pin_post_status"], "disabled")
        self.assertFalse(result["pinned_post_ok"])
        self.assertTrue(result["automated_ok"])

    def test_cleanup_uses_fixed_duplicate_allowlist(self):
        calls = []

        def fake_call(_token, method, payload=None):
            calls.append((method, payload))
            return True, {}, ""

        with patch.object(branding, "_try_call", side_effect=fake_call):
            result = branding._cleanup_approved_duplicates("test-token", "@junior_middle_it")

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 51)
        self.assertEqual(calls[0][1]["message_id"], 7493)
        self.assertEqual(calls[-1][1]["message_id"], 7574)
        self.assertTrue(all(method == "deleteMessage" for method, _ in calls))


if __name__ == "__main__":
    unittest.main()
