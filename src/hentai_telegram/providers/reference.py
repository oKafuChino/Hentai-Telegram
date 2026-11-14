"""Strict input parsing. Only configured source domains are accepted."""
import re
from urllib.parse import urlparse


def parse_reference(reference: str, domains: tuple[str, ...] = ()) -> str:
    value = reference.strip()
    if value.lower().startswith("jm") and re.fullmatch(r"[1-9][0-9]{0,15}", value[2:]):
        return value[2:]
    if re.fullmatch(r"[1-9][0-9]{0,15}", value):
        return value

    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("只支持数字漫画 ID（如 123456 或 JM123456）或 HTTPS 漫画链接")

    if domains and parsed.hostname and parsed.hostname.lower() not in domains:
        raise ValueError(f"链接域名 {parsed.hostname} 不在白名单配置中")

    match = re.search(r"/(?:album|photo)/([1-9][0-9]{0,15})/?", parsed.path)
    if not match:
        raise ValueError("无法从链接解析漫画 ID")
    return match.group(1)
