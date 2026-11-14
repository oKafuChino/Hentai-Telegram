import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from hentai_telegram.integrations.telegraph import TelegraphClient
from hentai_telegram.storage.s3 import S3StorageClient, S3StorageError


class TestS3Storage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sample_img = Path(self.temp_dir.name) / "test.jpg"
        self.sample_img.write_bytes(b"\xff\xd8\xff\xd9")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_s3_configured_check(self):
        unconfigured = S3StorageClient()
        self.assertFalse(unconfigured.is_configured())

        configured = S3StorageClient(
            endpoint_url="https://acc.r2.cloudflarestorage.com",
            bucket_name="manga-bucket",
            access_key_id="test-key",
            secret_access_key="test-secret",
            public_url="https://img.example.com",
        )
        self.assertTrue(configured.is_configured())

    def test_s3_build_key(self):
        client = S3StorageClient(key_prefix="manga/")
        key = client.build_key(source_id="12345", chapter_ordinal=1, page_number=5, ext=".jpg")
        self.assertEqual(key, "manga/12345/0001/0005.jpg")

    def test_s3_verify_bucket_success(self):
        client = S3StorageClient(
            endpoint_url="https://s3.us-west-004.backblazeb2.com",
            bucket_name="my-bucket",
            access_key_id="key",
            secret_access_key="sec",
            public_url="https://pub.example.com",
        )
        mock_boto = MagicMock()
        with patch.object(client, "_s3_client", mock_boto):
            info = client.verify_bucket()
            mock_boto.head_bucket.assert_called_once_with(Bucket="my-bucket")
            self.assertEqual(info["bucket"], "my-bucket")
            self.assertEqual(info["public_url"], "https://pub.example.com")

    async def test_s3_upload_file(self):
        client = S3StorageClient(
            endpoint_url="https://acc.r2.cloudflarestorage.com",
            bucket_name="r2-bucket",
            access_key_id="key",
            secret_access_key="sec",
            public_url="https://img.r2.com",
            key_prefix="manga/",
        )
        mock_boto = MagicMock()
        with patch.object(client, "_s3_client", mock_boto):
            url = await client.upload_file(self.sample_img, object_key="manga/123/0001/0001.jpg")
            self.assertEqual(url, "https://img.r2.com/manga/123/0001/0001.jpg")
            mock_boto.upload_file.assert_called_once()
            call_kwargs = mock_boto.upload_file.call_args[1]
            self.assertEqual(call_kwargs["Bucket"], "r2-bucket")
            self.assertEqual(call_kwargs["Key"], "manga/123/0001/0001.jpg")
            self.assertEqual(call_kwargs["ExtraArgs"]["ContentType"], "image/jpeg")

    async def test_telegraph_prefers_s3_over_http(self):
        mock_s3 = MagicMock()
        mock_s3.is_configured.return_value = True
        mock_s3.upload_file = AsyncMock(return_value="https://img.r2.com/manga/test.jpg")

        client = TelegraphClient(s3_client=mock_s3)
        session = MagicMock()

        res = await client.upload_image(session, self.sample_img, key="manga/test.jpg")
        self.assertEqual(res, "https://img.r2.com/manga/test.jpg")
        mock_s3.upload_file.assert_awaited_once_with(self.sample_img, object_key="manga/test.jpg")
        # Ensure HTTP session was not called
        session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
