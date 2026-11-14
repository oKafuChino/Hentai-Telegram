"""Command parsing and authorization checks."""


def parse_command(text: str) -> tuple[str, str, str]:
    parts = (text or "").strip().split(maxsplit=1)
    head = parts[0] if parts else ""
    command, _, recipient = head.partition("@")
    return command.lower(), recipient, parts[1] if len(parts) > 1 else ""
