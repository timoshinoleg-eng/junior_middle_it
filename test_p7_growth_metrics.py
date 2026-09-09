import unittest
from unittest.mock import patch

import p7_growth_metrics as metrics
import public_acquisition_runtime as p7


class P7GrowthMetricsTests(unittest.TestCase):
    def test_pct_handles_zero_and_rounding(self):
        self.assertEqual(metrics._pct(1, 4), 25.0)
        self.assertEqual(metrics._pct(2, 3), 66.7)
        self.assertEqual(metrics._pct(1, 0), 0.0)

    def test_high_intent_contract_contains_first_value(self):
        self.assertIn("first_value_delivered", metrics.HIGH_INTENT_EVENTS)
        self.assertIn("save_job", metrics.HIGH_INTENT_EVENTS)

    def test_p7_runtime_uses_cohort_metrics_for_postgres(self):
        db = object.__new__(p7.DatabaseConnection)
        db._growth_store = object()
        expected = {"starts": 3, "activated_users": 2}
        with patch.object(p7, "compute_growth_stats", return_value=expected) as compute:
            self.assertIs(db.growth_stats(14), expected)
        compute.assert_called_once_with(db._growth_store, 14)


if __name__ == "__main__":
    unittest.main()
