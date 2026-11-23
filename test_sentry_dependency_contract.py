"""Regression: serverless Sentry initialization must retain the 2.x option contract."""
import unittest
from pathlib import Path

class SentryDependencyContractTests(unittest.TestCase):
    def test_sentry_major_three_is_excluded_until_migration(self):
        requirements = (Path(__file__).parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
        sentry = [line.strip() for line in requirements if line.strip().startswith("sentry-sdk")]
        self.assertEqual(sentry, ["sentry-sdk>=2.0,<3"])
        import tomllib
        project = tomllib.loads((Path(__file__).parent / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("sentry-sdk>=2.0,<3", project["project"]["dependencies"])
