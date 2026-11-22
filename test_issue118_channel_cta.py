import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import channel_bot as cb


class ChannelResumeCtaTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_and_modern_with_configured_username(self):
        job = {'hash': 'a' * 32, 'title': 'Junior QA', 'company': 'Example', 'url': 'https://example.org/job', 'source': 'Example'}
        for modern in (False, True):
            bot = SimpleNamespace(send_message=AsyncMock(), get_me=AsyncMock())
            with self.subTest(modern=modern), patch.object(cb.Config, 'ENABLE_MARKDOWN_V2', modern), patch.object(cb.Config, 'BOT_USERNAME', 'ExampleJobsBot'):
                self.assertTrue(await cb._send_job_to_chat(bot, job, '@channel'))
                args = bot.send_message.await_args.kwargs
                buttons = [b if isinstance(b, dict) else b.to_dict() for row in args['reply_markup'].inline_keyboard for b in row]
                self.assertTrue(any(b.get('url') == 'https://t.me/ExampleJobsBot?start=resume_' + job['hash'] for b in buttons))
                self.assertTrue(all(b.get('url') and not b.get('callback_data') for b in buttons))
                self.assertEqual(args['text'], cb.JobMessageFormatter().format_job(job, view_mode='compact', bot_username='ExampleJobsBot').text if modern else cb.format_job_message_legacy(job))
                bot.get_me.assert_not_awaited()

    async def test_missing_config_discovers_username_once(self):
        job = {'hash': 'b' * 32, 'url': 'https://example.org/job'}
        bot = SimpleNamespace(send_message=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(username='ExampleJobsBot')))
        with patch.object(cb.Config, 'ENABLE_MARKDOWN_V2', True), patch.object(cb.Config, 'BOT_USERNAME', ''):
            await cb._send_job_to_chat(bot, job, '@channel')
            await cb._send_job_to_chat(bot, job, '@channel')
        self.assertEqual(bot.get_me.await_count, 1)
        buttons = [b for row in bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertTrue(any(b.get('url') and '?start=resume_' in b.get('url') for b in buttons))

    async def test_missing_invalid_identity_and_failed_lookup_fail_closed(self):
        for job, username, error in [
            ({'hash': '', 'url': 'https://example.org/job'}, 'ExampleJobsBot', None),
            ({'hash': 'bad/hash', 'url': 'https://example.org/job'}, 'ExampleJobsBot', None),
            ({'hash': 'abc123', 'url': 'https://example.org/job'}, 'bad/name', None),
            ({'hash': 'abc123', 'url': 'https://example.org/job'}, '', TimeoutError()),
        ]:
            for modern in (False, True):
                job = {'title': 'Junior QA', 'company': 'Example', 'source': 'Example', **job}
                bot = SimpleNamespace(send_message=AsyncMock(), get_me=AsyncMock(side_effect=error))
                with self.subTest(job=job, modern=modern, username=username), patch.object(cb.Config, 'ENABLE_MARKDOWN_V2', modern), patch.object(cb.Config, 'BOT_USERNAME', username):
                    self.assertTrue(await cb._send_job_to_chat(bot, job, '@channel'))
                    markup = bot.send_message.await_args.kwargs.get('reply_markup')
                    buttons = [b if isinstance(b, dict) else b.to_dict() for row in markup.inline_keyboard for b in row] if markup else []
                    self.assertFalse(any('?start=resume_' in b.get('url', '') for b in buttons))

    async def test_unsaved_payload_has_no_resume_button(self):
        bot = SimpleNamespace(send_message=AsyncMock(), get_me=AsyncMock())
        with patch.object(cb.Config, 'ENABLE_MARKDOWN_V2', False), patch.object(cb.Config, 'BOT_USERNAME', 'ExampleJobsBot'):
            await cb._send_job_to_chat(bot, {'hash': 'abc123', 'title': 'Junior QA', 'company': 'Example', 'source': 'Example', 'url': 'https://example.org/job'}, '@channel', payload_persisted=False)
        self.assertIsNone(bot.send_message.await_args.kwargs['reply_markup'])
