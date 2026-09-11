"""Bind request-driven product surfaces to the final localized runtime."""
import interactive_webhook_runtime as base

from telegram import CallbackQuery
from telegram.error import TelegramError

_original_runtime = base._runtime
_original_callback_answer = CallbackQuery.answer


async def _resilient_callback_answer(self, *args, **kwargs):
    """Never fail a substantive button action because Telegram rejected its ACK.

    CallbackQuery.answer only clears the client-side spinner / shows a small
    notification. On a serverless cold start Telegram can consider the ACK too
    old even though the requested action is still perfectly valid.
    """
    try:
        return await _original_callback_answer(self, *args, **kwargs)
    except TelegramError:
        return False


if CallbackQuery.answer is not _resilient_callback_answer:
    CallbackQuery.answer = _resilient_callback_answer


def _localized_runtime():
    _runtime, core = _original_runtime()
    import localized_product_runtime_v4 as localized
    return localized, core


base._runtime = _localized_runtime

MAX_UPDATE_BYTES = base.MAX_UPDATE_BYTES
process_update_payload = base.process_update_payload
webhook_secret_valid = base.webhook_secret_valid
build_runtime = base.build_runtime
