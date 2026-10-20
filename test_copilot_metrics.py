import unittest

from copilot import metrics


class MetricsTests(unittest.TestCase):
    def test_empty_counters_give_zeroes(self):
        report = metrics.build_metrics({})
        self.assertEqual(report.fit_viewed, 0)
        self.assertEqual(report.search_conversion_pct, 0)
        self.assertEqual(report.ai_fallback_pct, 0)

    def test_events_are_grouped(self):
        report = metrics.build_metrics(
            {
                "copilot_fit_viewed": 5,
                "copilot_forward_analyzed": 2,
                "copilot_webapp_fit_viewed": 1,
                "copilot_search_saved": 4,
                "copilot_cv_built": 3,
                "copilot_cv_cached": 2,
                "copilot_hr_link_clicked": 6,
                "copilot_ai_viewed": 9,
                "copilot_ai_failed": 1,
            }
        )
        self.assertEqual(report.fit_viewed, 8)
        self.assertEqual(report.search_saved, 4)
        self.assertEqual(report.cv_built, 3)
        self.assertEqual(report.cv_cached, 2)
        self.assertEqual(report.hr_clicked, 6)
        self.assertEqual(report.ai_viewed, 9)
        self.assertEqual(report.ai_failed, 1)

    def test_search_conversion_is_computed(self):
        report = metrics.build_metrics(
            {"copilot_fit_viewed": 4, "copilot_search_saved": 1}
        )
        self.assertEqual(report.search_conversion_pct, 25)

    def test_ai_fallback_share_is_computed(self):
        report = metrics.build_metrics(
            {"copilot_ai_viewed": 3, "copilot_ai_failed": 1}
        )
        self.assertEqual(report.ai_fallback_pct, 25)

    def test_unknown_events_are_ignored(self):
        report = metrics.build_metrics({"something_else": 100})
        self.assertEqual(report.fit_viewed, 0)

    def test_event_names_are_unique(self):
        names = metrics.all_event_names()
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("copilot_fit_viewed", names)
        self.assertIn("copilot_ai_failed", names)

    def test_report_is_serializable(self):
        payload = metrics.build_metrics({"copilot_fit_viewed": 2}).as_dict()
        self.assertEqual(payload["fit_viewed"], 2)
        self.assertIn("ai_fallback_pct", payload)

    def test_formatting_mentions_the_window_and_the_numbers(self):
        report = metrics.build_metrics(
            {"copilot_fit_viewed": 4, "copilot_search_saved": 2, "copilot_ai_failed": 1}
        )
        text = metrics.format_metrics(report, days=7)
        self.assertIn("7d", text)
        self.assertIn("Разборов: 4", text)
        self.assertIn("50%", text)


if __name__ == "__main__":
    unittest.main()
