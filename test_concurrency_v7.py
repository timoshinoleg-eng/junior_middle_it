"""Concurrency regression tests (Stage 4, B07/B10; beta gate "zero duplicates
under two simultaneous collection attempts").

Two independent publisher processes are simulated as two threads, each with its
OWN database connection to the SAME SQLite file — the closest SQLite-level
model of two processes sharing state. The delivery ledger's
UNIQUE(job_hash, target_channel) and posted_jobs PRIMARY KEY must guarantee
zero duplicates regardless of interleaving.

No network, no Telegram I/O.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from collections import Counter
from unittest.mock import patch

import channel_bot
from channel_bot import (
    DatabaseConnection,
    generate_job_hash,
    post_job_with_bot,
    run_v7_migration,
)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


JOBS = [
    {"title": f"Junior Engineer {i}", "company": "Acme", "level": "Junior",
     "url": f"https://boards.greenhouse.io/acme/jobs/{1000 + i}",
     "source": "Greenhouse:acme", "category": "development"}
    for i in range(6)
]
TARGETS = ("@main", "@qa")


class ConcurrentPublishersTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)

    def _publisher_cycle(self, barrier, errors):
        try:
            db = DatabaseConnection(self.path)
            run_v7_migration(db)
            barrier.wait(timeout=10)
            for job in JOBS:
                job = dict(job)
                h = generate_job_hash(job)
                job["hash"] = h
                # Race-prone check-then-insert, exactly like the real cycle:
                # the PRIMARY KEY / OR IGNORE makes it safe.
                if not db.fetchone("SELECT 1 FROM posted_jobs WHERE hash = ?", (h,)):
                    db.execute(
                        "INSERT OR IGNORE INTO posted_jobs "
                        "(hash, title, company, level, url, source, category) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (h, job["title"], job["company"], job["level"],
                         job["url"], job["source"], job["category"]),
                    )
                for target in TARGETS:
                    db.record_delivery(h, target, "sent")
            db.conn.close()
        except Exception as e:  # pragma: no cover - failures asserted below
            errors.append(e)

    def test_two_simultaneous_cycles_produce_zero_duplicates(self):
        barrier = threading.Barrier(2)
        errors = []
        threads = [
            threading.Thread(target=self._publisher_cycle, args=(barrier, errors)),
            threading.Thread(target=self._publisher_cycle, args=(barrier, errors)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])

        # Re-open through DatabaseConnection so the same assertion covers
        # SQLite and the PostgreSQL CI backend (TEST_DATABASE_URL).
        db = DatabaseConnection(self.path)
        try:
            posted = db.fetchone("SELECT COUNT(*) FROM posted_jobs")[0]
            self.assertEqual(posted, len(JOBS))  # each job exactly once

            per_hash = db.fetchall(
                "SELECT hash, COUNT(*) FROM posted_jobs GROUP BY hash HAVING COUNT(*) > 1"
            )
            self.assertEqual(per_hash, [])       # no duplicate rows

            delivery_dups = db.fetchall(
                "SELECT job_hash, target_channel, COUNT(*) FROM deliveries "
                "GROUP BY job_hash, target_channel HAVING COUNT(*) > 1"
            )
            self.assertEqual(delivery_dups, [])  # one ledger row per (job, channel)

            delivery_total = db.fetchone("SELECT COUNT(*) FROM deliveries")[0]
            self.assertEqual(delivery_total, len(JOBS) * len(TARGETS))
        finally:
            db.conn.close()

    def test_failed_target_retried_only_once_next_cycle(self):
        """Cycle 1: @qa fails. Cycle 2: only @qa is retried (B10 semantics)."""
        db = DatabaseConnection(self.path)
        run_v7_migration(db)
        job = dict(JOBS[0])
        h = generate_job_hash(job)
        db.record_delivery(h, "@main", "sent")
        db.record_delivery(h, "@qa", "failed", error="send_error")

        retried = db.failed_deliveries(limit=10)
        self.assertEqual(
            [(r["job_hash"], r["target_channel"]) for r in retried],
            [(h, "@qa")],
        )
        # after successful retry the ledger is clean
        db.record_delivery(h, "@qa", "sent")
        self.assertEqual(db.failed_deliveries(limit=10), [])
        db.conn.close()


class ConcurrentPublishPathTests(unittest.TestCase):
    """Drive the REAL publish path (post_job_with_bot) from two publishers.

    The ledger's UNIQUE(job_hash, target_channel) de-duplicates ROWS, not
    messages: two processes that both reach the send step produce one ledger
    row and two channel posts. This test counts actual sends, so it fails on
    the original incident symptom (duplicate posts) instead of passing on
    storage invariants alone.
    """

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.sends = Counter()

    def tearDown(self):
        os.unlink(self.path)

    async def _fake_send(self, bot, job, chat_id, interactive=True,
                         max_flood_retries=3):
        self.sends[(job["hash"], chat_id)] += 1
        await asyncio.sleep(0)
        return True

    async def _publisher(self):
        db = DatabaseConnection(self.path)
        run_v7_migration(db)
        for job in JOBS:
            job = dict(job)
            job["hash"] = generate_job_hash(job)
            await post_job_with_bot(object(), job, db=db)
        db.conn.close()

    def test_two_publishers_send_each_job_to_each_channel_once(self):
        with patch.object(channel_bot, "get_target_channels",
                          lambda job: list(TARGETS)), \
             patch.object(channel_bot, "_send_job_to_chat", self._fake_send), \
             patch.object(channel_bot.Config, "MULTI_TRACK_POST_DELAY", 0):

            async def run():
                await asyncio.gather(self._publisher(), self._publisher())

            asyncio.run(run())

        self.assertEqual(sum(self.sends.values()), len(JOBS) * len(TARGETS))
        self.assertEqual({k: v for k, v in self.sends.items() if v > 1}, {})

    def test_abandoned_pending_claim_is_reclaimable_after_timeout(self):
        """A crash after claiming must not drop the delivery forever."""
        db = DatabaseConnection(self.path)
        run_v7_migration(db)
        job = dict(JOBS[0])
        h = generate_job_hash(job)
        self.assertTrue(db.claim_delivery(h, "@main"))
        self.assertFalse(db.claim_delivery(h, "@main"))  # fresh claim is held
        # Simulate an abandoned claim from 30 minutes ago.
        stale_sql = (
            "UPDATE deliveries SET updated_at = "
            f"{db.backend.ts_ago(30)} "
            "WHERE job_hash = ? AND target_channel = ?"
        )
        db.execute(stale_sql, (h, "@main"))
        self.assertTrue(db.claim_delivery(h, "@main"))
        db.conn.close()

    def test_two_retry_workers_claim_failed_delivery_once(self):
        """B07 regression: concurrent retry workers must not double-send."""
        db = DatabaseConnection(self.path)
        run_v7_migration(db)
        h = generate_job_hash(JOBS[0])
        db.save_job_payload(h, {**JOBS[0], "hash": h})
        db.record_delivery(h, "@qa", "failed", error="first_send_failed")
        barrier = threading.Barrier(2)
        claims = []
        errors = []

        def worker():
            try:
                barrier.wait(timeout=10)
                claims.append(db.claim_failed_delivery(h, "@qa"))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(sum(claims), 1)
        self.assertEqual(db.fetchone(
            "SELECT status FROM deliveries WHERE job_hash = ? AND target_channel = ?",
            (h, "@qa"),
        )[0], "pending")
        db.conn.close()


if __name__ == "__main__":
    unittest.main()
