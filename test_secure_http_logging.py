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


if __name__ == "__main__":
    unittest.main()
