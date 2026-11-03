"""Unit tests for onboarding_flow: channel deep links, lead magnet, Stars payments.

The tests use the same FakeDb/FakeMessage pattern as test_copilot_runtime and
never require a real Postgres: CopilotStore.from_database is patched out.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import onboarding_flow


JOB = {
    "hash": "b" * 40,
    "title": "Middle Python Developer",
    "company": "Acme",
    "description": "Python, FastAPI. Опыт 3 года. Вопросы по вакансии пишите @hr_ivan",
    "url": "https://example.com/jobs/2",
    "source": "TG:@middle_dev",
}

USER_ID = 777


class FakeDb:
    def __init__(self, job=None):
        self.job = job
        self.events = []
        self.settings = {}
        self.favorites = []
        self.grants = []

    def get_job_payload(self, job_hash):
        if self.job and str(job_hash) == str(self.job.get("hash")):
            return dict(self.job)
        return None

    def get_user_settings(self, user_id):
        return dict(self.settings)

    def save_user_settings(self, user_id, values):
        self.settings.update(values or {})

    def log_event(self, user_id, name, props=None):
        self.events.append((name, dict(props or {})))

    def add_favorite(self, user_id, job_hash):
        self.favorites.append((user_id, str(job_hash)))
        return True


class FakeStore:
    def __init__(self):
        self.rows = []

    def ensure_credit_row(self, user_id):
        self.rows.append(("ensure", user_id))

    def grant_credits_with_event(self, user_id, amount, reason, ref=""):
        self.rows.append(("grant", user_id, amount, reason, ref))
        return True


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeMember:
    def __init__(self, status):
        self.status = status


class FakeBotApi:
    """Stands in for ``context.bot`` (PTB Bot)."""

    def __init__(self, member_status="member"):
        self.username = "test_bot"
        self.member_status = member_status
        self.invoices = []
        self.member_requests = []

    async def get_chat_member(self, chat_id=None, user_id=None):
        self.member_requests.append((chat_id, user_id))
        if self.member_status == "error":
            raise RuntimeError("telegram api down")
        return FakeMember(self.member_status)

    async def send_invoice(self, **kwargs):
        self.invoices.append(kwargs)
        return SimpleNamespace(message_id=1)


class FakePrecheckoutQuery:
    def __init__(self, payload):
        self.invoice_payload = payload
        self.answers = []

    async def answer(self, ok=False, error_message=None):
        self.answers.append({"ok": ok, "error_message": error_message})


def make_update(message=None, query=None, pre_checkout=None, payment=None):
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
        message=message or FakeMessage(),
        callback_query=query,
        pre_checkout_query=pre_checkout,
    )
    if payment is not None:
        update.message.successful_payment = payment
    return update


def make_context(args=None, bot=None):
    return SimpleNamespace(args=args or [], bot=bot or FakeBotApi())


def run(coro):
    import asyncio

    return asyncio.run(coro)


class FavoritesDeepLinkTests(unittest.TestCase):
    def test_fav_link_saves_favorite_and_confirms(self):
        db = FakeDb(JOB)
        message = FakeMessage()
        context = make_context(args=["fav_" + JOB["hash"]])
        handled = run(
            onboarding_flow.handle_start_extras(SimpleNamespace(db=db), make_update(message=message), context)
        )
        self.assertTrue(handled)
        self.assertEqual(db.favorites, [(USER_ID, JOB["hash"])])
        self.assertTrue(message.replies)
        text, kwargs = message.replies[0]
        self.assertIn("Избранное", text)
        keyboard = kwargs["reply_markup"].inline_keyboard
        button_urls = [btn.url for row in keyboard for btn in row]
        self.assertTrue(any("start=copilot_" + JOB["hash"] in url for url in button_urls))
        self.assertTrue(any(name == "onboarding_favorite_added" for name, _ in db.events))

    def test_fav_link_with_unknown_hash_is_reported(self):
        db = FakeDb(None)
        message = FakeMessage()
        context = make_context(args=["fav_unknown"])
        handled = run(
            onboarding_flow.handle_start_extras(SimpleNamespace(db=db), make_update(message=message), context)
        )
        self.assertTrue(handled)
        self.assertEqual(db.favorites, [])
        self.assertIn("устарела", message.replies[0][0])

    def test_foreign_payload_is_not_intercepted(self):
        db = FakeDb(JOB)
        context = make_context(args=["ref_123"])
        handled = run(
            onboarding_flow.handle_start_extras(SimpleNamespace(db=db), make_update(), context)
        )
        self.assertFalse(handled)


class LeadMagnetTests(unittest.TestCase):
    def _claim(self, db, bot):
        message = FakeMessage()
        context = make_context(args=["magnet"], bot=bot)
        with patch.object(onboarding_flow.core.Config, "CHANNEL_ID", "@test_channel"):
            handled = run(
                onboarding_flow.handle_start_extras(SimpleNamespace(db=db), make_update(message=message), context)
            )
        self.assertTrue(handled)
        return message

    def test_unsubscribed_user_gets_subscribe_prompt(self):
        db = FakeDb(JOB)
        message = self._claim(db, FakeBotApi(member_status="left"))
        text, kwargs = message.replies[0]
        self.assertIn("подпишись", text.lower())
        buttons = [btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row]
        self.assertIn(onboarding_flow.CB_CLAIM_MAGNET, buttons)
        self.assertEqual(db.grants, [])

    def test_subscribed_user_gets_bonus_and_guide(self):
        db = FakeDb(JOB)
        store = FakeStore()
        with patch.object(onboarding_flow.CopilotStore, "from_database", return_value=store):
            message = self._claim(db, FakeBotApi(member_status="member"))
        self.assertTrue(any(row[0] == "grant" and row[2] == 3 for row in store.rows))
        self.assertTrue(db.settings.get("magnet_claimed"))
        self.assertIn("ATS", message.replies[-1][0])
        self.assertTrue(any(name == "magnet_claimed" for name, _ in db.events))

    def test_second_claim_does_not_grant_twice(self):
        db = FakeDb(JOB)
        db.settings["magnet_claimed"] = True
        store = FakeStore()
        with patch.object(onboarding_flow.CopilotStore, "from_database", return_value=store):
            self._claim(db, FakeBotApi(member_status="member"))
        self.assertEqual([row for row in store.rows if row[0] == "grant"], [])

    def test_api_error_does_not_block_the_guide(self):
        db = FakeDb(JOB)
        message = self._claim(db, FakeBotApi(member_status="error"))
        self.assertIn("ATS", message.replies[-1][0])

    def test_claim_callback_reruns_the_check(self):
        db = FakeDb(JOB)
        query = SimpleNamespace(data=onboarding_flow.CB_CLAIM_MAGNET, message=FakeMessage())
        update = make_update(query=query)
        context = make_context(bot=FakeBotApi(member_status="left"))
        with patch.object(onboarding_flow.core.Config, "CHANNEL_ID", "@test_channel"):
            handled = run(
                onboarding_flow.handle_callback_extras(SimpleNamespace(db=db), update, context)
            )
        self.assertTrue(handled)
        self.assertIn("подпишись", update.message.replies[0][0].lower())


class StarsPaymentTests(unittest.TestCase):
    def test_stars_command_sends_xtr_invoice(self):
        db = FakeDb(JOB)
        bot = FakeBotApi()
        context = make_context(args=[], bot=bot)
        run(onboarding_flow.cmd_stars(SimpleNamespace(db=db), make_update(), context))
        self.assertEqual(len(bot.invoices), 1)
        invoice = bot.invoices[0]
        self.assertEqual(invoice["currency"], "XTR")
        self.assertEqual(invoice["provider_token"], "")
        self.assertEqual(invoice["payload"], f"stars_topup_{USER_ID}")
        self.assertEqual(invoice["prices"][0].amount, onboarding_flow.STARS_PRICE_XTR)

    def test_pre_checkout_accepts_only_own_payload(self):
        good = FakePrecheckoutQuery(f"stars_topup_{USER_ID}")
        run(onboarding_flow.precheckout_handler(make_update(pre_checkout=good), SimpleNamespace()))
        self.assertEqual(good.answers, [{"ok": True, "error_message": None}])

        bad = FakePrecheckoutQuery("evil_payload")
        run(onboarding_flow.precheckout_handler(make_update(pre_checkout=bad), SimpleNamespace()))
        self.assertFalse(bad.answers[0]["ok"])

    def test_successful_payment_grants_credits_once(self):
        db = FakeDb(JOB)
        store = FakeStore()
        payment = SimpleNamespace(telegram_payment_charge_id="chg_1")
        update = make_update(payment=payment)
        context = make_context()
        with patch.object(onboarding_flow.CopilotStore, "from_database", return_value=store):
            run(onboarding_flow.successful_payment_handler(SimpleNamespace(db=db), update, context))
        self.assertTrue(any(row[0] == "grant" and row[2] == 10 for row in store.rows))
        self.assertIn("chg_1", db.settings["stars_charge_ids"])
        self.assertIn("🌟", update.message.replies[0][0])

        store.rows.clear()
        update2 = make_update(payment=payment)
        with patch.object(onboarding_flow.CopilotStore, "from_database", return_value=store):
            run(onboarding_flow.successful_payment_handler(SimpleNamespace(db=db), update2, context))
        self.assertEqual(store.rows, [])

    def test_buy_credits_callback_sends_invoice(self):
        db = FakeDb(JOB)
        bot = FakeBotApi()
        query = SimpleNamespace(data=onboarding_flow.CB_BUY_CREDITS, message=FakeMessage())
        update = make_update(query=query)
        handled = run(
            onboarding_flow.handle_callback_extras(SimpleNamespace(db=db), update, make_context(bot=bot))
        )
        self.assertTrue(handled)
        self.assertEqual(len(bot.invoices), 1)

    def test_foreign_callback_is_not_intercepted(self):
        db = FakeDb(JOB)
        query = SimpleNamespace(data="copilot_hr:abc", message=FakeMessage())
        handled = run(
            onboarding_flow.handle_callback_extras(SimpleNamespace(db=db), make_update(query=query), make_context())
        )
        self.assertFalse(handled)


class RuntimeChainIntegrationTests(unittest.TestCase):
    """The webhook runtime delegates to onboarding_flow without breaking ref_."""

    def test_start_chain_handles_fav_payload(self):
        from unittest.mock import AsyncMock

        import copilot_webhook_runtime as copilot_runtime

        db = FakeDb(JOB)
        message = FakeMessage()
        context = make_context(args=["fav_" + JOB["hash"]])
        original = AsyncMock()
        with patch.object(copilot_runtime, "_original_cmd_start", new=original):
            run(copilot_runtime._copilot_cmd_start(SimpleNamespace(db=db), make_update(message=message), context))
        self.assertEqual(db.favorites, [(USER_ID, JOB["hash"])])
        original.assert_not_awaited()

    def test_start_chain_keeps_referral_payload(self):
        from unittest.mock import AsyncMock

        import copilot_webhook_runtime as copilot_runtime

        db = FakeDb(JOB)
        message = FakeMessage()
        context = make_context(args=["ref_4242"])
        original = AsyncMock()
        with patch.object(copilot_runtime, "_original_cmd_start", new=original):
            run(copilot_runtime._copilot_cmd_start(SimpleNamespace(db=db), make_update(message=message), context))
        original.assert_awaited_once()

    def test_callback_chain_handles_obn_prefix(self):
        from unittest.mock import AsyncMock

        import copilot_webhook_runtime as copilot_runtime

        db = FakeDb(JOB)
        bot = FakeBotApi()
        query = SimpleNamespace(data=onboarding_flow.CB_BUY_CREDITS, message=FakeMessage())
        update = make_update(query=query)
        context = make_context(bot=bot)
        original = AsyncMock()
        with patch.object(copilot_runtime, "_original_handle_callback", new=original):
            run(copilot_runtime._copilot_handle_callback(SimpleNamespace(db=db), update, context))
        self.assertEqual(len(bot.invoices), 1)
        original.assert_not_awaited()

    def test_copilot_callback_still_reaches_copilot_handler(self):
        import copilot_webhook_runtime as copilot_runtime

        db = FakeDb(JOB)
        message = FakeMessage()
        query = SimpleNamespace(data="copilot_hr:" + JOB["hash"], message=message)

        async def _noop(*args, **kwargs):
            return None

        query.answer = _noop
        update = make_update(query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            run(copilot_runtime._copilot_handle_callback(SimpleNamespace(db=db), update, SimpleNamespace()))
        self.assertTrue(message.replies)
        self.assertIn("t.me/hr_ivan", message.replies[0][0])


class EnvironmentContractTests(unittest.TestCase):
    def test_bot_username_falls_back_to_env(self):
        db = FakeDb(JOB)
        bot = FakeBotApi()
        bot.username = ""
        message = FakeMessage()
        context = make_context(args=["fav_" + JOB["hash"]], bot=bot)
        with patch.dict(os.environ, {"TELEGRAM_BOT_USERNAME": "env_bot"}, clear=False):
            run(onboarding_flow.handle_start_extras(SimpleNamespace(db=db), make_update(message=message), context))
        urls = [
            btn.url
            for row in message.replies[0][1]["reply_markup"].inline_keyboard
            for btn in row
            if getattr(btn, "url", None)
        ]
        self.assertTrue(any("t.me/env_bot" in url for url in urls))


if __name__ == "__main__":
    unittest.main()
