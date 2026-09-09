import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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
            {"GROWTH_DATABASE_URL": "postgresql://example/db", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": ""},
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

    async def test_edge_first_claim_posts_once(self):
        original = AsyncMock(return_value=True)
        edge_claim = MagicMock(return_value=True)
        release = MagicMock()
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr, "save_job_payload_edge", edge_claim
        ), patch.object(spr, "release_job_payload_edge", release), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertTrue(result)
        edge_claim.assert_called_once_with("abc123", job)
        original.assert_awaited_once_with(ANY, job, db=None)
        release.assert_not_called()

    async def test_edge_existing_claim_skips_telegram_post(self):
        original = AsyncMock(return_value=True)
        edge_claim = MagicMock(return_value=False)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr, "save_job_payload_edge", edge_claim
        ), patch.object(spr, "_ORIGINAL_POST_JOB_WITH_BOT", original):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertFalse(result)
        edge_claim.assert_called_once_with("abc123", job)
        original.assert_not_awaited()

    async def test_failed_telegram_post_releases_edge_claim(self):
        original = AsyncMock(return_value=False)
        release = MagicMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr, "save_job_payload_edge", return_value=True
        ), patch.object(spr, "release_job_payload_edge", release), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertFalse(result)
        release.assert_called_once_with("abc123")

    async def test_telegram_exception_releases_edge_claim_and_reraises(self):
        original = AsyncMock(side_effect=RuntimeError("telegram failed"))
        release = MagicMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr, "save_job_payload_edge", return_value=True
        ), patch.object(spr, "release_job_payload_edge", release), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            with self.assertRaisesRegex(RuntimeError, "telegram failed"):
                await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        release.assert_called_once_with("abc123")

    async def test_no_growth_backend_keeps_original_serverless_behavior(self):
        original = AsyncMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": ""},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=False), patch.object(
            spr.runtime, "DatabaseConnection"
        ) as ctor, patch.object(spr, "_ORIGINAL_POST_JOB_WITH_BOT", original):
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
            {"GROWTH_DATABASE_URL": "postgresql://example/db", "GROWTH_PUBLIC_URL": ""},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection") as ctor, patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=interactive_db)

        self.assertTrue(result)
        ctor.assert_not_called()
        original.assert_awaited_once_with(ANY, job, db=interactive_db)

    async def test_unavailable_optional_postgres_can_fall_back_to_edge_claim(self):
        fake_store = SimpleNamespace(
            growth_backend="sqlite",
            save_job_payload=MagicMock(),
            close=MagicMock(),
        )
        original = AsyncMock(return_value=True)
        edge_claim = MagicMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "postgresql://example/db", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection", return_value=fake_store), patch.object(
            spr, "edge_writer_configured", return_value=True
        ), patch.object(spr, "save_job_payload_edge", edge_claim), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertTrue(result)
        fake_store.close.assert_called_once()
        fake_store.save_job_payload.assert_not_called()
        edge_claim.assert_called_once_with("abc123", job)
        original.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
