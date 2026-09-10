import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import serverless_payload_runtime as spr


CLAIM_TOKEN = "11111111-1111-1111-1111-111111111111"


class ServerlessPayloadRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_store = spr._payload_store_instance
        spr._payload_store_instance = None
        self.context_tokens = [
            (spr._durable_duplicate_skips, spr._durable_duplicate_skips.set(0)),
            (spr._durable_begin_failures, spr._durable_begin_failures.set(0)),
            (spr._durable_ambiguous_sends, spr._durable_ambiguous_sends.set(0)),
            (spr._durable_finalize_failures, spr._durable_finalize_failures.set(0)),
        ]
        self.old_telegram_sources = spr.core.Config.ENABLE_TELEGRAM_CHANNELS

    def tearDown(self):
        store = spr._payload_store_instance
        if store is not None and store is not self.old_store:
            try:
                store.close()
            except Exception:
                pass
        spr._payload_store_instance = self.old_store
        for context, token in reversed(self.context_tokens):
            context.reset(token)
        spr.core.Config.ENABLE_TELEGRAM_CHANNELS = self.old_telegram_sources

    def test_vercel_disables_stateful_telegram_sources_by_default(self):
        spr.core.Config.ENABLE_TELEGRAM_CHANNELS = True
        with patch.dict(os.environ, {"VERCEL": "1", "VERCEL_ENABLE_TELEGRAM_SOURCES": ""}, clear=False):
            spr._configure_serverless_source_policy()
        self.assertFalse(spr.core.Config.ENABLE_TELEGRAM_CHANNELS)

    def test_vercel_can_explicitly_opt_in_telegram_sources(self):
        spr.core.Config.ENABLE_TELEGRAM_CHANNELS = False
        with patch.dict(os.environ, {"VERCEL": "1", "VERCEL_ENABLE_TELEGRAM_SOURCES": "true"}, clear=False):
            spr._configure_serverless_source_policy()
        self.assertTrue(spr.core.Config.ENABLE_TELEGRAM_CHANNELS)

    async def test_direct_postgres_preserves_existing_behavior(self):
        fake_store = SimpleNamespace(
            growth_backend="postgres",
            save_job_payload=MagicMock(),
            close=MagicMock(),
        )
        original = AsyncMock(return_value=True)
        job = {"title": "Junior Python Developer", "company": "Example", "url": "https://example.com/jobs/1"}
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
        fake_store.save_job_payload.assert_called_once_with(job["hash"], job)
        original.assert_awaited_once_with(ANY, job, db=None)

    async def test_edge_claim_advances_before_send_and_finalizes_after_success(self):
        original = AsyncMock(return_value=True)
        claim = MagicMock(return_value=SimpleNamespace(
            claimed=True,
            claim_token=CLAIM_TOKEN,
            publication_state="pending",
        ))
        begin = MagicMock(return_value=True)
        publish = MagicMock(return_value=True)
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=["@jobs"]
        ), patch.object(spr, "claim_job_payload_edge", claim), patch.object(
            spr, "begin_job_payload_send_edge", begin
        ), patch.object(spr, "mark_job_payload_published_edge", publish), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertTrue(result)
        claim.assert_called_once_with("abc123", job)
        begin.assert_called_once_with("abc123", CLAIM_TOKEN)
        original.assert_awaited_once_with(ANY, job, db=None)
        publish.assert_called_once_with("abc123", CLAIM_TOKEN)

    async def test_existing_durable_claim_skips_telegram(self):
        original = AsyncMock(return_value=True)
        claim = MagicMock(return_value=SimpleNamespace(
            claimed=False,
            claim_token="",
            publication_state="published",
        ))
        job = {"hash": "abc123", "title": "QA", "company": "Example"}
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=["@jobs"]
        ), patch.object(spr, "claim_job_payload_edge", claim), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), job, db=None)

        self.assertFalse(result)
        original.assert_not_awaited()
        self.assertEqual(spr._durable_duplicate_skips.get(), 1)

    async def test_no_target_channel_does_not_consume_claim(self):
        claim = MagicMock()
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=[]
        ), patch.object(spr, "claim_job_payload_edge", claim):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), {"hash": "abc123"}, db=None)

        self.assertFalse(result)
        claim.assert_not_called()

    async def test_begin_failure_prevents_telegram_send_and_preserves_claim(self):
        original = AsyncMock(return_value=True)
        claim = SimpleNamespace(claimed=True, claim_token=CLAIM_TOKEN, publication_state="pending")
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=["@jobs"]
        ), patch.object(spr, "claim_job_payload_edge", return_value=claim), patch.object(
            spr, "begin_job_payload_send_edge", return_value=False
        ), patch.object(spr, "_ORIGINAL_POST_JOB_WITH_BOT", original):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), {"hash": "abc123"}, db=None)

        self.assertFalse(result)
        original.assert_not_awaited()
        self.assertEqual(spr._durable_begin_failures.get(), 1)

    async def test_ambiguous_false_send_is_not_released_or_finalized(self):
        original = AsyncMock(return_value=False)
        publish = MagicMock()
        claim = SimpleNamespace(claimed=True, claim_token=CLAIM_TOKEN, publication_state="pending")
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=["@jobs"]
        ), patch.object(spr, "claim_job_payload_edge", return_value=claim), patch.object(
            spr, "begin_job_payload_send_edge", return_value=True
        ), patch.object(spr, "mark_job_payload_published_edge", publish), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), {"hash": "abc123"}, db=None)

        self.assertFalse(result)
        publish.assert_not_called()
        self.assertEqual(spr._durable_ambiguous_sends.get(), 1)

    async def test_telegram_exception_preserves_sending_claim_and_reraises(self):
        original = AsyncMock(side_effect=RuntimeError("telegram failed"))
        publish = MagicMock()
        claim = SimpleNamespace(claimed=True, claim_token=CLAIM_TOKEN, publication_state="pending")
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=["@jobs"]
        ), patch.object(spr, "claim_job_payload_edge", return_value=claim), patch.object(
            spr, "begin_job_payload_send_edge", return_value=True
        ), patch.object(spr, "mark_job_payload_published_edge", publish), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            with self.assertRaisesRegex(RuntimeError, "telegram failed"):
                await spr.post_job_with_durable_payload(SimpleNamespace(), {"hash": "abc123"}, db=None)

        publish.assert_not_called()
        self.assertEqual(spr._durable_ambiguous_sends.get(), 1)

    async def test_finalize_failure_does_not_turn_successful_send_into_retry(self):
        original = AsyncMock(return_value=True)
        claim = SimpleNamespace(claimed=True, claim_token=CLAIM_TOKEN, publication_state="pending")
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr, "edge_writer_configured", return_value=True), patch.object(
            spr.core, "get_target_channels", return_value=["@jobs"]
        ), patch.object(spr, "claim_job_payload_edge", return_value=claim), patch.object(
            spr, "begin_job_payload_send_edge", return_value=True
        ), patch.object(spr, "mark_job_payload_published_edge", return_value=False), patch.object(
            spr, "_ORIGINAL_POST_JOB_WITH_BOT", original
        ):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), {"hash": "abc123"}, db=None)

        self.assertTrue(result)
        self.assertEqual(spr._durable_finalize_failures.get(), 1)

    async def test_edge_transition_retries_transient_exception(self):
        transition = MagicMock(side_effect=[RuntimeError("down"), True])
        result = await spr._edge_transition_with_retries(
            transition, "abc123", CLAIM_TOKEN, label="test", attempts=3
        )
        self.assertTrue(result)
        self.assertEqual(transition.call_count, 2)

    async def test_collect_reconciles_and_resets_all_durable_telemetry(self):
        old_telemetry = copy.deepcopy(spr.core.CYCLE_TELEMETRY)

        async def fake_collect(*args, **kwargs):
            spr._durable_duplicate_skips.set(2)
            spr._durable_begin_failures.set(1)
            spr._durable_ambiguous_sends.set(3)
            spr._durable_finalize_failures.set(4)
            spr.core.CYCLE_TELEMETRY["failed_posts"] = 5
            spr.core.CYCLE_TELEMETRY.setdefault("funnel", {})["duplicates"] = 7
            return {"ok": True, "posted": 1, "duplicates": 7, "failed": 5}

        try:
            with patch.object(spr, "_ORIGINAL_COLLECT_AND_POST_ONCE", side_effect=fake_collect):
                result = await spr.collect_and_post_once(use_sqlite=False)

            self.assertEqual(result["failed"], 3)
            self.assertEqual(result["duplicates"], 9)
            self.assertEqual(result["durable_duplicates"], 2)
            self.assertEqual(result["durable_begin_failures"], 1)
            self.assertEqual(result["durable_ambiguous_sends"], 3)
            self.assertEqual(result["durable_finalize_failures"], 4)
        finally:
            spr.core.CYCLE_TELEMETRY.clear()
            spr.core.CYCLE_TELEMETRY.update(old_telemetry)

    async def test_collect_exposes_zero_durable_counters_on_clean_cycle(self):
        with patch.object(
            spr,
            "_ORIGINAL_COLLECT_AND_POST_ONCE",
            new=AsyncMock(return_value={"ok": True, "posted": 0, "duplicates": 0, "failed": 0}),
        ):
            result = await spr.collect_and_post_once(use_sqlite=False)
        self.assertEqual(result["durable_duplicates"], 0)
        self.assertEqual(result["durable_begin_failures"], 0)
        self.assertEqual(result["durable_ambiguous_sends"], 0)
        self.assertEqual(result["durable_finalize_failures"], 0)

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
        original.assert_awaited_once_with(ANY, job, db=None)

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

    async def test_unavailable_optional_postgres_falls_back_to_edge_state_machine(self):
        fake_store = SimpleNamespace(growth_backend="sqlite", save_job_payload=MagicMock(), close=MagicMock())
        original = AsyncMock(return_value=True)
        claim = SimpleNamespace(claimed=True, claim_token=CLAIM_TOKEN, publication_state="pending")
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "postgresql://example/db", "DATABASE_URL": "", "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy"},
            clear=False,
        ), patch.object(spr.runtime, "DatabaseConnection", return_value=fake_store), patch.object(
            spr, "edge_writer_configured", return_value=True
        ), patch.object(spr.core, "get_target_channels", return_value=["@jobs"]), patch.object(
            spr, "claim_job_payload_edge", return_value=claim
        ), patch.object(spr, "begin_job_payload_send_edge", return_value=True), patch.object(
            spr, "mark_job_payload_published_edge", return_value=True
        ), patch.object(spr, "_ORIGINAL_POST_JOB_WITH_BOT", original):
            result = await spr.post_job_with_durable_payload(SimpleNamespace(), {"hash": "abc123"}, db=None)

        self.assertTrue(result)
        fake_store.close.assert_called_once()
        fake_store.save_job_payload.assert_not_called()
        original.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
