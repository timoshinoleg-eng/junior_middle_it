import os
import unittest
from unittest.mock import AsyncMock, patch

import serverless_payload_runtime as spr


class ServerlessTelegramHistoryPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_sources = spr.core.Config.ENABLE_TELEGRAM_CHANNELS
        self.old_history = spr.core.get_recent_channel_job_hashes

    def tearDown(self):
        spr.core.Config.ENABLE_TELEGRAM_CHANNELS = self.old_sources
        spr.core.get_recent_channel_job_hashes = self.old_history

    async def test_vercel_default_replaces_legacy_history_with_noop(self):
        original = AsyncMock(return_value={"legacy-hash"})
        spr._ORIGINAL_GET_RECENT_CHANNEL_JOB_HASHES = original
        spr.core.get_recent_channel_job_hashes = original
        spr.core.Config.ENABLE_TELEGRAM_CHANNELS = True

        with patch.dict(
            os.environ,
            {"VERCEL": "1", "VERCEL_ENABLE_TELEGRAM_SOURCES": ""},
            clear=False,
        ):
            spr._configure_serverless_source_policy()
            result = await spr.core.get_recent_channel_job_hashes()

        self.assertFalse(spr.core.Config.ENABLE_TELEGRAM_CHANNELS)
        self.assertEqual(result, set())
        original.assert_not_awaited()

    async def test_vercel_explicit_opt_in_restores_legacy_history(self):
        original = AsyncMock(return_value={"legacy-hash"})
        spr._ORIGINAL_GET_RECENT_CHANNEL_JOB_HASHES = original
        spr.core.get_recent_channel_job_hashes = spr._empty_recent_channel_job_hashes
        spr.core.Config.ENABLE_TELEGRAM_CHANNELS = False

        with patch.dict(
            os.environ,
            {"VERCEL": "1", "VERCEL_ENABLE_TELEGRAM_SOURCES": "true"},
            clear=False,
        ):
            spr._configure_serverless_source_policy()
            result = await spr.core.get_recent_channel_job_hashes()

        self.assertTrue(spr.core.Config.ENABLE_TELEGRAM_CHANNELS)
        self.assertEqual(result, {"legacy-hash"})
        original.assert_awaited_once()

    async def test_non_vercel_restores_legacy_history(self):
        original = AsyncMock(return_value={"legacy-hash"})
        spr._ORIGINAL_GET_RECENT_CHANNEL_JOB_HASHES = original
        spr.core.get_recent_channel_job_hashes = spr._empty_recent_channel_job_hashes

        with patch.dict(
            os.environ,
            {"VERCEL": "", "VERCEL_ENABLE_TELEGRAM_SOURCES": ""},
            clear=False,
        ):
            spr._configure_serverless_source_policy()
            result = await spr.core.get_recent_channel_job_hashes()

        self.assertEqual(result, {"legacy-hash"})
        original.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
