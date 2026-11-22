"""Exercise the production handler builder without DB or Telegram network."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import channel_bot as core
import copilot_webhook_runtime as runtime
import interactive_webhook_runtime as base
import localized_product_runtime_v8 as localized
from telegram.ext import CommandHandler, MessageHandler


class RuntimeStartupTests(unittest.TestCase):
    def test_real_builder_registers_cv_resume_and_document_handler(self):
        db = SimpleNamespace(growth_backend="postgres")
        env = {"TELEGRAM_BOT_TOKEN": "123456:dummy_token_for_offline_tests_12345", "CHANNEL_ID": "-1001234567890"}
        with patch.dict(os.environ, env), patch.object(base, "_runtime", return_value=(localized, core)), patch.object(localized, "DatabaseConnection", return_value=db):
            app, actual_db, bot = runtime.build_runtime()

        self.assertIs(actual_db, db)
        handlers = app.handlers[0]
        commands = [h for h in handlers if isinstance(h, CommandHandler)]
        resume = [h for h in commands if "resume" in h.commands]
        self.assertEqual(len(resume), 1)
        self.assertEqual(resume[0].callback.__name__, "_on_resume")
        self.assertEqual(next(h for h in commands if "unpause" in h.commands).callback, bot.cmd_resume)
        self.assertEqual(next(h for h in commands if "start" in h.commands).callback, bot.cmd_start)
        self.assertEqual(next(h for h in commands if "cancel_feedback" in h.commands).callback, bot.cmd_cancel_feedback)
        self.assertTrue(any(isinstance(h, MessageHandler) and h.callback == bot.handle_setup_text for h in handlers))
