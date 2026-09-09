import json
import os
import unittest
from pathlib import Path
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

    def test_vercel_uses_other_preset_and_file_based_python_routes(self):
        config = json.loads(Path('vercel.json').read_text(encoding='utf-8'))
        self.assertIsNone(config.get('framework'))

        rewrites = {
            item['source']: item['destination']
            for item in config.get('rewrites', [])
        }
        self.assertEqual(rewrites.get('/'), '/api/site')
        self.assertEqual(rewrites.get('/robots.txt'), '/api/robots')
        self.assertNotIn('/api/health', rewrites)
        self.assertNotIn('/api/cron', rewrites)

        functions = config.get('functions', {})
        self.assertIn('api/**/*.py', functions)
        self.assertEqual(functions['api/**/*.py'].get('maxDuration'), 300)


if __name__ == '__main__':
    unittest.main()
