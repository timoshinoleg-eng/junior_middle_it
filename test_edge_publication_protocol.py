import json
import os
import unittest
from unittest.mock import patch

import edge_payload_writer as writer


CLAIM_TOKEN = "11111111-1111-1111-1111-111111111111"


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit=-1):
        return self._payload


class EdgePublicationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy",
            "TELEGRAM_BOT_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF",
        }

    def test_compact_payload_bounds_large_source_fields_and_keeps_public_contract(self):
        raw = {
            "title": "Junior Python",
            "company": "Example",
            "description": "x" * 500_000,
            "tags": ["tag" * 1000] * 100,
            "quality_gate_status": "passed",
            "url_preflight_status": "passed",
            "primary_track": "development",
            "specialization_tags": ["backend", "python"],
            "geo_restriction": "Europe",
            "remote_scope": "country_restricted",
            "level_source": "explicit_title",
            "level_confidence": 95,
        }
        compact = writer.compact_job_payload("abc123", raw)
        self.assertEqual(compact["hash"], "abc123")
        self.assertEqual(compact["quality_gate_status"], "passed")
        self.assertEqual(compact["url_preflight_status"], "passed")
        self.assertEqual(compact["primary_track"], "development")
        self.assertEqual(len(compact["description"]), 1200)
        self.assertLessEqual(len(compact["tags"]), 12)
        self.assertLess(len(json.dumps(compact).encode("utf-8")), 20_000)

    def test_protocol_v2_claim_returns_owned_token_and_sends_compact_payload(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({
                "ok": True,
                "hash": "abc123",
                "created": True,
                "claimed": True,
                "claim_token": CLAIM_TOKEN,
                "publication_state": "pending",
                "lease_expires_at": "2026-09-10T10:00:00Z",
            })

        huge_job = {
            "title": "Junior Python",
            "company": "Example",
            "description": "x" * 500_000,
            "quality_gate_status": "passed",
        }
        with patch.dict(os.environ, self.env, clear=True), patch.object(writer, "urlopen", side_effect=fake_urlopen):
            claim = writer.claim_job_payload_edge("abc123", huge_job)

        self.assertTrue(claim.claimed)
        self.assertEqual(claim.claim_token, CLAIM_TOKEN)
        self.assertEqual(claim.publication_state, "pending")
        self.assertEqual(captured["body"]["protocol_version"], 2)
        self.assertEqual(len(captured["body"]["payload"]["description"]), 1200)
        self.assertLess(len(json.dumps(captured["body"]).encode("utf-8")), writer.MAX_REQUEST_BYTES)

    def test_existing_published_hash_is_not_claimed(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            writer,
            "urlopen",
            return_value=FakeResponse({
                "ok": True,
                "hash": "abc123",
                "created": False,
                "claimed": False,
                "publication_state": "published",
            }),
        ):
            claim = writer.claim_job_payload_edge("abc123", {"title": "QA"})
        self.assertFalse(claim.claimed)
        self.assertEqual(claim.publication_state, "published")
        self.assertEqual(claim.claim_token, "")

    def test_begin_and_publish_use_owned_state_endpoints(self):
        requests = []

        def fake_urlopen(request, timeout):
            body = json.loads(request.data.decode("utf-8"))
            requests.append((request.full_url, body))
            if request.full_url.endswith("/sending"):
                return FakeResponse({"ok": True, "hash": "abc123", "sending": True})
            return FakeResponse({"ok": True, "hash": "abc123", "published": True})

        with patch.dict(os.environ, self.env, clear=True), patch.object(writer, "urlopen", side_effect=fake_urlopen):
            self.assertTrue(writer.begin_job_payload_send_edge("abc123", CLAIM_TOKEN))
            self.assertTrue(writer.mark_job_payload_published_edge("abc123", CLAIM_TOKEN))

        self.assertEqual(requests[0][0], self.env["GROWTH_PUBLIC_URL"] + "/job-payloads/sending")
        self.assertEqual(requests[1][0], self.env["GROWTH_PUBLIC_URL"] + "/job-payloads/published")
        self.assertEqual(requests[0][1], {"hash": "abc123", "claim_token": CLAIM_TOKEN})
        self.assertEqual(requests[1][1], {"hash": "abc123", "claim_token": CLAIM_TOKEN})

    def test_claimed_response_requires_valid_owned_token(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            writer,
            "urlopen",
            return_value=FakeResponse({
                "ok": True,
                "hash": "abc123",
                "created": True,
                "claimed": True,
                "claim_token": "bad",
                "publication_state": "pending",
            }),
        ):
            with self.assertRaises(ValueError):
                writer.claim_job_payload_edge("abc123", {"title": "QA"})

    def test_transition_rejects_bad_claim_token_before_network(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(writer, "urlopen") as urlopen_mock:
            with self.assertRaises(ValueError):
                writer.begin_job_payload_send_edge("abc123", "../bad")
            with self.assertRaises(ValueError):
                writer.mark_job_payload_published_edge("abc123", "../bad")
        urlopen_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
