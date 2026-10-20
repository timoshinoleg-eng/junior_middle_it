import re
import unittest
from pathlib import Path

EDGE_VALIDATORS = (
    "supabase/functions/growth-proxy/index.ts",
    "supabase/functions/interactive-growth-proxy/index.ts",
)

# Mirrors of the relation scan of the Edge validators. The list must stay in
# sync with the TypeScript sources, otherwise the test would check nothing.
RELATION_PATTERNS = (
    r"\bFROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
    r"\bJOIN\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
    r"\bINTO\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
    r"\bDELETE\s+FROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
    r"\bUPDATE\s+(?!SET\b)((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
)
DISTINCT_FROM_PATTERN = re.compile(r"\bIS\s+(?:NOT\s+)?DISTINCT\s+FROM\b", re.IGNORECASE)


def _relations(sql: str, *, normalized: bool) -> list:
    """Relations the Edge validator would read out of one statement."""
    scan = re.sub(r"\s+", " ", sql.strip())
    if normalized:
        scan = DISTINCT_FROM_PATTERN.sub("IS DISTINCT_FROM", scan)
    found = []
    for pattern in RELATION_PATTERNS:
        found.extend(match.group(1).lower() for match in re.finditer(pattern, scan, re.IGNORECASE))
    return found


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

    def test_webhook_admin_tolerates_only_transient_deployment_race(self):
        text = Path(".github/workflows/webhook-admin.yml").read_text(encoding="utf-8")
        self.assertIn("for attempt in {1..18}", text)
        self.assertIn("404|502|503|504|000", text)
        self.assertIn("sleep 10", text)
        self.assertIn("non-transient HTTP", text)
        self.assertIn("did not become ready after deployment grace period", text)

    def test_branding_workflow_is_manual_and_never_posts_intro(self):
        branding = Path(".github/workflows/telegram-branding-once.yml").read_text(encoding="utf-8")
        endpoint = Path("api/telegram_branding_admin.py").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", branding)
        self.assertNotIn("workflow_run:", branding)
        self.assertNotIn("sendMessage", endpoint)
        self.assertNotIn("pinChatMessage", endpoint)

    def test_webhook_runtime_never_starts_background_runtime(self):
        text = Path("interactive_webhook_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("run_polling(", text)
        self.assertNotIn("run_webhook(", text)
        self.assertNotIn("core.main()", text)
        self.assertNotIn("await application.start()", text)
        self.assertIn("await application.process_update(update)", text)

    def test_interactive_edge_validator_catches_cte_update_targets(self):
        text = Path("supabase/functions/interactive-growth-proxy/index.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn(r"/\bUPDATE\s+(?!SET\b)", text)
        self.assertNotIn(r"/^UPDATE\s+", text)

    def test_edge_validators_ignore_is_distinct_from_as_a_relation(self):
        for path in EDGE_VALIDATORS:
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn(r"/\bIS\s+(?:NOT\s+)?DISTINCT\s+FROM\b/gi", text, path)
            self.assertIn("relationScan", text, path)

    def test_edge_functions_report_the_deployed_validator_version(self):
        for path in EDGE_VALIDATORS:
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("const VALIDATOR_VERSION = 2;", text, path)
            self.assertIn("validator: VALIDATOR_VERSION", text, path)

    def test_both_edge_functions_expose_a_public_health_route(self):
        for path in EDGE_VALIDATORS:
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn('url.pathname.endsWith("/health")', text, path)
            self.assertIn("ok: Boolean(DB_URL)", text, path)

    def test_daily_free_credit_statement_survives_the_relation_scan(self):
        source = Path("copilot/store.py").read_text(encoding="utf-8")
        match = re.search(
            r"(UPDATE\s+growth_copilot_credits.*?RETURNING\s+balance)", source, re.S
        )
        self.assertIsNotNone(match, "daily free credit statement not found")
        statement = match.group(1)

        without_fix = _relations(statement, normalized=False)
        self.assertIn("current_date", without_fix)
        with_fix = _relations(statement, normalized=True)
        self.assertNotIn("current_date", with_fix)
        for relation in with_fix:
            self.assertTrue(
                relation.startswith("growth_"),
                "relation outside the growth allowlist: %s" % relation,
            )


if __name__ == "__main__":
    unittest.main()
