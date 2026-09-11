"""Bind request-driven product surfaces to the final localized runtime."""
import interactive_webhook_runtime as base

from telegram import CallbackQuery
from telegram.error import TelegramError
from telegram.ext import CommandHandler

_original_runtime = base._runtime
_original_build_runtime = base.build_runtime
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


def _optimized_ledger_claim(self, update_id: int) -> str:
    """Claim a new update with cleanup folded into the same SQL round-trip."""
    uid = int(update_id)
    with self.store._ensure_conn().cursor() as cur:
        cur.execute(
            """
            WITH cleanup AS (
                DELETE FROM growth_telegram_updates
                WHERE state='processed'
                  AND processed_at < NOW() - INTERVAL '30 days'
                RETURNING update_id
            )
            INSERT INTO growth_telegram_updates
                (update_id, state, lease_expires_at, first_seen_at, processed_at)
            VALUES (%s, 'processing', NOW() + INTERVAL '10 minutes', NOW(), NULL)
            ON CONFLICT (update_id) DO UPDATE SET
                state='processing',
                lease_expires_at=NOW() + INTERVAL '10 minutes',
                processed_at=NULL
            WHERE growth_telegram_updates.state='processing'
              AND growth_telegram_updates.lease_expires_at IS NOT NULL
              AND growth_telegram_updates.lease_expires_at <= NOW()
            RETURNING update_id
            """,
            (uid,),
        )
        if cur.fetchone():
            return "claimed"
        cur.execute(
            "SELECT state FROM growth_telegram_updates WHERE update_id=%s LIMIT 1",
            (uid,),
        )
        row = cur.fetchone()
    if row and str(row[0]) == "processed":
        return "processed"
    return "busy"


_optimized_ledger_claim._latency_hardened = True
if not getattr(base.TelegramUpdateLedger.claim, "_latency_hardened", False):
    base.TelegramUpdateLedger.claim = _optimized_ledger_claim


def _localized_runtime():
    _runtime, core = _original_runtime()
    import localized_product_runtime_v8 as localized
    return localized, core


base._runtime = _localized_runtime


def _localized_build_runtime():
    application, db, job_bot = _original_build_runtime()
    application.add_handler(CommandHandler("language", job_bot.cmd_language))
    application.add_handler(CommandHandler("channel", job_bot.cmd_channel))
    return application, db, job_bot


base.build_runtime = _localized_build_runtime

MAX_UPDATE_BYTES = base.MAX_UPDATE_BYTES
process_update_payload = base.process_update_payload
webhook_secret_valid = base.webhook_secret_valid
build_runtime = base.build_runtime
