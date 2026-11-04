"""Unit tests for the owner-feedback flow.

Hermetic style as test_resume_profile_flow: no real Postgres, no real
Telegram; the bot object records sent messages and forwarded originals.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import feedback_flow as flow

USER_ID = 777


class FakeDb:
    def __init__(self):
        self.events = []
        self.runtime_state = {}

    def log_event(self, user_id, name, props=None):
        self.events.append((int(user_id or 0), name, dict(props or {})))

    def get_runtime_state(self, user_id, key):
        return self.runtime_state.get((int(user_id), key))

    def set_runtime_state(self, user_id, key, payload, ttl_seconds=None):
        self.runtime_state[(int(user_id), key)] = dict(payload or {})

    def delete_runtime_state(self, user_id, key):
        self.runtime_state.pop((int(user_id), key), None)


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self.forwarded = []
        self.fail = fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=1)

    async def forward_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("telegram down")
        self.forwarded.append(kwargs)
        return SimpleNamespace(message_id=2)


class FakeMessage:
    def __init__(self, text="", document=None, caption=None, chat_id=USER_ID, message_id=99):
        self.text = text
        self.document = document
        self.caption = caption
        self.chat_id = chat_id
        self.message_id = message_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def _update(message=None, user_id=USER_ID, username="ivan", first="Иван", last="Петров"):
    user = SimpleNamespace(id=user_id, username=username, first_name=first, last_name=last)
    return SimpleNamespace(
        effective_user=user,
        message=message or FakeMessage(),
    )


def _job_bot(bot=None):
    job_bot = SimpleNamespace(db=FakeDb())
    job_bot.bot = bot  # not used by the flow; context.bot is the transport
    return job_bot


def _context(bot):
    return SimpleNamespace(bot=bot)


class FeedbackFlowTests(unittest.TestCase):
    def test_feedback_command_arms_capture_and_prompts(self):
        job_bot = _job_bot()
        message = FakeMessage()
        with patch.dict(os.environ, {}, clear=False):
            import asyncio
            asyncio.run(flow.cmd_feedback(job_bot, _update(message), _context(FakeBot())))
        self.assertIn(USER_ID, flow._pending(job_bot))
        self.assertIn("отзыв", message.replies[0].lower())
        self.assertEqual(job_bot.db.events[-1][1], "feedback_started")

    def test_pending_text_is_forwarded_with_attribution_and_confirmed(self):
        job_bot = _job_bot()
        bot = FakeBot()
        message = FakeMessage(text="Всё сломалось, помогите")
        update = _update(message)
        flow._pending(job_bot)[USER_ID] = {"v": 1}

        import asyncio
        consumed = asyncio.run(flow.capture_if_pending(job_bot, update, _context(bot)))

        self.assertTrue(consumed)
        self.assertEqual(len(bot.sent), 1)  # header
        self.assertIn("Иван Петров", bot.sent[0]["text"])
        self.assertIn("@ivan", bot.sent[0]["text"])
        self.assertEqual(len(bot.forwarded), 1)  # original message
        self.assertEqual(bot.forwarded[0]["message_id"], 99)
        self.assertNotIn(USER_ID, flow._pending(job_bot))  # disarmed
        self.assertIn("Передал", message.replies[0])
        self.assertEqual(job_bot.db.events[-1][1], "feedback_sent")

    def test_pending_document_is_forwarded_not_downloaded(self):
        job_bot = _job_bot()
        bot = FakeBot()
        document = SimpleNamespace(file_id="f1", file_name="bug.pdf")
        message = FakeMessage(text="", document=document)
        flow._pending(job_bot)[USER_ID] = {"v": 1}

        import asyncio
        consumed = asyncio.run(
            flow.capture_if_pending(job_bot, _update(message), _context(bot))
        )

        self.assertTrue(consumed)
        self.assertEqual(len(bot.forwarded), 1)
        self.assertNotIn(USER_ID, flow._pending(job_bot))

    def test_without_pending_nothing_is_touched(self):
        job_bot = _job_bot()
        bot = FakeBot()
        message = FakeMessage(text="просто текст")
        import asyncio
        consumed = asyncio.run(flow.capture_if_pending(job_bot, _update(message), _context(bot)))
        self.assertFalse(consumed)
        self.assertEqual(bot.sent, [])
        self.assertEqual(bot.forwarded, [])
        self.assertEqual(message.replies, [])

    def test_delivery_failure_keeps_pending_and_warns(self):
        job_bot = _job_bot()
        bot = FakeBot(fail=True)
        message = FakeMessage(text="проблема")
        flow._pending(job_bot)[USER_ID] = {"v": 1}
        import asyncio
        consumed = asyncio.run(flow.capture_if_pending(job_bot, _update(message), _context(bot)))
        self.assertTrue(consumed)
        self.assertIn(USER_ID, flow._pending(job_bot))  # still armed, user can retry
        self.assertIn("сбой", message.replies[0])

    def test_disabled_flag_disarms_and_refuses(self):
        job_bot = _job_bot()
        bot = FakeBot()
        message = FakeMessage(text="отзыв")
        flow._pending(job_bot)[USER_ID] = {"v": 1}
        import asyncio
        with patch.dict(os.environ, {"FEEDBACK_ENABLED": "0"}):
            consumed = asyncio.run(
                flow.capture_if_pending(job_bot, _update(message), _context(bot))
            )
        self.assertTrue(consumed)
        self.assertNotIn(USER_ID, flow._pending(job_bot))
        self.assertEqual(bot.sent, [])
        self.assertIn("недоступн", message.replies[0])

    def test_cancel_command_disarms_without_sending(self):
        job_bot = _job_bot()
        bot = FakeBot()
        message = FakeMessage()
        flow._pending(job_bot)[USER_ID] = {"v": 1}
        import asyncio
        asyncio.run(flow.cmd_cancel(job_bot, _update(message), _context(bot)))
        self.assertNotIn(USER_ID, flow._pending(job_bot))
        self.assertEqual(bot.sent, [])
        self.assertEqual(bot.forwarded, [])

    def test_owner_chat_defaults_and_env_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(flow.owner_chat(), "@freeveol")
        with patch.dict(os.environ, {"FEEDBACK_OWNER_CHAT": "@someone_else"}, clear=True):
            self.assertEqual(flow.owner_chat(), "@someone_else")

    def test_feedback_disabled_blocks_the_command_itself(self):
        job_bot = _job_bot()
        message = FakeMessage()
        import asyncio
        with patch.dict(os.environ, {"FEEDBACK_ENABLED": "false"}):
            asyncio.run(flow.cmd_feedback(job_bot, _update(message), _context(FakeBot())))
        self.assertNotIn(USER_ID, flow._pending(job_bot))
        self.assertIn("недоступн", message.replies[0])


if __name__ == "__main__":
    unittest.main()
