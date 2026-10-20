import asyncio
import unittest

from copilot import enrich


HTML = """
<html><head><style>.a{color:red}</style><script>var x = 1;</script></head>
<body><h1>Junior Python Developer</h1><p>Python, FastAPI, PostgreSQL.</p>
<p>Remote, 1-2 years of experience.</p></body></html>
"""

GREENHOUSE_JSON = (
    b'{"title": "Junior Python Developer", "content": '
    b'"<p>Python and FastAPI, 1-2 years.</p>"}'
)


class EnrichTextTests(unittest.TestCase):
    def test_html_is_reduced_to_readable_text(self):
        text = enrich.extract_text_from_html(HTML)
        self.assertIn("Junior Python Developer", text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("var x", text)

    def test_empty_html_is_safe(self):
        self.assertEqual(enrich.extract_text_from_html(""), "")


class EnrichJobTests(unittest.TestCase):
    def setUp(self):
        enrich._robots_cache.clear()
        enrich._robots_cache["https://example.com"] = ()
        enrich._robots_cache["https://boards.greenhouse.io"] = ()

    def tearDown(self):
        enrich._robots_cache.clear()

    def test_invalid_url_is_rejected(self):
        self.assertIsNone(asyncio.run(enrich.enrich_job("not-a-url")))
        self.assertIsNone(asyncio.run(enrich.enrich_job("ftp://example.com/x")))

    def test_ats_json_is_preferred(self):
        calls = []

        def fetcher(url, timeout):
            calls.append(url)
            return GREENHOUSE_JSON

        result = asyncio.run(
            enrich.enrich_job(
                "https://boards.greenhouse.io/acme/jobs/12345",
                fetcher=fetcher,
            )
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.source, enrich.SOURCE_ATS_JSON)
        self.assertIn("FastAPI", result.text)
        self.assertTrue(any("boards-api.greenhouse.io" in call for call in calls))

    def test_html_page_is_used_when_ats_is_not_available(self):
        def fetcher(url, timeout):
            if "r.jina.ai" in url:
                raise OSError("proxy unavailable")
            return HTML.encode("utf-8")

        result = asyncio.run(enrich.enrich_job("https://example.com/jobs/1", fetcher=fetcher))
        self.assertIsNotNone(result)
        self.assertIn(result.source, (enrich.SOURCE_HTML, enrich.SOURCE_TEXT_PROXY))

    def test_robots_disallow_stops_the_fetch(self):
        enrich._robots_cache["https://example.com"] = ("/jobs",)
        calls = []

        def fetcher(url, timeout):
            calls.append(url)
            return HTML.encode("utf-8")

        result = asyncio.run(enrich.enrich_job("https://example.com/jobs/1", fetcher=fetcher))
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_total_failure_degrades_to_none(self):
        def fetcher(url, timeout):
            raise OSError("network down")

        result = asyncio.run(enrich.enrich_job("https://example.com/jobs/1", fetcher=fetcher))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
