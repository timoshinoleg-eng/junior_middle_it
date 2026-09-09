import json
import os
import unittest
from unittest.mock import patch

import edge_payload_writer as writer


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit=-1):
        return self._payload


class EdgePayloadWriterTests(unittest.TestCase):
    def test_writer_url_is_https_and_supabase_pinned(self):
        self.assertEqual(writer._writer_url("https://example.com/functions/v1/x"), "")
        self.assertEqual(writer._writer_url("http://x.supabase.co/functions/v1/x"), "")
        self.assertEqual(
            writer._writer_url("https://project.supabase.co/functions/v1/growth-proxy"),
            "https://project.supabase.co/functions/v1/growth-proxy/job-payloads",
        )
        self.assertEqual(
            writer._release_url("https://project.supabase.co/functions/v1/growth-proxy"),
            "https://project.supabase.co/functions/v1/growth-proxy/job-payloads/release",
        )

    def test_claim_returns_created_state_and_keeps_token_out_of_body(self):
        token = "123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF"
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode("utf-8")
            captured["token"] = request.headers.get("X-telegram-bot-token") or request.headers.get("X-Telegram-Bot-Token")
            return FakeResponse({"ok": True, "hash": "abc123", "created": True})

        env = {
            "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy",
            "TELEGRAM_BOT_TOKEN": token,
        }
        with patch.dict(os.environ, env, clear=True), patch.object(writer, "urlopen", side_effect=fake_urlopen):
            self.assertTrue(writer.save_job_payload_edge("abc123", {"title": "Junior Python"}))

        self.assertEqual(captured["token"], token)
        self.assertNotIn(token, captured["body"])
        self.assertEqual(captured["url"], "https://project.supabase.co/functions/v1/growth-proxy/job-payloads")

    def test_existing_hash_returns_false(self):
        env = {
            "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy",
            "TELEGRAM_BOT_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            writer,
            "urlopen",
            return_value=FakeResponse({"ok": True, "hash": "abc123", "created": False}),
        ):
            self.assertFalse(writer.save_job_payload_edge("abc123", {"title": "QA"}))

    def test_release_uses_compensation_endpoint(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"ok": True, "hash": "abc123", "released": True})

        env = {
            "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy",
            "TELEGRAM_BOT_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(writer, "urlopen", side_effect=fake_urlopen):
            self.assertTrue(writer.release_job_payload_edge("abc123"))

        self.assertEqual(
            captured["url"],
            "https://project.supabase.co/functions/v1/growth-proxy/job-payloads/release",
        )
        self.assertEqual(captured["body"], {"hash": "abc123"})

    def test_writer_requires_valid_hash(self):
        env = {
            "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy",
            "TELEGRAM_BOT_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                writer.save_job_payload_edge("../bad", {})
            with self.assertRaises(ValueError):
                writer.release_job_payload_edge("../bad")


if __name__ == "__main__":
    unittest.main()
