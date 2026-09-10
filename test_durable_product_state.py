import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import durable_product_state as dps


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
        if sql.startswith("insert into growth_favorites"):
            user_id, job_hash = int(params[0]), str(params[1])
            job = self.store.jobs.get(job_hash)
            if job and job.get("publication_state") == "published":
                self.store.favorites[(user_id, job_hash)] = True
                self.rows = [(job_hash,)]
        elif sql.startswith("delete from growth_favorites"):
            key = (int(params[0]), str(params[1]))
            if self.store.favorites.pop(key, None):
                self.rows = [(key[1],)]
        elif "from growth_favorites f" in sql:
            user_id = int(params[0])
            hashes = [job_hash for uid, job_hash in self.store.favorites if uid == user_id]
            self.rows = [
                (job_hash, self.store.jobs[job_hash]["payload"])
                for job_hash in hashes
                if job_hash in self.store.jobs
                and self.store.jobs[job_hash].get("publication_state") == "published"
            ]
        elif "from growth_job_payloads" in sql and "coalesce(published_at" in sql:
            limit = int(params[1])
            published = [
                (job_hash, row["payload"])
                for job_hash, row in self.store.jobs.items()
                if row.get("publication_state") == "published"
            ]
            self.rows = published[:limit]
        else:
            raise AssertionError(f"unexpected SQL: {query}")
        return self

    def fetchone(self):
        if self.index >= len(self.rows):
            return None
        row = self.rows[self.index]
        self.index += 1
        return row

    def fetchall(self):
        rows = self.rows[self.index :]
        self.index = len(self.rows)
        return rows


class FakeGrowthStore:
    def __init__(self):
        self.jobs = {
            "pub1": {
                "publication_state": "published",
                "payload": {
                    "title": "Junior Python",
                    "company": "Example",
                    "hash": "pub1",
                    "salary_min_usd": 1800,
                },
            },
            "pub_no_salary": {
                "publication_state": "published",
                "payload": {"title": "Junior QA", "hash": "pub_no_salary"},
            },
            "pending1": {
                "publication_state": "pending",
                "payload": {
                    "title": "Do not expose",
                    "hash": "pending1",
                    "salary_min_usd": 9000,
                },
            },
        }
        self.favorites = {}

    def _ensure_conn(self):
        return self

    def cursor(self):
        return FakeCursor(self)


class FakeDB:
    def __init__(self, save_result):
        self.save_result = save_result
        self.events = []

    def add_favorite(self, user_id, job_hash):
        self.saved = (user_id, job_hash)
        return self.save_result

    def log_event(self, user_id, name, props=None):
        self.events.append((user_id, name, props or {}))


class DurableProductStateTests(unittest.TestCase):
    def make_db(self):
        db = object.__new__(dps.DatabaseConnection)
        db._growth_store = FakeGrowthStore()
        return db

    def test_durable_favorite_requires_published_payload_and_roundtrips(self):
        db = self.make_db()
        self.assertTrue(db.add_favorite(77, "pub1"))
        self.assertFalse(db.add_favorite(77, "pending1"))
        favorites = db.get_user_favorites(77)
        self.assertEqual(len(favorites), 1)
        self.assertEqual(favorites[0]["hash"], "pub1")
        self.assertEqual(favorites[0]["title"], "Junior Python")
        self.assertTrue(db.remove_favorite(77, "pub1"))
        self.assertEqual(db.get_user_favorites(77), [])

    def test_digest_reads_only_published_durable_payloads(self):
        db = self.make_db()
        jobs = db.recent_jobs_for_digest(hours=36, limit=80)
        self.assertEqual([job["hash"] for job in jobs], ["pub1", "pub_no_salary"])
        self.assertEqual(jobs[0]["company"], "Example")

    def test_salary_report_reads_only_published_jobs_with_numeric_salary(self):
        db = self.make_db()
        jobs = db.jobs_with_salary_for_report(days=14, limit=500)
        self.assertEqual([job["hash"] for job in jobs], ["pub1"])
        self.assertEqual(jobs[0]["salary_min_usd"], 1800)

    def test_payload_parser_rejects_non_object_json(self):
        self.assertEqual(dps.DatabaseConnection._payload_dict("[]", "x"), {})
        self.assertEqual(dps.DatabaseConnection._payload_dict("not-json", "x"), {})


class DurableProductCallbackTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_update(data="save:pub1"):
        query = SimpleNamespace(data=data, answer=AsyncMock())
        return SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )

    async def test_save_callback_fails_truthfully_for_stale_job(self):
        bot = object.__new__(dps.JobBot)
        bot.db = FakeDB(False)
        update = self.make_update()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await bot.handle_callback(update, context)
        update.callback_query.answer.assert_awaited_once_with("Вакансия уже устарела", show_alert=True)
        context.bot.send_message.assert_not_awaited()
        self.assertEqual(bot.db.events, [])

    async def test_save_callback_confirms_durable_save(self):
        bot = object.__new__(dps.JobBot)
        bot.db = FakeDB(True)
        update = self.make_update()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await bot.handle_callback(update, context)
        update.callback_query.answer.assert_awaited_once_with("Сохранено")
        context.bot.send_message.assert_awaited_once()
        self.assertEqual(bot.db.events[0], (77, "save_job", {"hash": "pub1"}))


if __name__ == "__main__":
    unittest.main()
