import os
import unittest
from unittest.mock import patch

from hentai_telegram.config import Settings


class TestConfig(unittest.TestCase):
    def test_settings_rejects_missing_token(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_ALLOWED_USER_IDS": "123"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_settings_rejects_invalid_user(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "valid_token", "TELEGRAM_ALLOWED_USER_IDS": "0"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_settings_requires_higher_resume_threshold(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "valid_token",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "MIN_FREE_SPACE_GB": "10",
            "RESUME_FREE_SPACE_GB": "10",
        }, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_settings_valid(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "valid_token",
            "TELEGRAM_ALLOWED_USER_IDS": "123,456",
            "TELEGRAM_CHANNEL_ID": "-1001234567890",
            "TELEGRAPH_AUTHOR_NAME": "TestChannel",
        }, clear=True):
            s = Settings.from_env()
            self.assertEqual(s.telegram_bot_token, "valid_token")
            self.assertEqual(s.allowed_user_ids, frozenset({123, 456}))
            self.assertEqual(s.telegram_channel_id, "-1001234567890")
            self.assertEqual(s.telegraph_author_name, "TestChannel")
            self.assertEqual(s.telegraph_upload_url, "https://catbox.moe/user/api.php")

    def test_settings_upload_url_fallback(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "valid_token",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "TELEGRAPH_UPLOAD_URL": "https://telegra.ph/upload",
        }, clear=True):
            s = Settings.from_env()
            self.assertEqual(s.telegraph_upload_url, "https://catbox.moe/user/api.php")

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "valid_token",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "TELEGRAPH_UPLOAD_URL": "https://custom.proxy.dev/upload",
        }, clear=True):
            s = Settings.from_env()
            self.assertEqual(s.telegraph_upload_url, "https://custom.proxy.dev/upload")

    def test_settings_s3_r2_b2_aliases(self):
        # Test R2_* environment variables
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "valid_token",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "R2_ENDPOINT_URL": "https://acc123.r2.cloudflarestorage.com",
            "R2_BUCKET_NAME": "my-r2-bucket",
            "R2_ACCESS_KEY_ID": "r2-key",
            "R2_SECRET_ACCESS_KEY": "r2-secret",
            "R2_PUBLIC_URL": "https://img.r2domain.com",
        }, clear=True):
            s = Settings.from_env()
            self.assertEqual(s.s3_endpoint_url, "https://acc123.r2.cloudflarestorage.com")
            self.assertEqual(s.s3_bucket_name, "my-r2-bucket")
            self.assertEqual(s.s3_access_key_id, "r2-key")
            self.assertEqual(s.s3_secret_access_key, "r2-secret")
            self.assertEqual(s.s3_public_url, "https://img.r2domain.com")

        # Test B2_* environment variables
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "valid_token",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "B2_ENDPOINT_URL": "https://s3.us-west-004.backblazeb2.com",
            "B2_BUCKET_NAME": "my-b2-bucket",
            "B2_KEY_ID": "b2-key",
            "B2_APPLICATION_KEY": "b2-secret",
            "B2_PUBLIC_URL": "https://img.b2domain.com",
        }, clear=True):
            s = Settings.from_env()
            self.assertEqual(s.s3_endpoint_url, "https://s3.us-west-004.backblazeb2.com")
            self.assertEqual(s.s3_bucket_name, "my-b2-bucket")
            self.assertEqual(s.s3_access_key_id, "b2-key")
            self.assertEqual(s.s3_secret_access_key, "b2-secret")
            self.assertEqual(s.s3_public_url, "https://img.b2domain.com")


if __name__ == "__main__":
    unittest.main()
