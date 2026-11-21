import unittest
from unittest.mock import patch
import resume_profile_flow as flow
from test_resume_profile_flow import FakeStore, FakeJobBot, FakeMessage, make_update, run, RESUME_TEXT

class PrivacyConsentTests(unittest.TestCase):
    def test_unrequested_text_never_persists_full_resume(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage()
        with patch.object(flow.CopilotStore, 'from_database', return_value=store):
            run(flow.save_and_report(bot, make_update(message), RESUME_TEXT))
        self.assertEqual(store.resumes, {})
        self.assertEqual(store.profiles, [])
        self.assertEqual(bot.db.settings, {})
