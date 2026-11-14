"""Telegraph publishing module with automatic ultra-long pagination, navigation bars, and exponential backoff retries."""

import math
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

import httpx

try:
    from src.config import Settings
    from src.utils import setup_logger
except ImportError:
    from config import Settings
    from utils import setup_logger

logger = setup_logger("telegraph_pub")

TELEGRAPH_API_BASE = "https://api.telegra.ph"


class TelegraphPublisher:
    """Publishes manga image sequences to Telegraph with automatic pagination."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.access_token = settings.telegraph_access_token
        self.author_name = settings.telegraph_author_name
        self.short_name = settings.telegraph_short_name
        self.max_images_per_page = max(1, settings.telegraph_max_images_per_page)

    def _call_api_sync(self, endpoint: str, data: dict[str, Any], max_retries: int = 5) -> dict[str, Any]:
        """Call Telegraph API with exponential backoff on FLOOD_WAIT and network timeouts."""
        url = f"{TELEGRAPH_API_BASE}/{endpoint}"
        backoff = 2.0

        for attempt in range(1, max_retries + 1):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, json=data)
                    resp.raise_for_status()
                    res_json = resp.json()

                    if res_json.get("ok"):
                        return res_json.get("result", {})

                    error_msg = res_json.get("error", "Unknown Telegraph Error")
                    logger.warning(f"Telegraph API 错误 [{endpoint}] (尝试 {attempt}/{max_retries}): {error_msg}")

                    # Check for FloodWait error
                    match = re.search(r"FLOOD_WAIT_?(\d+)?", error_msg, re.IGNORECASE)
                    wait_seconds = int(match.group(1)) if match and match.group(1) else int(backoff)
                    logger.info(f"触发 Telegraph 限流，等待 {wait_seconds} 秒后重试...")
                    time.sleep(wait_seconds)
                    backoff *= 2.0

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                logger.warning(f"Telegraph 网络请求异常 (尝试 {attempt}/{max_retries}): {exc}")
                if attempt == max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2.0

        raise RuntimeError(f"Telegraph API [{endpoint}] 超出最大重试次数")

    def ensure_account(self) -> str:
        """Ensure an access token exists, creating a new account if absent."""
        if self.access_token:
            return self.access_token

        logger.info(f"未配置 TELEGRAPH_ACCESS_TOKEN，正在创建新 Telegraph 账号 ({self.short_name})...")
        payload = {
            "short_name": self.short_name,
            "author_name": self.author_name,
        }
        res = self._call_api_sync("createAccount", payload)
        self.access_token = res["access_token"]
        print("\n" + "=" * 60)
        print("🎉 成功创建 Telegraph 账号!")
        print(f"• Short Name: {self.short_name}")
        print(f"• Author Name: {self.author_name}")
        print(f"• Access Token: {self.access_token}")
        print("⚠️ 请将该 Token 复制保存到 .env 文件的 TELEGRAPH_ACCESS_TOKEN 以便下次复用:")
        print(f"TELEGRAPH_ACCESS_TOKEN={self.access_token}")
        print("=" * 60 + "\n")
        return self.access_token

    def _create_page_raw(self, title: str, content_nodes: list[dict[str, Any]]) -> dict[str, str]:
        """Create a single Telegraph page returning {'path': ..., 'url': ...}.

        Controls URL suffix by creating page with random UUID title, then immediately
        renaming back to the actual title.
        """
        token = self.ensure_account()
        random_title = str(uuid.uuid4())
        payload = {
            "access_token": token,
            "title": random_title,
            "author_name": self.author_name,
            "content": content_nodes,
            "return_content": False,
        }
        res = self._call_api_sync("createPage", payload)
        path = res["path"]
        url = res["url"]

        # Rename back to original title
        try:
            self._edit_page_raw(path, title[:64], content_nodes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"更新 Telegraph 页面原标题失败 path={path}: {exc}")

        return {"path": path, "url": url}

    def _edit_page_raw(self, path: str, title: str, content_nodes: list[dict[str, Any]]) -> None:
        """Edit an existing Telegraph page."""
        token = self.ensure_account()
        payload = {
            "access_token": token,
            "path": path,
            "title": title[:64],
            "author_name": self.author_name,
            "content": content_nodes,
            "return_content": False,
        }
        self._call_api_sync("editPage", payload)

    def delete_page(self, path_or_url: str) -> bool:
        """Overwrite a Telegraph page to clear all comic images and mark as deleted."""
        clean_path = str(path_or_url).strip()
        if "telegra.ph/" in clean_path:
            clean_path = clean_path.split("telegra.ph/", 1)[1]
        clean_path = clean_path.split("?")[0].split("#")[0].strip("/")
        if not clean_path:
            return False
        empty_content = [{"tag": "p", "children": ["该内容已被删除或下架。"]}]
        try:
            token = self.ensure_account()
            payload = {
                "access_token": token,
                "path": clean_path,
                "title": "已删除",
                "author_name": "",
                "content": empty_content,
                "return_content": False,
            }
            self._call_api_sync("editPage", payload)
            logger.info(f"✅ Telegraph 页面已清空删除: {clean_path}")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"删除 Telegraph 页面失败 path={clean_path}: {exc}")
            return False

    def delete_pages(self, paths_or_urls: Sequence[str]) -> int:
        """Delete multiple Telegraph pages sequentially."""
        deleted_count = 0
        for item in paths_or_urls:
            if self.delete_page(item):
                deleted_count += 1
        return deleted_count

    def publish_chapter(self, title: str, image_urls: Sequence[str]) -> list[str]:
        """Publish image URLs to Telegraph, splitting into multiple chapters if exceeding limit.

        Adds inter-page navigation bars for multi-part chapters.
        """
        if not image_urls:
            raise ValueError("没有图片可发布到 Telegraph")

        total_urls = len(image_urls)
        limit = self.max_images_per_page

        # Single page publication
        if total_urls <= limit:
            logger.info(f"图片数量 {total_urls} <= {limit}，单页发布...")
            nodes = [{"tag": "img", "attrs": {"src": url}} for url in image_urls]
            page_info = self._create_page_raw(title, nodes)
            logger.info(f"✅ Telegraph 单篇发布成功: {page_info['url']}")
            return [page_info["url"]]

        # Ultra-long manga: multi-page pagination
        num_pages = math.ceil(total_urls / limit)
        logger.info(f"图片总数 {total_urls} > {limit}，自动拆分为 {num_pages} 卷发布...")

        # Subtitle generator
        subtitles: list[str] = []
        if num_pages == 2:
            subtitles = [f"{title} (上)", f"{title} (下)"]
        elif num_pages == 3:
            subtitles = [f"{title} (上)", f"{title} (中)", f"{title} (下)"]
        else:
            subtitles = [f"{title} (第{i+1}/{num_pages}卷)" for i in range(num_pages)]

        # Chunk image URLs
        chunks: list[list[str]] = []
        for i in range(num_pages):
            start = i * limit
            end = min(start + limit, total_urls)
            chunks.append(list(image_urls[start:end]))

        # Phase 1: Create all pages first to acquire their URLs
        created_pages: list[dict[str, str]] = []
        for i in range(num_pages):
            part_title = subtitles[i]
            img_nodes = [{"tag": "img", "attrs": {"src": url}} for url in chunks[i]]
            logger.info(f"正在创建分卷 [{i+1}/{num_pages}]: {part_title} ({len(chunks[i])} 张图片)...")
            res = self._create_page_raw(part_title, img_nodes)
            created_pages.append(res)
            time.sleep(0.5)

        # Phase 2: Inject navigation bars at top and bottom of each page
        logger.info("注入分卷互跳导航栏...")
        page_urls = [p["url"] for p in created_pages]

        for i in range(num_pages):
            part_title = subtitles[i]
            part_path = created_pages[i]["path"]

            nav_children: list[Any] = []
            if i > 0:
                prev_url = page_urls[i - 1]
                nav_children.append({"tag": "a", "attrs": {"href": prev_url}, "children": ["⬅️ 上一卷"]})

            if i > 0 and i < num_pages - 1:
                nav_children.append("  |  ")

            if i < num_pages - 1:
                next_url = page_urls[i + 1]
                nav_children.append({"tag": "a", "attrs": {"href": next_url}, "children": ["➡️ 下一卷"]})

            nav_bar = {"tag": "p", "children": nav_children}
            img_nodes = [{"tag": "img", "attrs": {"src": url}} for url in chunks[i]]

            full_content = [nav_bar] + img_nodes + [nav_bar]
            try:
                self._edit_page_raw(part_path, part_title, full_content)
                logger.info(f"分卷 [{i+1}/{num_pages}] 导航栏更新完成: {page_urls[i]}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"分卷 [{i+1}/{num_pages}] 导航栏注入警告 (不影响阅读): {e}")

        logger.info(f"✅ Telegraph 全部 {num_pages} 卷发布完成")
        return page_urls
