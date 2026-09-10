import logging
import unittest

import secure_http_logging as secure


class SecureHttpLoggingTests(unittest.TestCase):
    def test_sensitive_http_loggers_are_warning_or_stricter(self):
        previous = {name: logging.getLogger(name).level for name in secure.SENSITIVE_HTTP_LOGGERS}
        try:
            for name in secure.SENSITIVE_HTTP_LOGGERS:
                logging.getLogger(name).setLevel(logging.INFO)
            secure.configure_sensitive_http_logging()
            for name in secure.SENSITIVE_HTTP_LOGGERS:
                self.assertGreaterEqual(logging.getLogger(name).level, logging.WARNING)
        finally:
            for name, level in previous.items():
                logging.getLogger(name).setLevel(level)

    def test_redacts_query_tokens_and_telegram_bot_paths(self):
        message = (
            "400 for https://api.example.test/run?token=super-secret-value&clean=true "
            "and https://api.telegram.org/bot123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF/getMe"
        )
        redacted = secure.redact_credentials(message)
        self.assertNotIn("super-secret-value", redacted)
        self.assertNotIn("123456789:abcdefghijklmnopqrstuvwxyz_ABCDEF", redacted)
        self.assertIn("token=<redacted>", redacted)
        self.assertIn("/bot<redacted>/getMe", redacted)

    def test_filter_sanitizes_formatted_exception_message(self):
        record = logging.LogRecord(
            "channel_bot",
            logging.ERROR,
            __file__,
            1,
            "request failed: %s",
            ("https://x.test?q=1&api_key=secret123",),
            None,
        )
        filt = secure.CredentialRedactionFilter()
        self.assertTrue(filt.filter(record))
        rendered = record.getMessage()
        self.assertNotIn("secret123", rendered)
        self.assertIn("api_key=<redacted>", rendered)

    def test_expected_ats_404_is_demoted_to_info(self):
        record = logging.LogRecord(
            "channel_bot",
            logging.ERROR,
            __file__,
            1,
            "❌ Greenhouse hashicorp error: 404 Client Error: Not Found",
            (),
            None,
        )
        filt = secure.CredentialRedactionFilter()
        self.assertTrue(filt.filter(record))
        self.assertEqual(record.levelno, logging.INFO)
        self.assertEqual(record.levelname, "INFO")

    def test_optional_devitjobs_403_is_demoted_to_warning(self):
        record = logging.LogRecord(
            "channel_bot",
            logging.ERROR,
            __file__,
            1,
            "❌ DevITJobs error: 403 Client Error: Forbidden",
            (),
            None,
        )
        filt = secure.CredentialRedactionFilter()
        self.assertTrue(filt.filter(record))
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(record.levelname, "WARNING")

    def test_actionable_source_error_stays_error(self):
        record = logging.LogRecord(
            "channel_bot",
            logging.ERROR,
            __file__,
            1,
            "❌ Apify USAJobs error: 500 Server Error",
            (),
            None,
        )
        filt = secure.CredentialRedactionFilter()
        self.assertTrue(filt.filter(record))
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertEqual(record.levelname, "ERROR")


if __name__ == "__main__":
    unittest.main()
