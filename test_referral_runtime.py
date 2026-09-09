import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import referral_runtime as rr
from referral_growth import build_referral_share_url, format_top_scores


class ReferralGrowthHelperTests(unittest.TestCase):
    def test_share_url_wraps_personal_referral_link(self):
        url = build_referral_share_url("https://t.me/example_bot?start=ref_42")
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params["url"][0], "https://t.me/example_bot?start=ref_42")
        self.assertIn("Junior/Middle", params["text"][0])

    def test_top_scores_do_not_expose_user_ids(self):
        self.assertEqual(format_top_scores([7, 5, 2]), "#1: 7 · #2: 5 · #3: 2")
        self.assertEqual(format_top_scores([]), "—")


class ReferralDatabaseTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.db = rr.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)

    def _refer(self, invitee: int, referrer: int, activate: bool = False):
        self.assertTrue(self.db.register_referral(invitee, referrer))
        if activate:
            self.db.log_event(invitee, "setup_done", {"source": "test"})

    def test_existing_user_cannot_be_claimed_by_late_referral(self):
        self.db.log_event(101, "start", {"payload": None})
        self.assertFalse(self.db.register_referral(101, 10))
        row = self.db.fetchone("SELECT 1 FROM referrals WHERE user_id=?", (101,))
        self.assertIsNone(row)

    def test_rank_uses_activated_invitees_not_raw_accepts(self):
        self._refer(101, 10, activate=True)
        self._refer(102, 10, activate=True)
        self._refer(201, 20, activate=True)
        self._refer(202, 20, activate=False)
        self._refer(301, 30, activate=False)
        self._refer(302, 30, activate=False)
        self._refer(303, 30, activate=False)

        first = self.db.referral_progress(10, 7)
        second = self.db.referral_progress(20, 7)
        spammy = self.db.referral_progress(30, 7)

        self.assertEqual(first["rank_period"], 1)
        self.assertEqual(first["activated_period"], 2)
        self.assertEqual(first["top_scores"], [2, 1])
        self.assertEqual(second["rank_period"], 2)
        self.assertEqual(second["accepted_period"], 2)
        self.assertEqual(second["activated_period"], 1)
        self.assertIsNone(spammy["rank_period"])
        self.assertEqual(spammy["accepted_period"], 3)
        self.assertEqual(spammy["activated_period"], 0)

    def test_activation_before_referral_does_not_count_for_legacy_row(self):
        now = datetime.now()
        old = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        referred = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            "INSERT INTO events (ts,user_id,name,props) VALUES (?,?,?,?)",
            (old, 401, "setup_done", "{}"),
        )
        # Simulate a legacy graph row created by the previous permissive logic.
        self.db.execute(
            "INSERT INTO referrals (user_id,referrer_id,created_at) VALUES (?,?,?)",
            (401, 40, referred),
        )

        stats = self.db.referral_progress(40, 7)
        self.assertEqual(stats["accepted_period"], 1)
        self.assertEqual(stats["activated_period"], 0)
        self.assertIsNone(stats["rank_period"])

    def test_global_referral_funnel_is_quality_weighted(self):
        self._refer(501, 50, activate=True)
        self._refer(502, 50, activate=False)
        self._refer(601, 60, activate=True)
        stats = self.db.referral_funnel(7)
        self.assertEqual(stats["accepted"], 3)
        self.assertEqual(stats["activated"], 2)
        self.assertEqual(stats["activation_pct"], 66.7)
        self.assertEqual(stats["referrers"], 2)
        self.assertEqual(stats["active_referrers"], 2)
        self.assertEqual(stats["top_score"], 1)


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


class ReferralBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.db = rr.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)
        self.bot = rr.JobBot(SimpleNamespace(bot=SimpleNamespace()), self.db)

    async def test_ref_command_shows_private_rank_and_share_button(self):
        self.assertTrue(self.db.register_referral(701, 70))
        self.db.log_event(701, "setup_done", {})
        message = FakeMessage()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=70), message=message)

        with patch.object(rr.core.Config, "BOT_USERNAME", "junior_middle_bot"):
            await self.bot.cmd_ref(update, SimpleNamespace(args=[]))

        text, kwargs = message.calls[-1]
        self.assertIn("Referral 2.0", text)
        self.assertIn("Позиция недели: #1", text)
        keyboard = kwargs["reply_markup"].inline_keyboard
        share = keyboard[0][0]
        self.assertEqual(share.text, "📤 Пригласить друга")
        self.assertTrue(share.url.startswith("https://t.me/share/url?"))


if __name__ == "__main__":
    unittest.main()
