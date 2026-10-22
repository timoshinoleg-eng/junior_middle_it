import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import copilot_webhook_runtime as runtime


HASH = "a" * 40
JOB = {"hash": HASH, "title": "Developer", "description": "Python"}


class FakeDb:
    def __init__(self):
        self.values = {}

    def get_runtime_state(self, uid, key):
        return self.values.get((uid, key))

    def set_runtime_state(self, uid, key, state, ttl_seconds=3600):
        self.values[(uid, key)] = dict(state)

    def delete_runtime_state(self, uid, key):
        self.values.pop((uid, key), None)

    def get_job_payload(self, key):
        return dict(JOB) if key == HASH else None


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class ResumeWebhookRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_job_is_durable_across_handlers(self):
        db = FakeDb()
        first = SimpleNamespace(db=db)
        runtime._pdf_requests(first)[42] = {"job_hash": HASH}
        self.assertEqual(db.values[(42, "copilot_cv_pending")], {"job_hash": HASH})

        message = FakeMessage()
        message.text = "A valid long resume with enough words to complete the build."
        second = SimpleNamespace(db=db)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
        with patch.object(runtime, "_build_cv_from_message", new_callable=AsyncMock) as build:
            await runtime._copilot_handle_setup_text(second, update, SimpleNamespace())
        build.assert_awaited_once()
        self.assertEqual(build.await_args.args[3]["job_hash"], HASH)
        self.assertEqual(build.await_args.args[3]["job"]["title"], "Developer")

    async def test_image_is_acknowledged_without_consuming_selection(self):
        db = FakeDb()
        runtime._pdf_requests(SimpleNamespace(db=db))[42] = {"job_hash": HASH}
        msg = FakeMessage()
        await runtime._resume_image_guidance(
            SimpleNamespace(db=db), SimpleNamespace(message=msg), SimpleNamespace()
        )
        self.assertIn("PNG/JPG", msg.replies[0])
        self.assertEqual(db.values[(42, "copilot_cv_pending")]["job_hash"], HASH)


if __name__ == "__main__":
    unittest.main()
