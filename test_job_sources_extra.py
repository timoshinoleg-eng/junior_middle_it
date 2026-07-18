"""Tests for v6.5 extra job sources (offline + optional live)."""
import os
import unittest

from job_sources_extra import (
    RSS_FEEDS,
    SOURCE_HEALTH,
    fetch_4dayweek,
    fetch_remoteok_dev,
    fetch_rss_jobs,
    fetch_themuse,
    fetch_working_nomads,
    get_extra_fetchers,
    run_fetcher,
    _parse_remoteok_json,
)


class SourceHealthTests(unittest.TestCase):
    def test_record_and_report(self):
        SOURCE_HEALTH.record("UnitTestSource", 5, elapsed_ms=12)
        snap = {r["name"]: r for r in SOURCE_HEALTH.snapshot()}
        self.assertIn("UnitTestSource", snap)
        self.assertEqual(snap["UnitTestSource"]["fetched"], 5)
        report = SOURCE_HEALTH.format_report()
        self.assertIn("UnitTestSource", report)

    def test_fail_streak_skip(self):
        name = "UnitFailSource"
        SOURCE_HEALTH.record(name, 0, error="boom")
        SOURCE_HEALTH.record(name, 0, error="boom")
        SOURCE_HEALTH.record(name, 0, error="boom")
        self.assertEqual(SOURCE_HEALTH.fail_streak(name), 3)
        self.assertTrue(SOURCE_HEALTH.should_skip(name, max_fails=3))
        self.assertFalse(SOURCE_HEALTH.should_skip(name, max_fails=0))
        # success resets
        SOURCE_HEALTH.record(name, 2, error="")
        self.assertEqual(SOURCE_HEALTH.fail_streak(name), 0)
        self.assertFalse(SOURCE_HEALTH.should_skip(name, max_fails=3))

    def test_run_fetcher_records(self):
        def _ok():
            return [{"title": "x"}]

        jobs = run_fetcher("UnitFake", _ok)
        self.assertEqual(len(jobs), 1)
        names = [r["name"] for r in SOURCE_HEALTH.snapshot()]
        self.assertIn("UnitFake", names)

    def test_extra_fetcher_list(self):
        names = [n for _, n in get_extra_fetchers()]
        self.assertIn("4dayweek", names)
        self.assertIn("The Muse", names)
        self.assertIn("RemoteOK Dev", names)
        self.assertIn("Working Nomads", names)
        self.assertIn("RSS boards", names)

    def test_remoteok_meta_skip(self):
        data = [
            {"legal": "terms", "last_updated": 1},
            {"id": "1", "position": "Junior Python Engineer", "company": "Acme", "url": "https://x"},
        ]
        jobs = _parse_remoteok_json(data)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Junior Python Engineer")

    def test_rss_feeds_no_dead_hosts(self):
        hosts = " ".join(u for _, u, _ in RSS_FEEDS)
        self.assertNotIn("nodejsjobslist.com", hosts)
        self.assertIn("weworkremotely.com", hosts)


@unittest.skipUnless(os.getenv("LIVE_SOURCE_TESTS") == "1", "set LIVE_SOURCE_TESTS=1")
class LiveSourceTests(unittest.TestCase):
    def test_4dayweek_live(self):
        jobs = fetch_4dayweek(max_pages=1, limit=10)
        self.assertIsInstance(jobs, list)
        if jobs:
            self.assertTrue(jobs[0].get("title"))
            self.assertEqual(jobs[0].get("source"), "4dayweek")

    def test_themuse_live(self):
        jobs = fetch_themuse(max_pages=1)
        self.assertIsInstance(jobs, list)

    def test_remoteok_dev_live(self):
        jobs = fetch_remoteok_dev()
        self.assertIsInstance(jobs, list)
        # multi-category should return something on a healthy network
        self.assertGreater(len(jobs), 0)

    def test_working_nomads_live(self):
        jobs = fetch_working_nomads()
        self.assertIsInstance(jobs, list)
        if jobs:
            self.assertEqual(jobs[0].get("source"), "Working Nomads")
            self.assertTrue(jobs[0].get("title"))

    def test_rss_live(self):
        jobs = fetch_rss_jobs(limit_per=5)
        self.assertIsInstance(jobs, list)
        self.assertGreater(len(jobs), 0)


if __name__ == "__main__":
    unittest.main()
