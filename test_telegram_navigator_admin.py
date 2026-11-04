import os
import unittest
from unittest.mock import patch

from api import telegram_navigator_admin as navigator


class _FakeStore:
    def __init__(self, state=None, fail_save=False):
        self.state = dict(state or {})
        self.fail_save = fail_save
        self.saved = []

    def get_channel_state(self, _key):
        return dict(self.state)

    def save_channel_state(self, _key, state):
        if self.fail_save:
            raise RuntimeError("db down")
        self.saved.append(dict(state))
        self.state.update(state)
        return True

    def close(self):
        pass


def _patch_store(store):
    return patch.object(navigator, "_build_growth_store", return_value=store)


def _ok_call(script):
    """Build a fake ``_try_call`` driven by a dict of scripted responses."""

    def fake_call(_token, method, _payload=None):
        scripted = script.get(method)
        if scripted is None:
            return True, {}, ""
        if isinstance(scripted, list):
            item = scripted.pop(0)
        else:
            item = scripted
        if isinstance(item, tuple):
            return item
        return True, item, ""

    return fake_call


_ENV = {
    "VERCEL_ENV": "production",
    "TELEGRAM_BOT_TOKEN": "test-token",
    "CHANNEL_ID": "@junior_middle_it",
    "BOT_USERNAME": "test_bot",
    "GROWTH_DATABASE_URL": "postgresql://test",
}


class TelegramNavigatorAdminTests(unittest.TestCase):
    def test_no_pin_right_never_posts(self):
        """Regression: a bot without the pin right must not spam the channel."""
        store = _FakeStore()
        script = {
            "getMe": {"id": 42},
            "getChatMember": {
                "status": "administrator",
                "can_pin_message": False,
                "can_edit_messages": False,
            },
        }
        calls = []

        def fake_call(token, method, payload=None):
            calls.append(method)
            return _ok_call(script)(token, method, payload)

        with patch.dict(os.environ, _ENV, clear=False), _patch_store(store), patch.object(
            navigator, "_try_call", side_effect=fake_call
        ):
            result = navigator._apply_navigator()

        self.assertNotIn("sendMessage", calls)
        self.assertNotIn("pinChatMessage", calls)
        self.assertFalse(result["can_pin_message"])
        self.assertFalse(result["posted_new"])
        self.assertFalse(result["automated_ok"])

    def test_channel_edit_right_counts_as_pin_capability(self):
        """Channels have no can_pin_messages right; can_edit_messages is the
        Telegram right that governs pinning there (Bot API pinChatMessage)."""
        store = _FakeStore()
        script = {
            "getMe": {"id": 42},
            "getChatMember": {"status": "administrator", "can_edit_messages": True},
            "getChat": [
                (True, {"pinned_message": {"message_id": 4174, "text": "legacy pin"}}, ""),
                (True, {"pinned_message": {"message_id": 9001, "text": _pinned_navigator_text()}}, ""),
            ],
            "sendMessage": {"message_id": 9001},
            "pinChatMessage": True,
        }
        with patch.dict(os.environ, _ENV, clear=False), _patch_store(store), patch.object(
            navigator, "_try_call", side_effect=_ok_call(script)
        ):
            result = navigator._apply_navigator()

        self.assertTrue(result["can_pin_message"])
        self.assertTrue(result["posted_new"])
        self.assertTrue(result["automated_ok"])

    def test_fresh_post_saves_state_and_pins(self):
        store = _FakeStore()
        pinned_text = navigator.__doc__  # unused, keep flake quiet
        script = {
            "getMe": {"id": 42},
            "getChatMember": {"status": "administrator", "can_pin_message": True},
            "getChat": [
                (True, {"pinned_message": {"message_id": 4174, "text": "legacy pin"}}, ""),
                (True, {"pinned_message": {"message_id": 9001, "text": _pinned_navigator_text()}}, ""),
            ],
            "sendMessage": {"message_id": 9001},
            "pinChatMessage": True,
        }
        with patch.dict(os.environ, _ENV, clear=False), _patch_store(store), patch.object(
            navigator, "_try_call", side_effect=_ok_call(script)
        ):
            result = navigator._apply_navigator()

        self.assertTrue(result["posted_new"])
        self.assertTrue(result["navigator_post_ok"])
        self.assertTrue(result["automated_ok"])
        self.assertEqual(result["post_message_id"], 9001)
        self.assertEqual(store.state.get("post_message_id"), 9001)

    def test_repin_reuses_stored_message_without_posting(self):
        store = _FakeStore({"post_message_id": 5555})
        calls = []
        script = {
            "getMe": {"id": 42},
            "getChatMember": {"status": "administrator", "can_pin_message": True},
            "getChat": [
                (True, {"pinned_message": {"message_id": 4174, "text": "legacy"}}, ""),
                (True, {"pinned_message": {"message_id": 5555, "text": _pinned_navigator_text()}}, ""),
            ],
            "pinChatMessage": True,
        }

        def fake_call(token, method, payload=None):
            calls.append((method, payload))
            return _ok_call(script)(token, method, payload)

        with patch.dict(os.environ, _ENV, clear=False), _patch_store(store), patch.object(
            navigator, "_try_call", side_effect=fake_call
        ):
            result = navigator._apply_navigator()

        self.assertFalse(result["posted_new"])
        self.assertEqual(result["pin_post_status"], "repinned")
        self.assertTrue(result["automated_ok"])
        self.assertNotIn("sendMessage", [m for m, _ in calls])
        pin_payloads = [p for m, p in calls if m == "pinChatMessage"]
        self.assertEqual(pin_payloads[0]["message_id"], 5555)

    def test_deleted_stored_message_triggers_single_repost(self):
        store = _FakeStore({"post_message_id": 5555})
        script = {
            "getMe": {"id": 42},
            "getChatMember": {"status": "administrator", "can_pin_message": True},
            "getChat": [
                (True, {"pinned_message": {"message_id": 4174, "text": "legacy"}}, ""),
                (True, {"pinned_message": {"message_id": 9002, "text": _pinned_navigator_text()}}, ""),
            ],
            "pinChatMessage": [
                (False, None, "Bad Request: message to pin not found"),
                True,
            ],
            "sendMessage": {"message_id": 9002},
        }
        with patch.dict(os.environ, _ENV, clear=False), _patch_store(store), patch.object(
            navigator, "_try_call", side_effect=_ok_call(script)
        ):
            result = navigator._apply_navigator()

        self.assertTrue(result["posted_new"])
        self.assertEqual(result["post_message_id"], 9002)
        self.assertTrue(result["navigator_post_ok"])
        self.assertEqual(store.state.get("post_message_id"), 9002)

    def test_existing_navigator_pin_is_left_untouched(self):
        store = _FakeStore()
        calls = []
        script = {
            "getMe": {"id": 42},
            "getChatMember": {"status": "administrator", "can_pin_message": True},
            "getChat": (True, {"pinned_message": {"message_id": 7777, "text": _pinned_navigator_text()}}, ""),
        }

        def fake_call(token, method, payload=None):
            calls.append(method)
            return _ok_call(script)(token, method, payload)

        with patch.dict(os.environ, _ENV, clear=False), _patch_store(store), patch.object(
            navigator, "_try_call", side_effect=fake_call
        ):
            result = navigator._apply_navigator()

        self.assertEqual(result["pin_post_status"], "existing")
        self.assertTrue(result["automated_ok"])
        self.assertNotIn("sendMessage", calls)
        self.assertNotIn("pinChatMessage", calls)

    def test_missing_state_backend_aborts_before_any_post(self):
        calls = []
        script = {
            "getMe": {"id": 42},
            "getChatMember": {"status": "administrator", "can_pin_message": True},
        }

        def fake_call(token, method, payload=None):
            calls.append(method)
            return _ok_call(script)(token, method, payload)

        with patch.dict(os.environ, _ENV, clear=False), patch.object(
            navigator, "_build_growth_store", side_effect=RuntimeError("growth_dsn_missing")
        ), patch.object(navigator, "_try_call", side_effect=fake_call):
            with self.assertRaises(RuntimeError) as ctx:
                navigator._apply_navigator()

        self.assertIn("navigator_state_unavailable", str(ctx.exception))
        self.assertNotIn("sendMessage", calls)
        self.assertNotIn("pinChatMessage", calls)


def _pinned_navigator_text():
    from channel_navigator import NAVIGATOR_TITLE

    return NAVIGATOR_TITLE


if __name__ == "__main__":
    unittest.main()
