"""Regression tests for Stage 3 (collection hardening) — B13/B21/B22/B23/B24.

No network: DNS, requests and fetchers are mocked. SSRF tests assert that
private/loopback ranges are never requested, including via redirects.
"""
import asyncio  # noqa: F401  (kept for parity with other stage tests)
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import http_guard
import http_cache
import ats_scrapers_adapter
import channel_bot
from channel_bot import (
    Config,
    DatabaseConnection,
    job_is_stale,
    parse_published_date,
    run_v7_migration,
    safe_fetch_with_retry,
    warm_source_health_from_db,
)

PY = sys.executable


class IpBlockingTests(unittest.TestCase):
    def test_private_and_loopback_ips_are_blocked(self):
        for ip in ("127.0.0.1", "10.0.0.5", "172.16.3.3", "192.168.1.1",
                   "169.254.169.254", "::1", "0.0.0.0"):
            with self.subTest(ip=ip):
                self.assertTrue(http_guard._is_blocked_ip(ip))

    def test_public_ip_is_allowed(self):
        self.assertFalse(http_guard._is_blocked_ip("8.8.8.8"))
        self.assertFalse(http_guard._is_blocked_ip("93.184.216.34"))

    def test_resolve_blocks_when_any_ip_is_private(self):
        fake = [(None, None, None, None, ("127.0.0.1", 443))]
        with mock.patch.object(http_guard.socket, "getaddrinfo", return_value=fake):
            self.assertEqual(http_guard.resolve_public_ips("example.host"), [])

    def test_validate_url_rejects_private_ip_for_allowlisted_host(self):
        fake = [(None, None, None, None, ("10.1.2.3", 443))]
        with mock.patch.object(http_guard, "_host_allowed", return_value=True), \
             mock.patch.object(http_guard.socket, "getaddrinfo", return_value=fake):
            self.assertFalse(http_guard.validate_url("https://greenhouse.io/x"))


class GuardedRequestTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

        def close(self):
            return None

    def test_host_outside_allowlist_never_reaches_network(self):
        with mock.patch.object(http_guard.requests, "request") as request:
            result = http_guard.guarded_request(
                "HEAD", "https://not-allowlisted.example/x", timeout=5
            )
        self.assertIsNone(result)
        request.assert_not_called()

    def test_redirect_to_metadata_endpoint_is_blocked(self):
        # first hop passes (allowlisted public host), second hop is the cloud
        # metadata IP — must be stopped BEFORE issuing the request (B21)
        calls = []

        def fake_validate(url):
            calls.append(url)
            return "169.254.169.254" not in url

        redirect = self.FakeResponse(302, {"Location": "http://169.254.169.254/latest/meta-data/"})
        with mock.patch.object(http_guard, "validate_url", side_effect=fake_validate), \
             mock.patch.object(http_guard.requests, "request",
                               return_value=redirect) as request:
            result = http_guard.guarded_request(
                "HEAD", "https://greenhouse.io/redirector", timeout=5
            )
        self.assertIsNone(result)
        self.assertEqual(request.call_count, 1)  # second hop never requested

    def test_clean_redirect_chain_followed(self):
        hop1 = self.FakeResponse(302, {"Location": "https://boards.greenhouse.io/final"})
        hop2 = self.FakeResponse(200)
        with mock.patch.object(http_guard, "validate_url", return_value=True), \
             mock.patch.object(http_guard.requests, "request",
                               side_effect=[hop1, hop2]) as request:
            result = http_guard.guarded_request("HEAD", "https://greenhouse.io/a", timeout=5)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(request.call_count, 2)

    def test_redirect_loop_bounded(self):
        redirect = self.FakeResponse(302, {"Location": "https://greenhouse.io/a"})
        with mock.patch.object(http_guard, "validate_url", return_value=True), \
             mock.patch.object(http_guard.requests, "request",
                               return_value=redirect) as request:
            result = http_guard.guarded_request(
                "HEAD", "https://greenhouse.io/a", timeout=5, max_redirects=3
            )
        self.assertIsNone(result)
        self.assertEqual(request.call_count, 4)  # initial + 3 hops, then stop


class FreshnessGateTests(unittest.TestCase):
    def test_parse_formats(self):
        self.assertIsNotNone(parse_published_date("2026-08-01T10:00:00Z"))
        self.assertIsNotNone(parse_published_date("2026-08-01"))
        self.assertIsNotNone(parse_published_date("1754000000"))           # epoch s
        self.assertIsNotNone(parse_published_date("1754000000000"))         # epoch ms
        self.assertIsNone(parse_published_date(""))
        self.assertIsNone(parse_published_date("недавно"))
        self.assertIsNone(parse_published_date(None))

    def test_stale_job_dropped_fresh_kept(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertTrue(job_is_stale({"published": old}, max_age_days=14, now=now))
        self.assertFalse(job_is_stale({"published": fresh}, max_age_days=14, now=now))

    def test_unparseable_date_is_kept(self):
        self.assertFalse(job_is_stale({"published": "unknown"}, max_age_days=14))
        self.assertFalse(job_is_stale({}, max_age_days=14))

    def test_gate_disabled_with_zero(self):
        old = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
        self.assertFalse(job_is_stale({"published": old}, max_age_days=0))

    def test_config_default_is_sane_window(self):
        self.assertTrue(7 <= Config.FRESHNESS_MAX_DAYS <= 14)


class TypedFetchOutcomeTests(unittest.TestCase):
    def test_success_outcome(self):
        with mock.patch.object(channel_bot, "should_skip_source", return_value=False), \
             mock.patch.object(channel_bot.time, "sleep"), \
             mock.patch.object(channel_bot.random, "uniform", return_value=0):
            outcome = safe_fetch_with_retry(lambda: [{"a": 1}], "TestSource", max_retries=1)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["jobs"], [{"a": 1}])
        self.assertEqual(outcome["error"], "")
        self.assertFalse(outcome["skipped"])

    def test_error_outcome_is_not_silent_success(self):
        def boom():
            raise RuntimeError("source exploded")

        with mock.patch.object(channel_bot, "should_skip_source", return_value=False), \
             mock.patch.object(channel_bot.time, "sleep"), \
             mock.patch.object(channel_bot.random, "uniform", return_value=0):
            outcome = safe_fetch_with_retry(boom, "TestSource", max_retries=1)
        self.assertFalse(outcome["ok"])            # B22: [] no longer masquerades as success
        self.assertEqual(outcome["jobs"], [])
        self.assertIn("source exploded", outcome["error"])

    def test_skip_outcome(self):
        with mock.patch.object(channel_bot, "should_skip_source", return_value=True), \
             mock.patch.object(channel_bot, "SOURCE_HEALTH") as health:
            health.fail_streak.return_value = 3
            outcome = safe_fetch_with_retry(lambda: [{"a": 1}], "TestSource")
        self.assertTrue(outcome["skipped"])
        self.assertEqual(outcome["jobs"], [])


class WarmSourceHealthTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseConnection(self.path)
        run_v7_migration(self.db)
        from job_sources_extra import SOURCE_HEALTH
        self.registry = SOURCE_HEALTH
        self._saved = dict(self.registry._fail_streak)

    def tearDown(self):
        self.registry._fail_streak = self._saved
        try:
            self.db.conn.close()
        finally:
            os.unlink(self.path)

    def test_streak_restored_after_cold_start(self):
        # chronological: success, then 3 consecutive failures (newest last)
        self.db.record_source_run("BadSource", 0, "")
        for _ in range(3):
            self.db.record_source_run("BadSource", 0, "HTTP 500")
        self.db.record_source_run("GoodSource", 0, "boom")
        self.db.record_source_run("GoodSource", 5, "")
        warm_source_health_from_db(self.db)
        self.assertEqual(self.registry.fail_streak("BadSource"), 3)   # restored
        self.assertEqual(self.registry.fail_streak("GoodSource"), 0)  # last run ok


class ETagCacheTests(unittest.TestCase):
    def test_conditional_headers_and_304_payload(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        try:
            cache = http_cache.ETagCache(path=path)
            cache.store("https://api.example/x", "etag-1", None, {"jobs": [1, 2]})
            headers = cache.conditional_headers("https://api.example/x")
            self.assertEqual(headers.get("If-None-Match"), "etag-1")

            resp304 = mock.MagicMock(status_code=304, headers={})
            with mock.patch.object(http_cache, "get_cache", return_value=cache), \
                 mock.patch("requests.get", return_value=resp304):
                data = http_cache.fetch_json_cached(
                    "https://api.example/x", headers={}, timeout=5
                )
            self.assertEqual(data, {"jobs": [1, 2]})
            resp304.raise_for_status.assert_not_called()
        finally:
            if os.path.exists(path):
                os.unlink(path)


class MigrateBackfillTests(unittest.TestCase):
    def _legacy_db(self, path, with_jobs_table=True):
        conn = sqlite3.connect(path)
        if with_jobs_table:
            conn.execute(
                "CREATE TABLE posted_jobs (hash TEXT PRIMARY KEY, title TEXT NOT NULL,"
                " company TEXT NOT NULL, level TEXT, url TEXT, source TEXT,"
                " category TEXT DEFAULT 'other',"
                " posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO posted_jobs (hash, title, company, category) VALUES "
                "('h1', 'Junior Python Developer', 'Acme', 'other')"
            )
        conn.commit()
        conn.close()

    def test_backfill_no_longer_selects_missing_description_column(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "migrate_db_module", os.path.join(REPO_ROOT, "migrate_db.py"))
        migrate_db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate_db)

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            self._legacy_db(path)
            import job_classifier
            with mock.patch.object(job_classifier, "classify_job",
                                   return_value="development"):
                ok = migrate_db.backfill_categories(path)
            self.assertTrue(ok)  # previously: "no such column: description"
            conn = sqlite3.connect(path)
            row = conn.execute(
                "SELECT category FROM posted_jobs WHERE hash='h1'").fetchone()
            conn.close()
            self.assertEqual(row[0], "development")
        finally:
            os.unlink(path)

    def test_backfill_exit_code_nonzero_on_failure(self):
        # empty dir -> no posted_jobs table -> SELECT fails -> exit 1 (B13)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        proc = subprocess.run(
            [PY, "migrate_db.py", "--backfill", "--db", path],
            cwd=REPO_ROOT, capture_output=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 1)


class AdapterDegradationTests(unittest.TestCase):
    def test_adapter_absence_falls_back_to_builtin_fetchers(self):
        # In the bot env the package cannot be imported (httpx/bs4 conflict) —
        # the adapter must degrade gracefully and fetchers must keep working.
        self.assertFalse(ats_scrapers_adapter.ATS_SCRAPERS_AVAILABLE)
        self.assertTrue(ats_scrapers_adapter.import_error())

        with mock.patch.object(ats_scrapers_adapter, "fetch_greenhouse",
                               side_effect=AssertionError("must not be called")), \
             mock.patch.object(Config, "GREENHOUSE_BOARDS", "acme"), \
             mock.patch.object(channel_bot.http_cache, "fetch_json_cached",
                               return_value={"jobs": []}):
            self.assertEqual(channel_bot.fetch_greenhouse(), [])


if __name__ == "__main__":
    unittest.main()
