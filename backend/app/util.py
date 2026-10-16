import hmac
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def token_matches(provided: str | None, expected: str) -> bool:
    """恒定时间比较；按 UTF-8 字节走，compare_digest 不支持含非 ASCII 的字符串。"""
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
