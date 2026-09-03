"""Regression tests for Stage 1 (unified state, SQLite interim) — B08/B10/B11.

No network, no live Telegram: Telethon and Bot I/O are fully mocked.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import channel_bot
from channel_bot import (
    Config,
    DatabaseConnection,
    dedup_target_channels,
    get_recent_channel_job_hashes,
    retry_failed_deliveries,
    run_v7_migration,
)


class TempDbTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseConnection(self.path)

    def tearDown(self):
        try:
            self.db.conn.close()
        finally:
            os.unlink(self.path)


class V7MigrationTests(unittest.TestCase):
    def test_migration_upgrades_legacy_schema_and_is_idempotent(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            # Legacy = pre-v7 but post-v6.1 schema (category/fingerprint exist);
            # deliveries / last_seen_at / favorites.status are missing.
            conn.execute(
                "CREATE TABLE posted_jobs (hash TEXT PRIMARY KEY, title TEXT NOT NULL,"
                " company TEXT NOT NULL, level TEXT, url TEXT, source TEXT,"
                " category TEXT DEFAULT 'other', fingerprint TEXT,"
                " posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "CREATE TABLE user_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " user_id INTEGER NOT NULL, job_hash TEXT NOT NULL,"
                " saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                " UNIQUE(user_id, job_hash))"
            )
            conn.commit()
            conn.close()

            db = DatabaseConnection(path)
            self.assertTrue(run_v7_migration(db))
            # second run must not fail (idempotent)
            self.assertTrue(run_v7_migration(db))

            cols = {r[1] for r in db.conn.execute("PRAGMA table_info(posted_jobs)")}
            self.assertIn("last_seen_at", cols)
            fav_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(user_favorites)")}
            self.assertIn("status", fav_cols)
            tables = {r[0] for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("deliveries", tables)
            db.conn.close()
        finally:
            os.unlink(path)


class DeliveryLedgerTests(TempDbTestCase):
    def test_record_and_upsert_attempts(self):
        self.db.record_delivery("h1", "@main", "failed", error="send_error")
        self.db.record_delivery("h1", "@main", "failed", error="send_error")
        rows = self.db.failed_deliveries(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempts"], 2)

    def test_sent_rows_are_not_retried(self):
        self.db.record_delivery("h1", "@main", "sent")
        self.db.record_delivery("h2", "@main", "failed")
        rows = self.db.failed_deliveries(limit=10)
        self.assertEqual([r["job_hash"] for r in rows], ["h2"])

    def test_attempts_capped(self):
        for _ in range(6):
            self.db.record_delivery("h1", "@main", "failed")
        self.assertEqual(self.db.failed_deliveries(limit=10, max_attempts=5), [])

    def test_multi_target_partial_failure(self):
        # B10 core: one channel ok, another failed → only the failed one retries
        self.db.record_delivery("h1", "@main", "sent")
        self.db.record_delivery("h1", "@qa", "failed")
        rows = self.db.failed_deliveries(limit=10)
        self.assertEqual(
            [(r["job_hash"], r["target_channel"]) for r in rows], [("h1", "@qa")]
        )


class RetryFailedDeliveriesTests(TempDbTestCase):
    def _job(self, hash_):
        return {"hash": hash_, "title": "Junior Dev", "company": "Acme",
                "url": "https://e.com/1", "level": "Junior",
                "category": "development", "source": "Test",
                "location": "Remote", "salary": "Не указана"}

    def test_retry_sends_only_to_failed_target(self):
        job = self._job("abc123")
        self.db.save_job_payload("abc123", job)
        self.db.record_delivery("abc123", "@main", "sent")
        self.db.record_delivery("abc123", "@qa", "failed")

        sent_to = []

        async def fake_send(bot, job_arg, chat_id, interactive=True, max_flood_retries=3):
            sent_to.append(chat_id)
            return True

        async def no_sleep(_):
            return None

        bot = SimpleNamespace()
        with mock.patch.object(channel_bot, "_send_job_to_chat", fake_send), \
             mock.patch.object(asyncio, "sleep", no_sleep), \
             mock.patch.object(Config, "ENABLE_MARKDOWN_V2", False):
            stats = asyncio.run(retry_failed_deliveries(bot, self.db))

        self.assertEqual(sent_to, ["@qa"])          # @main NOT reposted
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(self.db.failed_deliveries(limit=10), [])

    def test_missing_payload_is_not_retried_forever(self):
        self.db.record_delivery("gone", "@qa", "failed")

        async def no_sleep(_):
            return None

        bot = SimpleNamespace()
        with mock.patch.object(channel_bot, "_send_job_to_chat") as send, \
             mock.patch.object(asyncio, "sleep", no_sleep):
            stats = asyncio.run(retry_failed_deliveries(bot, self.db))
        send.assert_not_called()
        self.assertEqual(stats["missing_payload"], 1)
        row = self.db.fetchall(
            "SELECT status, last_error FROM deliveries WHERE job_hash='gone'")
        self.assertEqual(row[0][1], "payload_missing")


class MultiChannelDedupTests(unittest.TestCase):
    def test_dedup_target_channels_includes_routes_and_dedups(self):
        with mock.patch.object(Config, "CHANNEL_ID", "@main"), \
             mock.patch.object(Config, "CHANNEL_ROUTES",
                               [(["qa"], "@qa"), (["development"], "@main")]):
            self.assertEqual(dedup_target_channels(), ["@main", "@qa"])

    def test_hashes_collected_from_all_channels(self):
        visited = []

        class FakeMessage:
            def __init__(self, text):
                self.message = text
                self.reply_markup = None

        class FakeClient:
            async def get_entity(self, chat_id):
                visited.append(chat_id)
                return chat_id

            def iter_messages(self, entity, limit=None):
                async def gen():
                    yield FakeMessage(f"apply https://jobs.example/{entity}/1")
                return gen()

        class FakeParser:
            def __init__(self):
                self.client = FakeClient()

            async def connect(self):
                return True

            async def disconnect(self):
                return None

        async def run():
            with mock.patch.object(channel_bot, "TELEGRAM_PARSER_AVAILABLE", True), \
                 mock.patch.object(Config, "TELEGRAM_API_ID", "1"), \
                 mock.patch.object(Config, "TELEGRAM_API_HASH", "h"), \
                 mock.patch.object(Config, "CHANNEL_ID", "@main"), \
                 mock.patch.object(Config, "CHANNEL_ROUTES", [(["qa"], "@qa")]), \
                 mock.patch.object(channel_bot, "TelegramJobParser", FakeParser):
                return await get_recent_channel_job_hashes()

        hashes = asyncio.run(run())
        self.assertEqual(visited, ["@main", "@qa"])   # B11: both channels scanned
        self.assertEqual(len(hashes), 2)

    def test_one_dead_channel_does_not_kill_dedup(self):
        class FakeClient:
            async def get_entity(self, chat_id):
                if chat_id == "@dead":
                    raise ValueError("channel inaccessible")
                return chat_id

            def iter_messages(self, entity, limit=None):
                async def gen():
                    yield SimpleNamespace(
                        message=f"apply https://jobs.example/{entity}/1",
                        reply_markup=None,
                    )
                return gen()

        class FakeParser:
            def __init__(self):
                self.client = FakeClient()

            async def connect(self):
                return True

            async def disconnect(self):
                return None

        async def run():
            with mock.patch.object(channel_bot, "TELEGRAM_PARSER_AVAILABLE", True), \
                 mock.patch.object(Config, "TELEGRAM_API_ID", "1"), \
                 mock.patch.object(Config, "TELEGRAM_API_HASH", "h"), \
                 mock.patch.object(channel_bot, "TelegramJobParser", FakeParser):
                return await get_recent_channel_job_hashes(channels=["@dead", "@live"])

        hashes = asyncio.run(run())
        self.assertEqual(len(hashes), 1)  # live channel still feeds dedup


if __name__ == "__main__":
    unittest.main()
