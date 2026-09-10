import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import public_acquisition_runtime as p7


class FakeStateDB:
    def __init__(self, jobs=None):
        self.rows = {}
        self.jobs = jobs or {}

    def set_runtime_state(self, user_id, state_key, state, *, ttl_seconds):
        self.rows[(int(user_id), state_key)] = {
            "state": dict(state),
            "ttl": int(ttl_seconds),
        }

    def get_runtime_state(self, user_id, state_key):
        row = self.rows.get((int(user_id), state_key))
        return dict(row["state"]) if row else None

    def delete_runtime_state(self, user_id, state_key):
        self.rows.pop((int(user_id), state_key), None)

    def get_job_payload(self, job_hash):
        return self.jobs.get(job_hash)

    @staticmethod
    def _normalize_search_spec(spec):
        return {
            "name": str(spec.get("name") or "Поиск")[:80],
            "categories": list(spec.get("categories") or []),
            "skills": str(spec.get("skills") or ""),
            "min_salary_filter": int(spec.get("min_salary_filter") or 0),
            "hide_senior": bool(spec.get("hide_senior", True)),
        }


class DurableRuntimeStateTests(unittest.TestCase):
    def test_jobbot_maps_survive_reinstantiation_and_resume_stores_only_hash(self):
        job = {
            "hash": "abc123",
            "title": "Junior Python",
            "description": "durable vacancy payload",
        }
        db = FakeStateDB({"abc123": job})
        bot1 = p7.JobBot(object(), db)

        bot1.setup_steps[77] = "salary"
        bot1.resume_sessions[77] = {
            "job_hash": "abc123",
            "job": job,
            "resume_text": "THIS MUST NEVER BE STORED",
        }
        bot1.pending_saved_searches[77] = {
            "name": "Python junior",
            "categories": ["backend"],
            "skills": "python, django",
            "min_salary_filter": 1500,
            "hide_senior": True,
            "resume_text": "ALSO MUST NEVER BE STORED",
        }

        persisted_resume = db.rows[(77, "resume_session")]["state"]
        self.assertEqual(persisted_resume, {"job_hash": "abc123"})
        self.assertNotIn("resume_text", repr(db.rows))

        bot2 = p7.JobBot(object(), db)
        self.assertEqual(bot2.setup_steps.get(77), "salary")
        restored = bot2.resume_sessions.get(77)
        self.assertEqual(restored["job_hash"], "abc123")
        self.assertEqual(restored["job"]["title"], "Junior Python")
        pending = bot2.pending_saved_searches.get(77)
        self.assertEqual(pending["skills"], "python, django")
        self.assertEqual(pending["min_salary_filter"], 1500)

    def test_stale_resume_hash_self_cleans(self):
        db = FakeStateDB()
        db.set_runtime_state(
            77,
            "resume_session",
            {"job_hash": "gone"},
            ttl_seconds=p7.RESUME_STATE_TTL_SECONDS,
        )
        bot = p7.JobBot(object(), db)
        self.assertNotIn(77, bot.resume_sessions)
        self.assertNotIn((77, "resume_session"), db.rows)

    def test_pop_deletes_durable_state(self):
        db = FakeStateDB({"abc123": {"hash": "abc123", "title": "Junior"}})
        bot = p7.JobBot(object(), db)
        bot.setup_steps[5] = "skills"
        self.assertEqual(bot.setup_steps.pop(5), "skills")
        self.assertNotIn((5, "setup_step"), db.rows)
        self.assertIsNone(bot.setup_steps.pop(5, None))

    def test_sqlite_runtime_state_roundtrip_and_expiry(self):
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        ):
            db = p7.DatabaseConnection(":memory:")
        try:
            db.set_runtime_state(9, "setup_step", {"step": "salary"}, ttl_seconds=3600)
            self.assertEqual(db.get_runtime_state(9, "setup_step"), {"step": "salary"})

            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            db.execute(
                "UPDATE runtime_state SET expires_at=? WHERE user_id=? AND state_key=?",
                (expired, 9, "setup_step"),
            )
            self.assertIsNone(db.get_runtime_state(9, "setup_step"))
            self.assertIsNone(
                db.fetchone(
                    "SELECT state FROM runtime_state WHERE user_id=? AND state_key=?",
                    (9, "setup_step"),
                )
            )
        finally:
            db.close()

    def test_runtime_state_has_bounded_payload(self):
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        ):
            db = p7.DatabaseConnection(":memory:")
        try:
            with self.assertRaises(ValueError):
                db.set_runtime_state(
                    1,
                    "pending_saved_search",
                    {"skills": "x" * 20_000},
                    ttl_seconds=3600,
                )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
