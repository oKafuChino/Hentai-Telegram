import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from hentai_telegram.bot.presentation import describe_task
from hentai_telegram.config import Settings
from hentai_telegram.db.store import Store
from hentai_telegram.domain.models import Chapter, Manga, Page
from hentai_telegram.integrations.telegraph import TelegraphClient
from hentai_telegram.runtime import instance_lock
from hentai_telegram.services.channel_push import ChannelPublisher, channel_post_link
from hentai_telegram.services.tasks import TaskService
from hentai_telegram.storage.cbz import staged_pages
from hentai_telegram.storage.s3 import S3StorageClient
from hentai_telegram.workers.queue import Worker


def sample_png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(stream, "PNG")
    return stream.getvalue()


class FakeProvider:
    def __init__(self):
        self.source = "jmcomic"

    async def get_manga(self, source_id):
        return Manga(
            source="jmcomic",
            source_id=source_id,
            title="测试漫画标题",
            authors=("作者A", "作者B"),
            chapters=(
                Chapter("c1", 1, "第一话", ()),
                Chapter("c2", 2, "第二话", ()),
            ),
            tags=("标签1", "标签2"),
        )

    async def get_pages(self, chapter_id):
        return (Page(1, f"jm:{chapter_id}:1"), Page(2, f"jm:{chapter_id}:2"))

    async def download(self, page, destination, max_bytes):
        destination.write_bytes(sample_png())


class TestHentaiTelegram(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "test.db"
        self.store = Store(self.db_path)
        self.provider = FakeProvider()

        self.settings = Settings(
            telegram_bot_token="fake_bot_token",
            allowed_user_ids=frozenset({1001, 1002}),
            telegram_channel_id="-1009999999",
            database_url=f"sqlite+aiosqlite:///{self.db_path}",
            storage_root=str(self.root / "storage"),
        )

        self.telegraph = TelegraphClient(
            api_url="https://api.telegra.ph",
            upload_url="https://telegra.ph/upload",
            access_token="fake_token",
            token_file=self.root / "telegraph_token.txt",
            settle_delay=0,
        )

        self.bot = MagicMock()
        self.bot.session = AsyncMock()
        self.publisher = ChannelPublisher(self.bot, self.settings.telegram_channel_id)

    async def asyncTearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_channel_post_link(self):
        self.assertEqual(channel_post_link("-1001234567890", 42), "https://t.me/c/1234567890/42")
        self.assertEqual(channel_post_link("@my_channel", 99), "https://t.me/my_channel/99")

    def test_task_service_authorization(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        with self.assertRaises(ValueError):
            svc.authorize(9999)
        # Should not raise for authorized admin
        svc.authorize(1001)

    async def test_task_service_submission_and_deduplication(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        task = await svc.download("12345", 1001, 1001, message_id=10)
        self.assertEqual(task["status"], "QUEUED")
        self.assertEqual(task["user_id"], 1001)

        # Duplicate active submission should be rejected
        with self.assertRaises(ValueError):
            await svc.download("12345", 1001, 1001)

    async def test_worker_full_pipeline(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        task = await svc.download("55555", 1001, 1001, message_id=20)

        # Mock Telegraph methods
        self.telegraph.upload_image = AsyncMock(return_value="https://telegra.ph/file/mock_img.jpg")
        self.telegraph.create_chapter_page = AsyncMock(side_effect=lambda s, manga_title, chapter_title, ordinal, image_urls, channel_url: f"https://telegra.ph/chapter-{ordinal}")
        self.telegraph.create_index_page = AsyncMock(return_value="https://telegra.ph/index-page")

        # Mock ChannelPublisher publish
        self.publisher.publish = AsyncMock(return_value=777)

        worker = Worker(self.store, self.provider, self.telegraph, self.publisher, self.settings)

        # Run worker once to process the queued task
        processed = await worker.run_once()
        self.assertTrue(processed)

        # Verify task is completed
        finished_task = self.store.get(task["id"])
        self.assertEqual(finished_task["status"], "COMPLETED")
        self.assertEqual(finished_task["channel_message_id"], 777)

        # Verify chapters have telegraph urls
        chapters = self.store.chapters(finished_task["manga_id"])
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["telegraph_url"], "https://telegra.ph/chapter-1")
        self.assertEqual(chapters[1]["telegraph_url"], "https://telegra.ph/chapter-2")

        # Verify publisher was called
        self.publisher.publish.assert_called_once()

    async def test_worker_cancellation(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        task = await svc.download("66666", 1001, 1001)

        # Request cancellation
        svc.cancel(task["id"], 1001)

        worker = Worker(self.store, self.provider, self.telegraph, self.publisher, self.settings)
        await worker.run_once()

        cancelled_task = self.store.get(task["id"])
        self.assertEqual(cancelled_task["status"], "CANCELLED")

    def test_instance_lock_success(self):
        lock_path = Path(self.temp_dir.name) / "test.lock"
        with instance_lock(lock_path):
            self.assertTrue(lock_path.exists())

    def test_instance_lock_permission_error(self):
        lock_path = Path(self.temp_dir.name) / "perm.lock"
        with (
            patch.object(Path, "open", side_effect=PermissionError("Mock denied")),
            self.assertRaises(PermissionError) as ctx,
            instance_lock(lock_path),
        ):
            self.assertIn("权限不足", str(ctx.exception))

    async def test_telegraph_upload_catbox(self):
        img = Path(self.temp_dir.name) / "sample.png"
        img.write_bytes(sample_png())

        client = TelegraphClient(upload_url="https://catbox.moe/user/api.php")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = "https://files.catbox.moe/dummy.png\n"

        session = MagicMock()
        session.post.return_value.__aenter__.return_value = mock_resp

        result = await client.upload_image(session, img)
        self.assertEqual(result, "https://files.catbox.moe/dummy.png")

    async def test_telegraph_upload_proxy_format(self):
        img = Path(self.temp_dir.name) / "sample.png"
        img.write_bytes(sample_png())

        client = TelegraphClient(upload_url="https://custom.proxy.dev/upload")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = [{"src": "/file/test.png"}]

        session = MagicMock()
        session.post.return_value.__aenter__.return_value = mock_resp

        result = await client.upload_image(session, img)
        self.assertEqual(result, "https://custom.proxy.dev/file/test.png")

    async def test_telegraph_auto_redirect_dead_url(self):
        img = Path(self.temp_dir.name) / "sample.png"
        img.write_bytes(sample_png())

        # Old dead upload_url: https://telegra.ph/upload should route to catbox
        client = TelegraphClient(upload_url="https://telegra.ph/upload")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = "https://files.catbox.moe/from_redirect.png\n"

        session = MagicMock()
        session.post.return_value.__aenter__.return_value = mock_resp

        result = await client.upload_image(session, img)
        self.assertEqual(result, "https://files.catbox.moe/from_redirect.png")
        # Ensure post called the catbox endpoint
        call_args = session.post.call_args
        self.assertEqual(call_args[0][0], "https://catbox.moe/user/api.php")

    def test_describe_task_escapes_html(self):
        task = {
            "id": "abc12345",
            "snapshot": json.dumps({"title": "<Script>Alert('XSS')</Script> & Test", "source_id": "123"}),
            "status": "DOWNLOADING",
            "progress": "Downloading <page 1>",
            "error": "Error: <forbidden>",
            "channel_message_id": None,
        }
        rendered = describe_task(task)
        self.assertNotIn("<Script>", rendered)
        self.assertIn("&lt;Script&gt;", rendered)
        self.assertIn("&amp; Test", rendered)
        self.assertIn("&lt;page 1&gt;", rendered)
        self.assertIn("&lt;forbidden&gt;", rendered)

    async def test_resubmit_force_flag(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        task1 = await svc.download("12345", 1001, 1001)
        self.store.finish(task1["id"], "COMPLETED")

        # Without force, should raise ValueError suggesting force
        with self.assertRaises(ValueError) as ctx:
            await svc.download("12345", 1001, 1001, force=False)
        self.assertIn("force", str(ctx.exception))

        # With force=True, should succeed
        task2 = await svc.download("12345", 1001, 1001, force=True)
        self.assertNotEqual(task1["id"], task2["id"])
        self.assertEqual(task2["status"], "QUEUED")

    async def test_channel_publisher_instant_view(self):
        bot = AsyncMock()
        mock_msg = MagicMock()
        mock_msg.message_id = 888
        bot.send_message.return_value = mock_msg

        publisher = ChannelPublisher(bot, "-1001234567890")
        manga = Manga("jmcomic", "12345", "测试漫画")
        chapters = [{"ordinal": 1, "title": "第1话", "telegraph_url": "https://telegra.ph/manga-1"}]

        msg_id = await publisher.publish(manga, chapters)
        self.assertEqual(msg_id, 888)
        bot.send_message.assert_called_once()
        _, kwargs = bot.send_message.call_args
        self.assertEqual(kwargs["chat_id"], -1001234567890)
        preview_opt = kwargs["link_preview_options"]
        self.assertEqual(preview_opt.url, "https://telegra.ph/manga-1")
        self.assertTrue(preview_opt.prefer_large_media)
        self.assertTrue(preview_opt.show_above_text)
        caption_text = kwargs["text"]
        self.assertIn("📖 来源:JMComic", caption_text)
        self.assertIn("📝 作者：", caption_text)
        self.assertIn("🆔 漫画 ID：<code>JM12345</code>", caption_text)
        self.assertNotIn("在线阅读（Telegraph）", caption_text)
        self.assertNotIn("点击在线阅读全文", caption_text)
        self.assertIsNone(kwargs.get("reply_markup"))
        self.assertTrue(caption_text.index("🆔 漫画 ID：") < caption_text.index("📖 来源:JMComic"))

    def test_task_service_authorize_fails_closed(self):
        # Empty whitelist fails closed
        svc_empty = TaskService(self.store, self.provider, allowed_user_ids=frozenset())
        with self.assertRaises(ValueError) as ctx:
            svc_empty.authorize(1001)
        self.assertIn("仅管理员可用", str(ctx.exception))

        # None whitelist fails closed
        svc_none = TaskService(self.store, self.provider, allowed_user_ids=None)
        with self.assertRaises(ValueError) as ctx:
            svc_none.authorize(1001)
        self.assertIn("仅管理员可用", str(ctx.exception))

    async def test_task_service_delete_unauthorized(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        with self.assertRaises(ValueError) as ctx:
            await svc.delete("12345", 9999)
        self.assertIn("仅管理员可用", str(ctx.exception))

    async def test_task_service_delete_invalid_reference(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        with self.assertRaises(ValueError) as ctx:
            await svc.delete("invalid_ref_without_id", 1001)
        self.assertIn("只支持数字漫画 ID", str(ctx.exception))

    async def test_task_service_delete_full_flow(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        task = await svc.download("12345", 1001, 1001)

        # Set task channel_message_id in db
        self.store.update(task["id"], channel_message_id=666)

        # Update chapter telegraph_url and manga index_url
        manga_row = self.store.get_manga("jmcomic", "12345")
        self.store.update_manga(manga_row["id"], index_url="https://telegra.ph/index-12345")
        chapters = self.store.chapters(manga_row["id"])
        self.store.update_chapter(chapters[0]["id"], telegraph_url="https://telegra.ph/ch-1")
        self.store.update_chapter(chapters[1]["id"], telegraph_url="https://telegra.ph/ch-2")

        # Create dummy staging folder
        storage_root = self.root / "storage"
        task_staging = storage_root / "staging" / f"task_{task['id']}"
        task_staging.mkdir(parents=True, exist_ok=True)
        (task_staging / "dummy.png").write_bytes(b"data")

        # Mock bot, s3_client, telegraph
        mock_bot = AsyncMock()
        mock_s3 = MagicMock()
        mock_s3.is_configured.return_value = True
        mock_s3.delete_manga_objects = AsyncMock(return_value=12)
        mock_telegraph = AsyncMock()
        mock_telegraph.delete_pages = AsyncMock(return_value=3)

        res = await svc.delete(
            reference="12345",
            user_id=1001,
            bot=mock_bot,
            channel_id="-1009999999",
            s3_client=mock_s3,
            storage_root=storage_root,
            telegraph_client=mock_telegraph,
        )

        self.assertEqual(res["source_id"], "12345")
        self.assertEqual(res["title"], "测试漫画标题")
        self.assertTrue(res["found_in_db"])
        self.assertEqual(res["deleted_channel_msgs"], 1)
        self.assertEqual(res["deleted_telegraph_count"], 3)
        self.assertEqual(res["deleted_r2_count"], 12)
        self.assertEqual(res["deleted_tasks"], 1)
        self.assertEqual(res["deleted_chapters"], 2)
        self.assertEqual(res["deleted_local_files"], 1)

        mock_bot.delete_message.assert_called_once_with(chat_id=-1009999999, message_id=666)
        mock_s3.delete_manga_objects.assert_called_once_with("12345")
        mock_telegraph.delete_pages.assert_called_once()
        self.assertFalse(task_staging.exists())
        self.assertIsNone(self.store.get_manga("jmcomic", "12345"))

    async def test_task_service_delete_channel_delete_exception_handled(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        task = await svc.download("77777", 1001, 1001)
        self.store.update(task["id"], channel_message_id=999)

        mock_bot = AsyncMock()
        mock_bot.delete_message.side_effect = Exception("Message already deleted")

        res = await svc.delete(
            reference="JM77777",
            user_id=1001,
            bot=mock_bot,
            channel_id=-1009999999,
        )
        self.assertEqual(res["deleted_channel_msgs"], 0)
        self.assertEqual(res["deleted_tasks"], 1)
        self.assertIsNone(self.store.get_manga("jmcomic", "77777"))

    async def test_task_service_delete_direct_telegraph_url(self):
        svc = TaskService(self.store, self.provider, allowed_user_ids=frozenset({1001}))
        mock_telegraph = AsyncMock()
        mock_telegraph.delete_page = AsyncMock(return_value=True)

        res = await svc.delete(
            reference="https://telegra.ph/my-manga-page",
            user_id=1001,
            telegraph_client=mock_telegraph,
        )
        self.assertEqual(res["source_id"], "Telegraph")
        self.assertEqual(res["deleted_telegraph_count"], 1)
        mock_telegraph.delete_page.assert_called_once_with("https://telegra.ph/my-manga-page")

    async def test_telegraph_client_delete_page(self):
        client = TelegraphClient(access_token="test_token")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"ok": True, "result": {"path": "test-page"}}

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_resp

        success = await client.delete_page("https://telegra.ph/test-page", session=mock_session)
        self.assertTrue(success)

        # Ensure post called editPage
        call_args = mock_session.post.call_args
        self.assertIn("/editPage", call_args[0][0])
        payload = call_args[1]["data"]
        self.assertEqual(payload["path"], "test-page")
        self.assertEqual(payload["title"], "已删除")

    async def test_telegraph_client_create_page_uuid_slug_and_rename(self):
        client = TelegraphClient(access_token="test_token", settle_delay=0)
        mock_create_resp = AsyncMock()
        mock_create_resp.status = 200
        mock_create_resp.json.return_value = {
            "ok": True,
            "result": {
                "path": "random-uuid-slug-11-15",
                "url": "https://telegra.ph/random-uuid-slug-11-15",
            },
        }

        mock_edit_resp = AsyncMock()
        mock_edit_resp.status = 200
        mock_edit_resp.json.return_value = {
            "ok": True,
            "result": {
                "path": "random-uuid-slug-11-15",
                "title": "实际漫画原标题",
            },
        }

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.side_effect = [mock_create_resp, mock_edit_resp]

        content = [{"tag": "p", "children": ["测试正文"]}]
        url = await client.create_page(
            session=mock_session,
            title="实际漫画原标题",
            content=content,
        )

        self.assertEqual(url, "https://telegra.ph/random-uuid-slug-11-15")
        self.assertEqual(mock_session.post.call_count, 2)

        # 1st call: createPage with random UUID as title
        call_create = mock_session.post.call_args_list[0]
        self.assertIn("/createPage", call_create[0][0])
        create_payload = call_create[1]["data"]
        create_title = create_payload["title"]
        self.assertNotEqual(create_title, "实际漫画原标题")
        import uuid
        uuid.UUID(create_title)

        # 2nd call: editPage restoring original title
        call_edit = mock_session.post.call_args_list[1]
        self.assertIn("/editPage", call_edit[0][0])
        edit_payload = call_edit[1]["data"]
        self.assertEqual(edit_payload["path"], "random-uuid-slug-11-15")
        self.assertEqual(edit_payload["title"], "实际漫画原标题")

    async def test_restore_and_verify_title_checks(self):
        client = TelegraphClient(access_token="test_token", settle_delay=0.01)
        mock_edit_resp = AsyncMock()
        mock_edit_resp.status = 200
        mock_edit_resp.json.return_value = {"ok": True, "result": {"title": "新标题"}}

        mock_get_api_resp = AsyncMock()
        mock_get_api_resp.status = 200
        mock_get_api_resp.json.return_value = {"ok": True, "result": {"title": "新标题"}}

        mock_get_web_resp = AsyncMock()
        mock_get_web_resp.status = 200
        mock_get_web_resp.text.return_value = "<html><head><title>新标题</title></head></html>"

        mock_session = MagicMock()
        mock_session.post.return_value.__aenter__.return_value = mock_edit_resp
        mock_session.get.return_value.__aenter__.side_effect = [mock_get_api_resp, mock_get_web_resp]

        await client._restore_and_verify_title(
            session=mock_session,
            page_path="test-path",
            page_url="https://telegra.ph/test-path",
            title="新标题",
            content=[],
            temp_uuid="old-uuid",
        )

        self.assertEqual(mock_session.post.call_count, 1)
        self.assertEqual(mock_session.get.call_count, 2)

    def test_staged_pages_supports_jpeg(self):
        staging = Path(self.temp_dir.name) / "test_staged"
        staging.mkdir(parents=True, exist_ok=True)
        jpeg_file = staging / "000001.jpeg"
        jpeg_file.write_bytes(b"dummy")
        pages = staged_pages(staging, 1)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0], jpeg_file)

    def test_s3_storage_delete_sanitizes_id(self):
        s3 = S3StorageClient(
            endpoint_url="https://r2.example.com",
            bucket_name="test-bucket",
            access_key_id="test-key",
            secret_access_key="test-secret",
        )
        # Empty or traversal ids should be rejected immediately with 0 deletions
        self.assertEqual(s3.delete_manga_objects_sync(""), 0)
        self.assertEqual(s3.delete_manga_objects_sync("../"), 0)
        self.assertEqual(s3.delete_manga_objects_sync("manga/*"), 0)

    async def test_create_chapter_page_pure_images(self):
        client = TelegraphClient(access_token="test_token", settle_delay=0)
        with patch.object(client, "create_page", return_value="https://telegra.ph/sample") as mock_cp:
            mock_session = AsyncMock()
            url = await client.create_chapter_page(
                mock_session,
                manga_title="测试标题",
                chapter_title="第1话",
                ordinal=1,
                image_urls=["https://img.example.com/1.webp", "https://img.example.com/2.webp"],
                channel_url="https://t.me/test_ch",
            )
            self.assertEqual(url, "https://telegra.ph/sample")
            mock_cp.assert_called_once()
            call_args = mock_cp.call_args
            passed_nodes = call_args[0][2]
            # Verify nodes only contain figure/img, no p or hr tags
            self.assertEqual(len(passed_nodes), 2)
            for node in passed_nodes:
                self.assertEqual(node["tag"], "figure")
                self.assertEqual(node["children"][0]["tag"], "img")



if __name__ == "__main__":
    unittest.main()
