import importlib.util
import asyncio
import io
from pathlib import Path
import tempfile
import json
import sys
import unittest
from unittest.mock import patch

from hentai_telegram.providers.jm_adapter import handle, classify_error, initialize_client, StageError
from hentai_telegram.domain.errors import JobError


class Photo:
    id, name = "52", "第一章"
    def __len__(self):
        return 2
    def __getitem__(self, index):
        return ("image0", "image1")[index]


class SDK:
    def get_album_detail(self, album_id):
        class Album(list):
            name, authors = "漫画", ["作者"]
        return Album([Photo()])
    def get_photo_detail(self, photo_id):
        return Photo()
    def download_by_image_detail(self, image, path):
        self.image = image
        Path(path).write_bytes(b"decoded")


class AdapterTests(unittest.TestCase):
    def test_pixel_limit_rejects_header_before_sdk_decodes(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (40, 40)).save(stream, 'PNG')
        content = stream.getvalue()
        class DecodingSDK(SDK):
            loaded = False
            def download_by_image_detail(self, image, path):
                with Image.open(io.BytesIO(content)) as decoded:
                    self.loaded = True
                    decoded.load()
                    decoded.save(path, 'JPEG')
        client = DecodingSDK()
        previous = Image.MAX_IMAGE_PIXELS
        with tempfile.TemporaryDirectory() as directory:
            request = {'operation': 'download_batch', 'pages': [{'number': 1, 'url': 'jm:52:0'}],
                       'directory': directory, 'max_bytes': 10000, 'max_pixels': 1000, 'budget': 10000}
            with self.assertRaises(StageError) as caught:
                handle(request, client)
            self.assertFalse(client.loaded)
            self.assertEqual(classify_error(caught.exception, 'download')['error']['code'], 'IMAGE_LIMIT')
            self.assertEqual(Image.MAX_IMAGE_PIXELS, previous)

    def test_actors_take_precedence_over_tags_in_metadata(self):
        class Album(list):
            name, authors = 'Book', []
            actors = [' 优香 ', '优香', None]
            works = ['蔚蓝档案']
            tags = [f'tag-{i}' for i in range(100)]
            @property
            def characters(self):
                raise AttributeError('not supported')
        with patch.object(SDK, 'get_album_detail', return_value=Album([Photo()])):
            tags = handle({'operation': 'metadata', 'id': '42'}, SDK())['tags']
        self.assertEqual(tags[:2], ['优香', '蔚蓝档案'])
        self.assertEqual(len(tags), 100)

    def test_missing_actors_still_returns_other_tags(self):
        class Album(list):
            name, authors = 'Book', []
            works, tags = ['原作'], ['剧情']
            @property
            def actors(self):
                raise ValueError('unavailable')
        with patch.object(SDK, 'get_album_detail', return_value=Album([Photo()])):
            self.assertEqual(handle({'operation': 'metadata', 'id': '42'}, SDK())['tags'], ['原作', '剧情'])

    def test_disk_full_sdk_error_is_not_retried_as_network(self):
        import errno
        result = classify_error(OSError(errno.ENOSPC, "full"), "image_download_decode")
        self.assertEqual(result["error"]["code"], "DISK_FULL")

    def test_batch_reuses_chapter_and_preserves_pages_on_failure(self):
        from PIL import Image
        class BatchSDK(SDK):
            refreshes = 0
            downloads = 0
            fail = True
            def get_photo_detail(self, photo_id):
                self.refreshes += 1
                return Photo()
            def download_by_image_detail(self, image, path):
                self.downloads += 1
                if image == "image1" and self.fail:
                    raise ConnectionError("offline")
                Image.new("RGB", (8, 8)).save(path, "JPEG")
        client = BatchSDK()
        with tempfile.TemporaryDirectory() as root:
            request = {"operation": "download_batch", "pages": [
                {"url": "jm:52:0", "number": 1}, {"url": "jm:52:1", "number": 2}],
                "directory": root, "max_bytes": 10000, "max_pixels": 1000, "budget": 20000}
            with self.assertRaises(StageError):
                handle(request, client)
            self.assertEqual(client.refreshes, 1)
            first = Path(root) / "000001.jpg"
            before = first.read_bytes()
            client.fail = False
            request["pages"] = request["pages"][1:]
            self.assertEqual(handle(request, client), {})
            self.assertEqual(client.refreshes, 2)
            self.assertEqual(client.downloads, 3)
            self.assertEqual(first.read_bytes(), before)
            self.assertTrue((Path(root) / "000002.jpg").exists())

    def test_download_error_identifies_refresh_or_image_stage(self):
        class ResponseUnexpectedException(Exception):
            pass
        request = {"operation": "download", "url": "jm:52:1", "max_bytes": 30}
        with tempfile.TemporaryDirectory() as directory:
            request["destination"] = str(Path(directory) / "image.part")
            for method, phase in (("get_photo_detail", "download_photo_refresh"),
                                  ("download_by_image_detail", "image_download_decode")):
                client = SDK()
                with patch.object(client, method, side_effect=ResponseUnexpectedException("secret body")):
                    with self.assertRaises(StageError) as caught:
                        handle(request, client)
                result = classify_error(caught.exception, "download")
                self.assertEqual(result["error"]["diagnostic"]["phase"], phase)
                self.assertEqual(result["error"]["code"], "JM_RESPONSE_UNEXPECTED")
                self.assertNotIn("secret body", json.dumps(result))

    def test_initialization_unexpected_response_retries_and_recovers(self):
        class ResponseUnexpectedException(Exception):
            pass
        error = ResponseUnexpectedException("secret response")
        error.__context__ = AssertionError("secret")
        self.assertEqual(classify_error(error, "client_init")["error"]["code"], "JM_RESPONSE_UNEXPECTED")
        client = object()
        with patch("hentai_telegram.providers.jm_adapter.make_client", side_effect=[error, client]) as factory, patch("hentai_telegram.providers.jm_adapter.time.sleep"):
            self.assertIs(initialize_client(), client)
            self.assertEqual(factory.call_count, 2)

    def test_initialization_retry_is_bounded_and_config_errors_not_retried(self):
        for error, attempts in ((ConnectionError("offline"), 3), (ValueError("config"), 1)):
            with patch("hentai_telegram.providers.jm_adapter.make_client", side_effect=error) as factory, patch("hentai_telegram.providers.jm_adapter.time.sleep"):
                with self.assertRaises(type(error)):
                    initialize_client()
                self.assertEqual(factory.call_count, attempts)

    def test_source_error_classification_and_redaction(self):
        for error, expected in ((TimeoutError("secret-url"), "TIMEOUT"),
                                (AttributeError("secret-cookie"), "JM_SDK_INCOMPATIBLE"),
                                (ConnectionError("secret-password"), "NETWORK")):
            result = classify_error(error, "metadata")
            self.assertEqual(result["error"]["code"], expected)
            self.assertNotIn("secret", json.dumps(result))
        wrapper = RuntimeError("sensitive SDK message")
        wrapper.__cause__ = TimeoutError("sensitive proxy URL")
        self.assertEqual(classify_error(wrapper, "metadata")["error"]["code"], "TIMEOUT")

    def test_sdk_metadata_and_stable_pages(self):
        client = SDK()
        self.assertEqual(handle({"operation": "metadata", "id": "42"}, client)["chapters"], [{"id": "52", "title": "第一章"}])
        self.assertEqual(handle({"operation": "pages", "chapter_id": "52"}, client)["pages"], ["jm:52:0", "jm:52:1"])

    def test_sdk_decoder_and_size_limit(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "image.part"
            client = SDK()
            request = {"operation": "download", "url": "jm:52:1", "destination": str(destination), "max_bytes": 30}
            self.assertEqual(handle(request, client), {})
            self.assertEqual(client.image, "image1")
            self.assertEqual(destination.read_bytes(), b"decoded")
            destination.unlink()
            request["max_bytes"] = 1
            self.assertEqual(handle(request, client)["error"]["code"], "SIZE_LIMIT")
            self.assertFalse(destination.exists())
            self.assertFalse(list(Path(root).glob("*.jpg")))


if __name__ == "__main__":
    unittest.main()
