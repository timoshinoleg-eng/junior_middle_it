"""Concurrency regression tests (Stage 4, B07/B10; beta gate "zero duplicates
under two simultaneous collection attempts").

Two independent publisher processes are simulated as two threads, each with its
OWN database connection to the SAME SQLite file — the closest SQLite-level
model of two processes sharing state. The delivery ledger's
UNIQUE(job_hash, target_channel) and posted_jobs PRIMARY KEY must guarantee
zero duplicates regardless of interleaving.

No network, no Telegram I/O.
"""
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from channel_bot import DatabaseConnection, generate_job_hash, run_v7_migration


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

        conn = sqlite3.connect(self.path)
        try:
            posted = conn.execute("SELECT COUNT(*) FROM posted_jobs").fetchone()[0]
            self.assertEqual(posted, len(JOBS))  # each job exactly once

            per_hash = conn.execute(
                "SELECT hash, COUNT(*) FROM posted_jobs GROUP BY hash HAVING COUNT(*) > 1"
            ).fetchall()
            self.assertEqual(per_hash, [])       # no duplicate rows

            delivery_dups = conn.execute(
                "SELECT job_hash, target_channel, COUNT(*) FROM deliveries "
                "GROUP BY job_hash, target_channel HAVING COUNT(*) > 1"
            ).fetchall()
            self.assertEqual(delivery_dups, [])  # one ledger row per (job, channel)

            delivery_total = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
            self.assertEqual(delivery_total, len(JOBS) * len(TARGETS))
        finally:
            conn.close()

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


if __name__ == "__main__":
    unittest.main()
