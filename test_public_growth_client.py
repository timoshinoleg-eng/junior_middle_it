import json
import unittest
from unittest.mock import patch

import public_growth_client as client


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit=-1):
        return self.payload


class PublicGrowthClientTests(unittest.TestCase):
    def test_rejects_non_supabase_hosts(self):
        self.assertEqual(client.load_recent_public_jobs_http("https://example.com/proxy"), [])
        self.assertEqual(client.load_recent_public_jobs_http("http://x.supabase.co/proxy"), [])

    def test_normalizes_public_job_rows(self):
        payload = {
            "rows": [
                {
                    "hash": "abc123",
                    "payload": {"title": "Junior Python", "company": "Acme"},
                    "updated_at": "2026-09-09T00:00:00Z",
                }
            ]
        }
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse(payload)

        with patch.object(client, "urlopen", side_effect=fake_urlopen):
            jobs = client.load_recent_public_jobs_http(
                "https://project.supabase.co/functions/v1/growth-proxy",
                days=7,
                limit=20,
            )
        self.assertEqual(jobs[0]["hash"], "abc123")
        self.assertEqual(jobs[0]["title"], "Junior Python")
        self.assertIn("/public-jobs?days=7&limit=20", captured["url"])


if __name__ == "__main__":
    unittest.main()
