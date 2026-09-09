import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import serverless_payload_runtime as spr


class ServerlessPayloadRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_store = spr._payload_store_instance
        spr._payload_store_instance = None

    def tearDown(self):
        store = spr._payload_store_instance
        if store is not None and store is not self.old_store:
            try:
                store.close()
            except Exception:
                pass
        spr._payload_store_instance = self.old_store

    async def test_serverless_post_presaves_payload_but_preserves_none_db_argument(self):
        fake_store = SimpleNamespace(
            growth_backend="postgres",
            save_job_payload=MagicMock(),
            close=MagicMock(),
        )
        original = AsyncMock(return_value=True)
        job = {
            "title": "Junior Python Developer",
            "company": "Example",
            "url": "https://example.com/jobs/1",
        }
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "postgresql://example/db", "DATABASE_URL": ""},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection", return_value=fake_store) as ctor, patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertTrue(result)
        ctor.assert_called_once_with(":memory:")
        self.assertEqual(job["hash"], spr.core.generate_job_hash(job))
        fake_store.save_job_payload.assert_called_once_with(job["hash"], job)
        original.assert_awaited_once()
        self.assertIsNone(original.await_args.kwargs["db"])

    async def test_no_growth_dsn_keeps_original_serverless_behavior(self):
        original = AsyncMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": ""},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection") as ctor, patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertTrue(result)
        ctor.assert_not_called()
        original.assert_awaited_once()
        self.assertIsNone(original.await_args.kwargs["db"])

    async def test_existing_interactive_db_does_not_create_second_store(self):
        original = AsyncMock(return_value=True)
        interactive_db = object()
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "postgresql://example/db"},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection") as ctor, patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(
                SimpleNamespace(), job, db=interactive_db
            )

        self.assertTrue(result)
        ctor.assert_not_called()
        original.assert_awaited_once_with(unittest.mock.ANY, job, db=interactive_db)

    async def test_unavailable_optional_postgres_fallback_is_not_mistaken_for_durable(self):
        fake_store = SimpleNamespace(
            growth_backend="sqlite",
            save_job_payload=MagicMock(),
            close=MagicMock(),
        )
        original = AsyncMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "postgresql://example/db", "DATABASE_URL": ""},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection", return_value=fake_store), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertTrue(result)
        fake_store.close.assert_called_once()
        fake_store.save_job_payload.assert_not_called()
        original.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
