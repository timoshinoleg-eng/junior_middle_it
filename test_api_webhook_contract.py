import io
import json
import unittest
from unittest.mock import MagicMock, patch

from api import webhook as api_webhook


class FakeHandler:
    def __init__(self, body=b"{}", headers=None, path="/api/webhook"):
        self.headers = headers or {}
        self.path = path
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None

    def _send_json(self, status, payload):
        self.status = status
        self.payload = payload


class WebhookApiContractTests(unittest.TestCase):
    def test_unauthorized_request_is_rejected_before_body_processing(self):
        body = json.dumps({"update_id": 1}).encode()
        fake = FakeHandler(body=body, headers={"content-length": str(len(body))})
        with patch.object(api_webhook, "webhook_secret_valid", return_value=False), patch.object(
            api_webhook, "process_update_payload"
        ) as process:
            api_webhook.handler.do_POST(fake)
        self.assertEqual(fake.status, 401)
        process.assert_not_called()

    def _run_state(self, state, update_id=2):
        body = json.dumps({"update_id": update_id}).encode()
        fake = FakeHandler(
            body=body,
            headers={
                "content-length": str(len(body)),
                "X-Telegram-Bot-Api-Secret-Token": "secret",
            },
        )
        sentinel = object()
        process = MagicMock(return_value=sentinel)
        with patch.object(api_webhook, "webhook_secret_valid", return_value=True), patch.object(
            api_webhook, "process_update_payload", process
        ), patch.object(api_webhook.asyncio, "run", return_value=state) as run:
            api_webhook.handler.do_POST(fake)
        process.assert_called_once()
        run.assert_called_once_with(sentinel)
        return fake

    def test_busy_update_returns_503_for_telegram_retry(self):
        fake = self._run_state("busy", update_id=2)
        self.assertEqual(fake.status, 503)
        self.assertEqual(fake.payload["error"], "update_busy")

    def test_processed_and_duplicate_updates_acknowledge_with_200(self):
        for state in ("processed", "duplicate"):
            fake = self._run_state(state, update_id=3)
            self.assertEqual(fake.status, 200)
            self.assertEqual(fake.payload["status"], state)

    def test_handler_failure_returns_retryable_503(self):
        body = json.dumps({"update_id": 4}).encode()
        fake = FakeHandler(
            body=body,
            headers={
                "content-length": str(len(body)),
                "X-Telegram-Bot-Api-Secret-Token": "secret",
            },
        )
        sentinel = object()
        process = MagicMock(return_value=sentinel)
        with patch.object(api_webhook, "webhook_secret_valid", return_value=True), patch.object(
            api_webhook, "process_update_payload", process
        ), patch.object(api_webhook.asyncio, "run", side_effect=RuntimeError("boom")):
            api_webhook.handler.do_POST(fake)
        process.assert_called_once()
        self.assertEqual(fake.status, 503)
        self.assertEqual(fake.payload["error"], "temporarily_unavailable")

    def test_oversized_update_is_rejected(self):
        fake = FakeHandler(
            body=b"{}",
            headers={
                "content-length": str(api_webhook.MAX_UPDATE_BYTES + 1),
                "X-Telegram-Bot-Api-Secret-Token": "secret",
            },
        )
        with patch.object(api_webhook, "webhook_secret_valid", return_value=True):
            api_webhook.handler.do_POST(fake)
        self.assertEqual(fake.status, 413)


if __name__ == "__main__":
    unittest.main()
