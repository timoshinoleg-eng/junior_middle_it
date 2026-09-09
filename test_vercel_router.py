import os
import unittest
from unittest.mock import patch

from api.index import resolve_route
from serverless_health import readiness


class VercelRouterTests(unittest.TestCase):
    def test_explicit_rewrite_route_wins(self):
        self.assertEqual(resolve_route('/api/index?route=health'), 'health')
        self.assertEqual(resolve_route('/api/index?route=cron'), 'cron')
        self.assertEqual(resolve_route('/api/index?route=site&category=backend'), 'site')
        self.assertEqual(resolve_route('/api/index?route=robots'), 'robots')

    def test_public_path_fallbacks_are_unambiguous(self):
        self.assertEqual(resolve_route('/'), 'site')
        self.assertEqual(resolve_route('/robots.txt'), 'robots')
        self.assertEqual(resolve_route('/api/health'), 'health')
        self.assertEqual(resolve_route('/api/cron'), 'cron')
        self.assertEqual(resolve_route('/api/index'), '')
        self.assertEqual(resolve_route('/api/unknown'), '')

    def test_unknown_explicit_route_does_not_fall_into_cron(self):
        self.assertEqual(resolve_route('/api/index?route=unknown'), '')
        self.assertEqual(resolve_route('/api/index?route='), '')

    def test_health_contract_does_not_require_importing_cron_runtime(self):
        env = {
            'TELEGRAM_BOT_TOKEN': 'token',
            'CHANNEL_ID': '@channel',
            'CRON_SECRET': 'cron',
            'BOT_USERNAME': 'junior_jobs_channel_bot',
        }
        with patch.dict(os.environ, env, clear=True):
            state = readiness()
        self.assertTrue(state['collector_ready'])
        self.assertFalse(state['durable_growth'])
        self.assertFalse(state['ok'])


if __name__ == '__main__':
    unittest.main()
