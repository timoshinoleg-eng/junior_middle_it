import unittest
from pathlib import Path


class VercelWebhookSurfaceTests(unittest.TestCase):
    def test_request_driven_api_files_exist(self):
        for path in (
            "api/webhook.py",
            "api/webhook_admin.py",
            "api/delivery.py",
        ):
            self.assertTrue(Path(path).is_file(), path)

    def test_delivery_workflow_keeps_daily_and_weekly_out_of_jobqueue(self):
        text = Path(".github/workflows/delivery-cron.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0 9 * * *"', text)
        self.assertIn('cron: "15 10 * * 1"', text)
        self.assertIn("/api/delivery?kind=$KIND", text)
        self.assertIn("secrets.CRON_SECRET", text)

    def test_webhook_admin_is_manual_only_and_protected(self):
        text = Path(".github/workflows/webhook-admin.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("/api/webhook-admin?action=$ACTION", text)
        self.assertIn("secrets.CRON_SECRET", text)

    def test_webhook_runtime_never_calls_polling_main_or_application_start(self):
        text = Path("interactive_webhook_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("run_polling(", text)
        self.assertNotIn("run_webhook(", text)
        self.assertNotIn("core.main()", text.replace("``core.main()``", ""))
        self.assertNotIn("await application.start()", text)
        self.assertIn("await application.process_update(update)", text)


if __name__ == "__main__":
    unittest.main()
