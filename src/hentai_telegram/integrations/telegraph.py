import asyncio
import json
import logging
import uuid
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
        s3_client: Any | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.upload_url = upload_url.strip() or "https://catbox.moe/user/api.php"
        self.access_token = access_token.strip()
        self.author_name = author_name
        self.author_url = author_url
        self.token_file = token_file
        self.catbox_userhash = catbox_userhash.strip()
        self.s3_client = s3_client

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

    async def upload_image(
        self,
        session: aiohttp.ClientSession,
        image_path: Path,
        key: str | None = None,
    ) -> str:
        """Upload a single local image to storage and return the public URL.

        Priority:
        1. Cloudflare R2 / Backblaze B2 S3 storage (if configured)
        2. Catbox API (https://catbox.moe/user/api.php)
        3. Custom Telegraph-compatible image upload proxy
        """
        if not image_path.is_file():
            raise TelegraphError(f"图片不存在: {image_path.name}", retryable=False)

        # 1. Cloudflare R2 / Backblaze B2 S3 Object Storage
        if self.s3_client and self.s3_client.is_configured():
            try:
                return await self.s3_client.upload_file(image_path, object_key=key)
            except Exception as exc:
                logger.error("s3_upload_failed key=%s error=%s", key, exc)
                raise TelegraphError(f"R2/B2 对象存储上传失败: {exc}") from exc

        # 2. HTTP upload (Catbox / Custom proxy)
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
            except (TimeoutError, aiohttp.ClientError) as exc:
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
                        except Exception:  # noqa: BLE001
                            text = (await resp.text()).strip()
                            if text.startswith("http"):
                                return text
                            raise TelegraphError(f"代理上传响应格式错误: {text[:200]}")

                        if isinstance(body, list) and len(body) > 0 and "src" in body[0]:
                            src = body[0]["src"]
                            if src.startswith(("http://", "https://")):
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
            except (TimeoutError, aiohttp.ClientError) as exc:
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
        # Use random UUID as temporary title to control telegraph page path/slug
        random_title = str(uuid.uuid4())
        payload = {
            "access_token": token,
            "title": random_title,
            "author_name": (author_name or self.author_name)[:128],
            "author_url": (author_url or self.author_url)[:512],
            "content": json.dumps(content, ensure_ascii=False),
            "return_content": False,
        }

        created_res = None
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
                    created_res = data["result"]
                    break
            except (TimeoutError, aiohttp.ClientError) as exc:
                if attempt == 2:
                    raise TelegraphError(f"发布页面网络失败: {exc}") from exc
                await asyncio.sleep(1 + attempt)

        if not created_res:
            raise TelegraphError("发布页面重试耗尽")

        page_path = created_res.get("path") or created_res["url"].split("telegra.ph/")[-1]
        page_url = created_res["url"]

        # Rename back to original title via editPage while preserving random slug path
        try:
            await self.edit_page(
                session=session,
                path=page_path,
                title=title,
                content=content,
                author_name=author_name or self.author_name,
                author_url=author_url or self.author_url,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegraph_restore_title_failed path=%s error=%s", page_path, exc)

        return page_url

    async def edit_page(
        self,
        session: aiohttp.ClientSession,
        path: str,
        title: str,
        content: list[dict[str, Any]],
        author_name: str | None = None,
        author_url: str | None = None,
    ) -> bool:
        """Edit an existing Telegraph page."""
        token = await self.ensure_token(session)
        clean_path = path.strip()
        if "telegra.ph/" in clean_path:
            clean_path = clean_path.split("telegra.ph/", 1)[1]
        clean_path = clean_path.split("?")[0].split("#")[0].strip("/")

        eff_author = self.author_name if author_name is None else author_name
        eff_author_url = self.author_url if author_url is None else author_url

        payload = {
            "access_token": token,
            "path": clean_path,
            "title": (title[:250] or "已删除"),
            "author_name": (eff_author or "")[:128],
            "author_url": (eff_author_url or "")[:512],
            "content": json.dumps(content, ensure_ascii=False),
            "return_content": False,
        }

        for attempt in range(3):
            try:
                async with session.post(
                    f"{self.api_url}/editPage",
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise TelegraphError(f"HTTP {resp.status}: {text[:200]}")
                    data = await resp.json()
                    if not data.get("ok"):
                        error_msg = str(data.get("error", "未知错误"))
                        if any(err in error_msg for err in ("PAGE_NOT_FOUND", "ACCESS_DENIED", "PAGE_SAVE_FAILED")):
                            logger.warning("edit_telegraph_page_skipped path=%s error=%s", clean_path, error_msg)
                            return False
                        if "FLOOD_WAIT" in error_msg.upper():
                            import re

                            match = re.search(r"FLOOD_WAIT_?(\d+)?", error_msg, re.IGNORECASE)
                            wait_sec = int(match.group(1)) if match and match.group(1) else (attempt + 1) * 2
                            logger.info("telegraph_flood_wait path=%s wait=%s", clean_path, wait_sec)
                            await asyncio.sleep(wait_sec)
                            continue
                        raise TelegraphError(f"编辑页面失败: {error_msg}")
                    return True
            except (TimeoutError, aiohttp.ClientError) as exc:
                if attempt == 2:
                    raise TelegraphError(f"编辑页面网络失败: {exc}") from exc
                await asyncio.sleep(1 + attempt)

        raise TelegraphError("编辑页面重试耗尽")

    async def delete_page(
        self,
        path_or_url: str,
        session: aiohttp.ClientSession | None = None,
    ) -> bool:
        """Overwrite a Telegraph page to clear all comic images and mark as deleted."""
        clean_path = str(path_or_url).strip()
        if "telegra.ph/" in clean_path:
            clean_path = clean_path.split("telegra.ph/", 1)[1]
        clean_path = clean_path.split("?")[0].split("#")[0].strip("/")
        if not clean_path:
            return False

        empty_nodes: list[dict[str, Any]] = [
            {"tag": "p", "children": ["该内容已被删除或下架。"]},
        ]

        if session is not None:
            return await self.edit_page(
                session=session,
                path=clean_path,
                title="已删除",
                content=empty_nodes,
                author_name="",
                author_url="",
            )

        async with aiohttp.ClientSession() as new_session:
            return await self.edit_page(
                session=new_session,
                path=clean_path,
                title="已删除",
                content=empty_nodes,
                author_name="",
                author_url="",
            )

    async def delete_pages(
        self,
        urls_or_paths: list[str],
        session: aiohttp.ClientSession | None = None,
    ) -> int:
        """Overwrite multiple Telegraph pages, returning count of successfully cleared pages."""
        if not urls_or_paths:
            return 0
        deleted_count = 0
        if session is not None:
            for item in urls_or_paths:
                try:
                    if await self.delete_page(item, session=session):
                        deleted_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("delete_telegraph_page_failed item=%s error=%s", item, exc)
            return deleted_count

        async with aiohttp.ClientSession() as new_session:
            for item in urls_or_paths:
                try:
                    if await self.delete_page(item, session=new_session):
                        deleted_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("delete_telegraph_page_failed item=%s error=%s", item, exc)
            return deleted_count

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
            {
                "tag": "figure",
                "children": [{"tag": "img", "attrs": {"src": url}}],
            }
            for url in image_urls
        ]

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
        raw_sid = str(source_id).strip()
        clean_sid = raw_sid[2:] if raw_sid.upper().startswith("JM") else raw_sid
        nodes: list[dict[str, Any]] = [
            {"tag": "h3", "children": [manga_title]},
            {"tag": "p", "children": ["📖 来源: JMComic"]},
            {"tag": "p", "children": [f"📝 作者: {', '.join(authors) if authors else '未知'}"]},
            {"tag": "p", "children": [f"🏷 标签: {', '.join(tags) if tags else '无'}"]},
            {"tag": "p", "children": [f"🆔 漫画ID: JM{clean_sid}"]},
        ]
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

        return await self.create_page(session, page_title, nodes)
