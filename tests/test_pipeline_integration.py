"""Unit tests for storage_r2, telegraph_pub, and tg_bot modules."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings
from src.storage_r2 import R2StorageClient
from src.telegraph_pub import TelegraphPublisher
from src.tg_bot import TelegramNotifier


class TestPipelineModules(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            r2_account_id="test_acc",
            r2_access_key_id="test_key",
            r2_secret_access_key="test_secret",
            r2_bucket_name="test_bucket",
            r2_custom_domain="https://img.example.com",
            telegraph_short_name="TestBot",
            telegraph_author_name="TestAuthor",
            telegraph_access_token="test_token_123",
            telegraph_max_images_per_page=100,
            tg_bot_token="123456:ABC-DEF",
            tg_main_chat_id="-1002222",
        )

    def test_r2_client_keys_and_urls(self):
        with patch("boto3.client"):
            client = R2StorageClient(self.settings)
            self.assertTrue(client.is_configured())

            key = client.build_key(manga_id="1001", chapter_num="1", slice_filename="001.jpg")
            self.assertEqual(key, "manga_1001/ch_1/001.jpg")

            url = client.build_public_url(key)
            self.assertEqual(url, "https://img.example.com/manga_1001/ch_1/001.jpg")

    def test_r2_upload_slices(self):
        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            client = R2StorageClient(self.settings)
            slices = [("001.jpg", b"data1"), ("002.jpg", b"data2")]
            urls = client.upload_chapter_slices(slices, manga_id="1001", chapter_num="1")

            self.assertEqual(len(urls), 2)
            self.assertEqual(urls[0], "https://img.example.com/manga_1001/ch_1/001.jpg")
            self.assertEqual(urls[1], "https://img.example.com/manga_1001/ch_1/002.jpg")
            self.assertEqual(mock_s3.put_object.call_count, 2)

    def test_telegraph_pagination_single_page(self):
        pub = TelegraphPublisher(self.settings)
        with patch.object(pub, "_create_page_raw") as mock_create:
            mock_create.return_value = {"path": "test-path", "url": "https://telegra.ph/test-path"}
            urls = ["https://img.example.com/1.jpg", "https://img.example.com/2.jpg"]
            result = pub.publish_chapter("测试漫画 01", urls)
            self.assertEqual(result, ["https://telegra.ph/test-path"])
            mock_create.assert_called_once()

    def test_telegraph_pagination_multi_pages(self):
        # Override limit to 2 for testing
        self.settings.telegraph_max_images_per_page = 2
        pub = TelegraphPublisher(self.settings)

        with patch.object(pub, "_create_page_raw") as mock_create, patch.object(pub, "_edit_page_raw") as mock_edit:
            mock_create.side_effect = [
                {"path": "part1", "url": "https://telegra.ph/part1"},
                {"path": "part2", "url": "https://telegra.ph/part2"},
            ]
            urls = [f"https://img.example.com/{i}.jpg" for i in range(1, 4)]
            # 3 images with limit 2 -> 2 pages: part1 (2), part2 (1)
            result = pub.publish_chapter("测试漫画 01", urls)
            self.assertEqual(result, ["https://telegra.ph/part1", "https://telegra.ph/part2"])
            self.assertEqual(mock_create.call_count, 2)
            self.assertEqual(mock_edit.call_count, 2)

    def test_telegram_direct_channel_push_with_instant_view(self):
        notifier = TelegramNotifier(self.settings)
        self.assertTrue(notifier.is_configured())

        mock_bot = AsyncMock()
        import asyncio
        asyncio.run(
            notifier.publish_to_main_channel(
                mock_bot,
                title="测试漫画",
                chapter_num="1",
                telegraph_urls=["https://telegra.ph/test-1"],
                tags=["测试"],
            )
        )
        mock_bot.send_message.assert_called_once()
        _args, kwargs = mock_bot.send_message.call_args
        self.assertEqual(kwargs["chat_id"], "-1002222")
        self.assertIn("link_preview_options", kwargs)
        self.assertEqual(kwargs["link_preview_options"].url, "https://telegra.ph/test-1")
        self.assertTrue(kwargs["link_preview_options"].show_above_text)
        self.assertTrue(kwargs["link_preview_options"].prefer_large_media)


if __name__ == "__main__":
    unittest.main()
