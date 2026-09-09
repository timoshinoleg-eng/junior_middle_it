import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import http_growth_patch as bridge


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class HttpGrowthPatchTests(unittest.TestCase):
    def test_placeholder_conversion_and_json_wrappers(self):
        query, params = bridge._convert_placeholders(
            "SELECT * FROM growth_events WHERE user_id=%s AND name=%s",
            (123, SimpleNamespace(obj={"event": "start"})),
        )
        self.assertEqual(
            query,
            "SELECT * FROM growth_events WHERE user_id=$1 AND name=$2",
        )
        self.assertEqual(params, [123, {"event": "start"}])

    def test_remote_cursor_preserves_tuple_fetch_contract(self):
        conn = bridge.RemoteGrowthConnection("https://example.supabase.co/functions/v1/growth-proxy", "secret")
        response = FakeResponse(
            {"rows": [[1, "start"], [2, "setup_done"]], "columns": ["id", "name"], "rowcount": 2}
        )
        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["key"] = request.headers.get("X-growth-key") or request.headers.get("X-Growth-Key")
            return response

        with patch.object(bridge, "urlopen", side_effect=fake_urlopen):
            with conn.cursor() as cur:
                cur.execute("SELECT id,name FROM growth_events WHERE user_id=%s", (77,))
                self.assertEqual(cur.fetchone(), (1, "start"))
                self.assertEqual(cur.fetchall(), [(2, "setup_done")])
                self.assertEqual(cur.rowcount, 2)
        self.assertEqual(captured["body"]["query"], "SELECT id,name FROM growth_events WHERE user_id=$1")
        self.assertEqual(captured["body"]["params"], [77])
        self.assertEqual(captured["key"], "secret")

    def test_patch_only_activates_for_https_endpoint(self):
        with patch.dict(os.environ, {"GROWTH_DATABASE_URL": "postgresql://db", "DATABASE_URL": ""}, clear=False):
            self.assertFalse(bridge.install())
        with patch.dict(os.environ, {"GROWTH_DATABASE_URL": "https://x.supabase.co/functions/v1/growth-proxy"}, clear=False):
            self.assertTrue(bridge.install())


if __name__ == "__main__":
    unittest.main()
