"""Bind the production webhook processor to the localized P8 product runtime."""
import interactive_webhook_runtime as base

_original_runtime = base._runtime


def _localized_runtime():
    _runtime, core = _original_runtime()
    import localized_product_runtime as localized
    return localized, core


base._runtime = _localized_runtime

MAX_UPDATE_BYTES = base.MAX_UPDATE_BYTES
process_update_payload = base.process_update_payload
webhook_secret_valid = base.webhook_secret_valid
