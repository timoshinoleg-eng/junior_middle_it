"""Tests for v6.5 extra job sources (offline + optional live)."""
import os
import unittest
import xml.etree.ElementTree as ET

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
    _first_text,
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
        # first call after threshold → skip and arm cooldown
        self.assertTrue(SOURCE_HEALTH.should_skip(name, max_fails=3))
        self.assertFalse(SOURCE_HEALTH.should_skip(name, max_fails=0))
        # burn cooldown (max_fails-1 more skips), then probe allowed
        for _ in range(2):
            self.assertTrue(SOURCE_HEALTH.should_skip(name, max_fails=3))
        self.assertFalse(SOURCE_HEALTH.should_skip(name, max_fails=3))  # probe
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

    def test_remoteok_epoch_is_used_when_date_is_missing(self):
        jobs = _parse_remoteok_json(
            [
                {
                    "id": "1",
                    "position": "Junior Python Engineer",
                    "company": "Acme",
                    "url": "https://x",
                    "date": "",
                    "epoch": 1789012345,
                }
            ]
        )
        self.assertEqual(jobs[0]["published"], 1789012345)

    def test_xml_text_lookup_accepts_case_and_namespace_variants(self):
        item = ET.fromstring(
            '<job xmlns:ns="urn:test"><ns:pubDate>2026-09-25T10:00:00Z</ns:pubDate></job>'
        )
        self.assertEqual(
            _first_text(item, "pubDate"),
            "2026-09-25T10:00:00Z",
        )

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




class HabrCareerAdapterTests(unittest.TestCase):
    """Offline contract tests: never touch the live RSS endpoint in CI."""

    @staticmethod
    def _rss(*, tag="junior", remote=True, url="https://career.habr.com/vacancies/1000170621",
             salary="От 75 000 ₽ до 115 000 ₽", company="ТЛ Групп", title="Backend-разработчик (Python/Django)"):
        remote_text = "Можно удалённо." if remote else "Работа в офисе."
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <channel><item>
            <title>Требуется «{title}» (Москва)</title>
            <description><![CDATA[Компания «{company}» ищет хорошего специалиста.
               Москва (Россия). {salary}. Полный рабочий день.
               {remote_text} Требуемые навыки: #{tag}, #Python, #Django, #C#.]]></description>
            <dc:creator>{company}</dc:creator>
            <pubDate>Mon, 23 Nov 2026 16:25:06 +0300</pubDate>
            <link>{url}</link>
          </item></channel>
        </rss>"""
        return xml.encode("utf-8")

    def test_parses_title_company_salary_skills_and_date(self):
        from job_sources_habr import parse_habr_rss

        jobs = parse_habr_rss(self._rss(), "junior")
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "Backend-разработчик (Python/Django)")
        self.assertEqual(job["company"], "ТЛ Групп")
        self.assertEqual(job["salary"], "От 75 000 ₽ до 115 000 ₽")
        self.assertEqual(job["source"], "RSS:Habr Career")
        self.assertEqual(job["published"], "Mon, 23 Nov 2026 16:25:06 +0300")
        self.assertIn("Python", job["tags"])
        self.assertIn("C#", job["tags"])
        self.assertNotIn("worldwide", job["location"].lower())

        from growth_utils import apply_editorial_quality_gate

        self.assertEqual(apply_editorial_quality_gate(job)["quality_gate_status"], "passed")
        self.assertNotEqual(job["remote_scope"], "worldwide")

    def test_currency_ranges_eur_usd_rub_and_single_sided(self):
        from job_sources_habr import parse_habr_rss

        for raw, expected in (
            ("От 2 100 $ до 8 000 $", "От 2 100 $ до 8 000 $"),
            ("От 3 000 € до 5 000 €", "От 3 000 € до 5 000 €"),
            ("От 80 000 ₽ до 140 000 ₽", "От 80 000 ₽ до 140 000 ₽"),
            ("До 4 500 $", "До 4 500 $"),
        ):
            with self.subTest(raw=raw):
                job = parse_habr_rss(self._rss(salary=raw), "junior")[0]
                self.assertEqual(job["salary"], expected)

    def test_russian_senior_titles_veto_middle_tag(self):
        from job_sources_habr import parse_habr_rss

        for title in (
            "Старший разработчик серверной части (C#)",
            "Ведущий системный аналитик",
            "Главный инженер по тестированию",
            "старший Python-разработчик",
        ):
            with self.subTest(title=title):
                self.assertEqual(
                    parse_habr_rss(self._rss(tag="middle", title=title), "middle"),
                    [],
                )
        accepted = parse_habr_rss(
            self._rss(tag="middle", title="Middle C# разработчик"),
            "middle",
        )
        self.assertEqual(len(accepted), 1)

    def test_middle_non_it_titles_do_not_enter_public_channel(self):
        from job_sources_habr import parse_habr_rss
        from growth_utils import passes_channel_tracks
        from job_classifier import JobClassifier

        allowed = ["development", "qa", "devops", "data", "design", "pm", "security"]
        for title, category in (
            ("Менеджер по продажам B2B (банковские продукты)", "sales"),
            ("SMM-специалист", "marketing"),
        ):
            with self.subTest(title=title):
                self.assertEqual(
                    parse_habr_rss(self._rss(tag="middle", title=title), "middle"),
                    [],
                )
                # Independently check the existing channel-track fallback.
                job = {
                    "title": title,
                    "description": "Можно удалённо. #middle",
                    "location": "Remote",
                }
                self.assertEqual(JobClassifier().classify(job), category)
                self.assertFalse(passes_channel_tracks(
                    {**job, "category": category}, allowed
                ))

    def test_requires_explicit_grade_and_per_item_remote_evidence(self):
        from job_sources_habr import parse_habr_rss

        self.assertEqual(parse_habr_rss(self._rss(tag="middle"), "junior"), [])
        self.assertEqual(parse_habr_rss(self._rss(remote=False), "junior"), [])
        self.assertEqual(parse_habr_rss(self._rss(tag="senior"), "junior"), [])
        self.assertEqual(len(parse_habr_rss(self._rss(tag="intern"), "intern")), 1)
        with self.assertRaises(ValueError):
            parse_habr_rss(self._rss(), "senior")

    def test_rejects_non_habr_or_non_https_vacancy_links(self):
        from job_sources_habr import parse_habr_rss

        for link in (
            "https://elsewhere.example/vacancies/1000170621",
            "http://career.habr.com/vacancies/1000170621",
            "https://career.habr.com/companies/acme",
        ):
            with self.subTest(link=link):
                self.assertEqual(parse_habr_rss(self._rss(url=link), "junior"), [])

    def test_no_salary_is_marked_unknown(self):
        from job_sources_habr import parse_habr_rss

        job = parse_habr_rss(self._rss(salary=""), "junior")[0]
        self.assertEqual(job["salary"], "Не указана")

    def test_disabled_by_default_and_enabled_with_flag(self):
        from unittest.mock import patch

        with patch.dict(os.environ, {"ENABLE_HABR_RSS": "false"}):
            names = [name for _, name in get_extra_fetchers()]
            self.assertNotIn("Habr Career RSS", names)
        with patch.dict(os.environ, {"ENABLE_HABR_RSS": "true"}):
            names = [name for _, name in get_extra_fetchers()]
            self.assertIn("Habr Career RSS", names)

    def test_independent_per_grade_failure_and_no_duplicate_urls(self):
        from unittest.mock import Mock, patch
        import requests
        from job_sources_habr import fetch_habr_career

        response = Mock()
        response.content = self._rss()
        response.raise_for_status.return_value = None

        def fake_get(url, *, params, headers, timeout):
            self.assertEqual(timeout, 15)
            self.assertEqual(params["remote"], "true")
            if params["qid"] == 1:
                raise requests.Timeout("simulated timeout")
            return response

        with patch("job_sources_habr.requests.get", side_effect=fake_get) as get:
            jobs = fetch_habr_career()
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "ТЛ Групп")


if __name__ == "__main__":
    unittest.main()
