import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import channel_bot as core
import scheduled_delivery_runtime as delivery


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.rows = []
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        sql = " ".join(str(query).split()).lower()
        self.rows = []
        self.index = 0
        if sql.startswith("insert into growth_delivery_receipts"):
            kind, period, target = map(str, params[:3])
            key = (kind, period, target)
            if key not in self.store.receipts:
                self.store.receipts[key] = {"state": "sending", "count": 0}
                self.rows = [(target,)]
        elif sql.startswith("delete from growth_delivery_receipts"):
            pass
        elif sql.startswith("update growth_delivery_receipts"):
            state, count, kind, period, target = params
            key = (str(kind), str(period), str(target))
            if key in self.store.receipts and self.store.receipts[key]["state"] == "sending":
                self.store.receipts[key] = {"state": str(state), "count": int(count)}
        else:
            raise AssertionError(f"unexpected SQL: {query}")
        return self

    def fetchone(self):
        if self.index >= len(self.rows):
            return None
        row = self.rows[self.index]
        self.index += 1
        return row


class FakeStore:
    def __init__(self):
        self.receipts = {}

    def _ensure_conn(self):
        return self

    def cursor(self):
        return FakeCursor(self)


class FakeDB:
    def __init__(self, store):
        self._growth_store = store
        self.events = []
        self.closed = False

    def recent_jobs_for_digest(self, hours=36, limit=80):
        return [
            {"hash": "a", "title": "Junior Python", "company": "A", "url": "https://example.com/a"},
            {"hash": "b", "title": "QA", "company": "B", "url": "https://example.com/b"},
        ][:limit]

    def list_digest_subscribers(self):
        return [101, 202]

    def log_event(self, user_id, name, props=None):
        self.events.append((user_id, name, props or {}))

    def close(self):
        self.closed = True


class FakeApp:
    def __init__(self):
        self.bot = SimpleNamespace(send_message=AsyncMock())

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeJobBot:
    def __init__(self):
        self.personal_calls = []
        self.weekly_calls = 0

    async def send_personal_digest(self, uid):
        self.personal_calls.append(uid)
        return 2 if uid == 101 else 1

    async def post_weekly_salary_magnet(self):
        self.weekly_calls += 1
        return True


class ScheduledDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = FakeStore()
        self.db = FakeDB(self.store)
        self.job_bot = FakeJobBot()
        self.apps = []

    def runtime_factory(self):
        app = FakeApp()
        self.apps.append(app)
        return app, self.db, self.job_bot

    async def test_daily_delivery_is_at_most_once_per_target_and_period(self):
        with patch.multiple(
            core.Config,
            ENABLE_DAILY_DIGEST=True,
            ENABLE_PERSONAL_DIGEST=True,
            CHANNEL_ID="@channel",
            BOT_USERNAME="junior_jobs_channel_bot",
            DIGEST_MAX_JOBS=7,
            PERSONAL_DIGEST_LOOKBACK_HOURS=36,
        ):
            first = await delivery.run_daily_delivery(runtime_factory=self.runtime_factory)
            second = await delivery.run_daily_delivery(runtime_factory=self.runtime_factory)

        self.assertEqual(first["channel_sent"], 2)
        self.assertEqual(first["personal_targets"], 2)
        self.assertEqual(first["personal_jobs"], 3)
        self.assertEqual(self.job_bot.personal_calls, [101, 202])
        self.apps[0].bot.send_message.assert_awaited_once()
        self.apps[1].bot.send_message.assert_not_awaited()
        self.assertEqual(second["channel_sent"], 0)
        self.assertEqual(second["personal_targets"], 0)
        self.assertEqual(second["skipped_receipts"], 3)

    async def test_weekly_delivery_is_at_most_once_per_period(self):
        with patch.multiple(
            core.Config,
            ENABLE_WEEKLY_SALARY_REPORT=True,
            CHANNEL_ID="@channel",
        ):
            first = await delivery.run_weekly_delivery(runtime_factory=self.runtime_factory)
            second = await delivery.run_weekly_delivery(runtime_factory=self.runtime_factory)

        self.assertTrue(first["posted"])
        self.assertEqual(self.job_bot.weekly_calls, 1)
        self.assertEqual(second["skipped_receipts"], 1)

    def test_receipt_target_key_does_not_store_raw_chat_id(self):
        key = delivery.DeliveryReceiptLedger.target_key(-100123456789)
        self.assertEqual(len(key), 64)
        self.assertNotIn("123456789", key)

    def test_channel_digest_is_plain_bounded_text(self):
        text = delivery._channel_digest_text(
            [{"title": "X" * 500, "company": "C", "url": "javascript:bad"}],
            bot_username="@bot",
        )
        self.assertLessEqual(len(text), 4000)
        self.assertNotIn("javascript:bad", text)
        self.assertIn("https://t.me/bot?start=invite", text)


if __name__ == "__main__":
    unittest.main()
