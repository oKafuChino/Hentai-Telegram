import asyncio
import json
import logging
from pathlib import Path
from typing import Any
import aiohttp

from hentai_telegram.domain.errors import JobError

logger = logging.getLogger(__name__)


class TelegraphError(JobError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__("TELEGRAPH_ERROR", f"Telegraph 操作失败: {message}", retryable=retryable)


class TelegraphClient:
    def __init__(
        self,
        api_url: str = "https://api.telegra.ph",
        upload_url: str = "https://catbox.moe/user/api.php",
        access_token: str = "",
        author_name: str = "Hentai-Telegram",
        author_url: str = "",
        token_file: Path | None = None,
        catbox_userhash: str = "",
    ):
        self.api_url = api_url.rstrip("/")
        self.upload_url = upload_url.strip() or "https://catbox.moe/user/api.php"
        self.access_token = access_token.strip()
        self.author_name = author_name
        self.author_url = author_url
        self.token_file = token_file
        self.catbox_userhash = catbox_userhash.strip()

    async def ensure_token(self, session: aiohttp.ClientSession) -> str:
        if self.access_token:
            return self.access_token
        if self.token_file and self.token_file.is_file():
            token = self.token_file.read_text().strip()
            if token:
                self.access_token = token
                return self.access_token

        logger.info("creating_telegraph_account author=%s", self.author_name)
        payload = {
            "short_name": (self.author_name[:32] or "HentaiBot"),
            "author_name": (self.author_name[:128] or "Hentai-Telegram"),
            "author_url": self.author_url[:512],
        }
        async with session.post(f"{self.api_url}/createAccount", json=payload) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise TelegraphError(f"创建 Telegraph 账号失败: {data.get('error', '未知错误')}")
            self.access_token = data["result"]["access_token"]

        if self.token_file:
            try:
                self.token_file.parent.mkdir(parents=True, exist_ok=True)
                self.token_file.write_text(self.access_token)
            except OSError as exc:
                logger.warning("save_telegraph_token_failed error=%s", exc)

        return self.access_token

    async def upload_image(self, session: aiohttp.ClientSession, image_path: Path) -> str:
        """Upload a single local image to image hosting service and return the hosted URL.

        Supports:
        1. Catbox API (https://catbox.moe/user/api.php)
        2. Telegraph-compatible image upload proxies (returning [{"src": "..."}])
        3. Automatic fallback to Catbox if a custom upload proxy fails.
        """
        if not image_path.is_file():
            raise TelegraphError(f"图片不存在: {image_path.name}", retryable=False)

        target_url = self.upload_url.strip()
        # telegra.ph/upload is permanently discontinued by Telegram; automatically use Catbox
        if not target_url or target_url.rstrip("/") == "https://telegra.ph/upload":
            target_url = "https://catbox.moe/user/api.php"

        try:
            return await self._do_upload(session, target_url, image_path)
        except Exception as exc:
            if "catbox.moe" not in target_url:
                logger.warning("custom_upload_failed url=%s error=%s; falling back to catbox", target_url, exc)
                try:
                    return await self._upload_to_catbox(session, image_path)
                except Exception as fb_exc:
                    raise TelegraphError(f"图片上传失败 (主服务与备用 Catbox 均失败): {exc} / {fb_exc}") from fb_exc
            raise

    async def _do_upload(self, session: aiohttp.ClientSession, url: str, image_path: Path) -> str:
        if "catbox.moe" in url:
            return await self._upload_to_catbox(session, image_path, endpoint=url)
        return await self._upload_to_telegraph_proxy(session, url, image_path)

    async def _upload_to_catbox(
        self,
        session: aiohttp.ClientSession,
        image_path: Path,
        endpoint: str = "https://catbox.moe/user/api.php",
    ) -> str:
        ext = image_path.suffix.lower()
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "image/jpeg")

        for attempt in range(3):
            try:
                with image_path.open("rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("reqtype", "fileupload")
                    if self.catbox_userhash:
                        form.add_field("userhash", self.catbox_userhash)
                    form.add_field(
                        "fileToUpload",
                        f,
                        filename=image_path.name,
                        content_type=content_type,
                    )
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    }
                    async with session.post(endpoint, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        text = (await resp.text()).strip()
                        if resp.status == 200 and text.startswith("http"):
                            return text
                        raise TelegraphError(f"Catbox 上传失败 HTTP {resp.status}: {text[:200]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise TelegraphError(f"Catbox 连接失败: {exc}")
                await asyncio.sleep(1 + attempt)

        raise TelegraphError("Catbox 上传重试耗尽")

    async def _upload_to_telegraph_proxy(
        self,
        session: aiohttp.ClientSession,
        url: str,
        image_path: Path,
    ) -> str:
        ext = image_path.suffix.lower()
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(ext, "image/jpeg")

        for attempt in range(3):
            try:
                with image_path.open("rb") as f:
                    form = aiohttp.FormData()
                    form.add_field(
                        "file",
                        f,
                        filename=image_path.name,
                        content_type=content_type,
                    )
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    }
                    async with session.post(url, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            raise TelegraphError(f"HTTP {resp.status}: {text[:200]}")
                        try:
                            body = await resp.json(content_type=None)
                        except Exception:
                            text = (await resp.text()).strip()
                            if text.startswith("http"):
                                return text
                            raise TelegraphError(f"代理上传响应格式错误: {text[:200]}")

                        if isinstance(body, list) and len(body) > 0 and "src" in body[0]:
                            src = body[0]["src"]
                            if src.startswith("http://") or src.startswith("https://"):
                                return src
                            base = "https://telegra.ph"
                            if "://" in url:
                                parts = url.split("://", 1)
                                domain = parts[1].split("/", 1)[0]
                                base = f"{parts[0]}://{domain}"
                            return f"{base}{src}"
                        if isinstance(body, dict):
                            if "url" in body:
                                return body["url"]
                            if "src" in body:
                                return body["src"]
                            if "error" in body:
                                raise TelegraphError(f"代理上传响应错误: {body['error']}")
                        raise TelegraphError(f"代理上传返回未知数据: {body}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise TelegraphError(f"代理上传连接失败: {exc}")
                await asyncio.sleep(1 + attempt)

        raise TelegraphError("代理上传重试耗尽")

    async def create_page(
        self,
        session: aiohttp.ClientSession,
        title: str,
        content: list[dict[str, Any]],
        author_name: str = "",
        author_url: str = "",
    ) -> str:
        token = await self.ensure_token(session)
        payload = {
            "access_token": token,
            "title": (title[:250] or "Manga"),
            "author_name": (author_name or self.author_name)[:128],
            "author_url": (author_url or self.author_url)[:512],
            "content": json.dumps(content, ensure_ascii=False),
            "return_content": False,
        }

        for attempt in range(3):
            try:
                async with session.post(
                    f"{self.api_url}/createPage",
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise TelegraphError(f"HTTP {resp.status}: {text[:200]}")
                    data = await resp.json()
                    if not data.get("ok"):
                        raise TelegraphError(f"发布页面失败: {data.get('error', '未知错误')}")
                    return data["result"]["url"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise TelegraphError(f"发布页面网络失败: {exc}")
                await asyncio.sleep(1 + attempt)

        raise TelegraphError("发布页面重试耗尽")

    async def create_chapter_page(
        self,
        session: aiohttp.ClientSession,
        manga_title: str,
        chapter_title: str,
        ordinal: int,
        image_urls: list[str],
        channel_url: str = "",
    ) -> str:
        page_title = f"{manga_title} - 第{ordinal}话 {chapter_title}".strip()
        nodes: list[dict[str, Any]] = [
            {"tag": "p", "children": [f"📚 {manga_title} | 第 {ordinal} 话: {chapter_title}"]},
        ]
        if channel_url:
            nodes.append({
                "tag": "p",
                "children": [{"tag": "a", "attrs": {"href": channel_url}, "children": ["📢 关注频道获取最新漫画更新"]}],
            })
        nodes.append({"tag": "hr"})

        for url in image_urls:
            nodes.append({
                "tag": "figure",
                "children": [{"tag": "img", "attrs": {"src": url}}],
            })

        nodes.append({"tag": "hr"})
        if channel_url:
            nodes.append({
                "tag": "p",
                "children": [{"tag": "a", "attrs": {"href": channel_url}, "children": ["📢 关注 Telegram 频道"]}],
            })
        else:
            nodes.append({
                "tag": "p",
                "children": ["由 Hentai-Telegram 自动发布"],
            })

        return await self.create_page(session, page_title, nodes)

    async def create_index_page(
        self,
        session: aiohttp.ClientSession,
        manga_title: str,
        authors: tuple[str, ...],
        tags: tuple[str, ...],
        source_id: str,
        chapters: list[dict[str, Any]],
        channel_url: str = "",
    ) -> str:
        """Create a table of contents / index page for multi-chapter manga."""
        page_title = f"{manga_title} - 目录".strip()
        nodes: list[dict[str, Any]] = [
            {"tag": "h3", "children": [manga_title]},
            {"tag": "p", "children": [f"👨‍🎨 作者: {', '.join(authors) if authors else '未知'}"]},
            {"tag": "p", "children": [f"🏷 标签: {', '.join(tags) if tags else '无'}"]},
            {"tag": "p", "children": [f"🔢 漫画ID: JM{source_id}"]},
        ]
        if channel_url:
            nodes.append({
                "tag": "p",
                "children": [{"tag": "a", "attrs": {"href": channel_url}, "children": ["📢 关注 Telegram 频道"]}],
            })
        nodes.append({"tag": "hr"})
        nodes.append({"tag": "h4", "children": ["📖 章节列表"]})

        chapter_items = []
        for ch in chapters:
            url = ch.get("telegraph_url") or "#"
            ordinal = ch.get("ordinal", 1)
            title = ch.get("title", f"第 {ordinal} 话")
            chapter_items.append({
                "tag": "li",
                "children": [
                    {"tag": "a", "attrs": {"href": url}, "children": [f"第 {ordinal} 话: {title}"]},
                ],
            })

        nodes.append({"tag": "ol", "children": chapter_items})
        nodes.append({"tag": "hr"})
        if channel_url:
            nodes.append({
                "tag": "p",
                "children": [{"tag": "a", "attrs": {"href": channel_url}, "children": ["📢 关注 Telegram 频道"]}],
            })

        return await self.create_page(session, page_title, nodes)
