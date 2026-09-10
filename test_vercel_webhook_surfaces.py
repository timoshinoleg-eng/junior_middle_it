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

    def test_webhook_admin_reconciles_only_after_green_production_smoke(self):
        text = Path(".github/workflows/webhook-admin.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn('workflows: ["Production Smoke"]', text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", text)
        self.assertIn('action="enable"', text)
        self.assertNotIn("schedule:", text)
        self.assertIn("/api/webhook-admin?action=$ACTION", text)
        self.assertIn("secrets.CRON_SECRET", text)

    def test_webhook_runtime_never_starts_background_runtime(self):
        text = Path("interactive_webhook_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("run_polling(", text)
        self.assertNotIn("run_webhook(", text)
        self.assertNotIn("core.main()", text)
        self.assertNotIn("await application.start()", text)
        self.assertIn("await application.process_update(update)", text)

    def test_interactive_edge_validator_catches_cte_update_targets_without_rejecting_upsert(self):
        text = Path("supabase/functions/interactive-growth-proxy/index.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn(r"/\bUPDATE\s+(?!SET\b)", text)
        self.assertNotIn(r"/^UPDATE\s+", text)
        self.assertIn("ON CONFLICT DO UPDATE SET", text)


if __name__ == "__main__":
    unittest.main()
