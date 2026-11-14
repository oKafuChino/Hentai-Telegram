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


if __name__ == "__main__":
    unittest.main()
