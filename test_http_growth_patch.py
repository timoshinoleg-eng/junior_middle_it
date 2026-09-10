import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import http_growth_patch as bridge
from growth_store import GrowthStoreUnavailable


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit=-1):
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
        conn = bridge.RemoteGrowthConnection(
            "https://example.supabase.co/functions/v1/growth-proxy", "secret"
        )
        response = FakeResponse(
            {"rows": [[1, "start"], [2, "setup_done"]], "columns": ["id", "name"], "rowcount": 2}
        )
        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["key"] = request.headers.get("X-growth-key") or request.headers.get("X-Growth-Key")
            captured["timeout"] = timeout
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
        self.assertEqual(captured["timeout"], 15.0)

    def test_telegram_mode_uses_separate_exact_endpoint_and_header(self):
        endpoint = "https://x.supabase.co/functions/v1/interactive-growth-proxy"
        conn = bridge.RemoteGrowthConnection(endpoint, "123:bot-token", auth_mode="telegram")
        captured = {}

        def fake_urlopen(request, timeout):
            captured["token"] = request.headers.get("X-telegram-bot-token") or request.headers.get("X-Telegram-Bot-Token")
            captured["growth_key"] = request.headers.get("X-growth-key") or request.headers.get("X-Growth-Key")
            return FakeResponse({"rows": [], "columns": [], "rowcount": 0})

        with patch.object(bridge, "urlopen", side_effect=fake_urlopen):
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM growth_user_settings WHERE user_id=%s", (77,))
        self.assertEqual(captured["token"], "123:bot-token")
        self.assertIsNone(captured["growth_key"])

    def test_telegram_mode_never_forwards_schema_bootstrap(self):
        conn = bridge.RemoteGrowthConnection(
            "https://x.supabase.co/functions/v1/interactive-growth-proxy",
            "123:bot-token",
            auth_mode="telegram",
        )
        with patch.object(bridge, "urlopen") as urlopen_mock:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS growth_runtime_state (user_id BIGINT)"
                )
                self.assertEqual(cur.rowcount, 0)
        urlopen_mock.assert_not_called()

    def test_proxy_urls_are_pinned_to_exact_supabase_edge_paths(self):
        good = "https://x.supabase.co/functions/v1/growth-proxy"
        interactive = "https://x.supabase.co/functions/v1/interactive-growth-proxy"
        self.assertEqual(bridge._safe_growth_proxy_url(good), good)
        self.assertEqual(bridge._safe_interactive_proxy_url(interactive), interactive)
        self.assertEqual(bridge._safe_growth_proxy_url(interactive), "")
        self.assertEqual(bridge._safe_interactive_proxy_url(good), "")
        for bad in (
            "http://x.supabase.co/functions/v1/growth-proxy",
            "https://example.com/functions/v1/growth-proxy",
            "https://x.supabase.co/functions/v1/other",
            "https://x.supabase.co/functions/v1/growth-proxy/public-jobs",
        ):
            self.assertEqual(bridge._safe_growth_proxy_url(bad), "")

    def test_patch_only_activates_for_endpoint_matching_auth_mode(self):
        with patch.dict(os.environ, {"GROWTH_DATABASE_URL": "postgresql://db", "DATABASE_URL": ""}, clear=False):
            self.assertFalse(bridge.install())
        with patch.dict(os.environ, {"GROWTH_DATABASE_URL": "https://x.supabase.co/functions/v1/other"}, clear=False):
            self.assertFalse(bridge.install())
        with patch.dict(
            os.environ,
            {
                "GROWTH_DATABASE_URL": "https://x.supabase.co/functions/v1/growth-proxy",
                "GROWTH_HTTP_AUTH_MODE": "render",
            },
            clear=False,
        ):
            self.assertTrue(bridge.install())
        with patch.dict(
            os.environ,
            {
                "GROWTH_DATABASE_URL": "https://x.supabase.co/functions/v1/interactive-growth-proxy",
                "GROWTH_HTTP_AUTH_MODE": "telegram",
            },
            clear=False,
        ):
            self.assertTrue(bridge.install())

    def test_request_and_parameter_limits_fail_before_network(self):
        conn = bridge.RemoteGrowthConnection(
            "https://x.supabase.co/functions/v1/growth-proxy", "secret"
        )
        with patch.object(bridge, "urlopen") as urlopen_mock:
            with self.assertRaises(GrowthStoreUnavailable):
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id FROM growth_user_settings WHERE user_id IN (" +
                        ",".join("%s" for _ in range(bridge.MAX_PARAMS + 1)) + ")",
                        tuple(range(bridge.MAX_PARAMS + 1)),
                    )
            with self.assertRaises(GrowthStoreUnavailable):
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id FROM growth_user_settings WHERE user_id=%s",
                        ("x" * bridge.MAX_REQUEST_BYTES,),
                    )
        urlopen_mock.assert_not_called()

    def test_credential_is_required_and_bounded(self):
        endpoint = "https://x.supabase.co/functions/v1/growth-proxy"
        with self.assertRaises(GrowthStoreUnavailable):
            bridge.RemoteGrowthConnection(endpoint, "")
        with self.assertRaises(GrowthStoreUnavailable):
            bridge.RemoteGrowthConnection(endpoint, "x" * 257)


if __name__ == "__main__":
    unittest.main()
