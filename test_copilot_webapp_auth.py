import json
import time
import unittest

from copilot import webapp_auth


BOT_TOKEN = "123456:TEST-TOKEN"
AUTH_DATE = 1_700_000_000


def build_init_data(params, *, token=BOT_TOKEN, with_hash=True):
    """Build a signed initData string, like Telegram does."""
    payload = dict(params)
    payload.setdefault("auth_date", str(AUTH_DATE))
    payload.setdefault("query_id", "AAH_test")
    payload.setdefault("user", json.dumps({"id": 42, "first_name": "Иван"}))
    check_string = webapp_auth.data_check_string(payload)
    secret = webapp_auth.compute_secret_key(token)
    import hmac
    import hashlib

    signature = hmac.new(
        secret, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if with_hash:
        payload["hash"] = signature
    from urllib.parse import urlencode

    return urlencode(payload)


class InitDataTests(unittest.TestCase):
    def test_valid_signature_is_accepted(self):
        raw = build_init_data({})
        params = webapp_auth.verify_init_data(raw, BOT_TOKEN, now=AUTH_DATE + 10)
        self.assertEqual(params["query_id"], "AAH_test")

    def test_forged_signature_is_rejected(self):
        raw = build_init_data({}, token="other-token")
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(raw, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_tampered_payload_is_rejected(self):
        raw = build_init_data({})
        tampered = raw.replace("query_id=AAH_test", "query_id=AAH_tampered")
        self.assertNotEqual(raw, tampered)
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(tampered, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_missing_hash_is_rejected(self):
        raw = build_init_data({}, with_hash=False)
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(raw, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_empty_init_data_is_rejected(self):
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data("", BOT_TOKEN)

    def test_old_signature_is_rejected(self):
        raw = build_init_data({})
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(raw, BOT_TOKEN, now=AUTH_DATE + 10 * 24 * 60 * 60)

    def test_fresh_signature_survives_a_day(self):
        raw = build_init_data({})
        params = webapp_auth.verify_init_data(
            raw, BOT_TOKEN, now=AUTH_DATE + 23 * 60 * 60
        )
        self.assertTrue(params)

    def test_signature_from_the_future_is_rejected(self):
        raw = build_init_data({})
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(raw, BOT_TOKEN, now=AUTH_DATE - 3600)

    def test_missing_auth_date_is_rejected(self):
        raw = build_init_data({})
        without = raw.replace("auth_date=%d&" % AUTH_DATE, "")
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(without, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_missing_bot_token_is_rejected(self):
        raw = build_init_data({})
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_init_data(raw, "", now=AUTH_DATE + 10)

    def test_check_string_excludes_the_hash(self):
        params = {"a": "1", "b": "2", "hash": "x"}
        self.assertEqual(webapp_auth.data_check_string(params), "a=1\nb=2")


class UserTests(unittest.TestCase):
    def test_user_is_read_from_the_signed_payload(self):
        raw = build_init_data({})
        user = webapp_auth.verify_user(raw, BOT_TOKEN, now=AUTH_DATE + 10)
        self.assertEqual(user["id"], 42)
        self.assertEqual(user["first_name"], "Иван")

    def test_missing_user_is_rejected(self):
        raw = build_init_data({"user": ""})
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_user(raw, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_broken_user_json_is_rejected(self):
        raw = build_init_data({"user": "notjson"})
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_user(raw, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_user_without_an_id_is_rejected(self):
        raw = build_init_data({"user": json.dumps({"first_name": "Иван"})})
        with self.assertRaises(webapp_auth.InitDataError):
            webapp_auth.verify_user(raw, BOT_TOKEN, now=AUTH_DATE + 10)

    def test_default_max_age_is_one_day(self):
        self.assertEqual(webapp_auth.MAX_AGE_SECONDS, 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
