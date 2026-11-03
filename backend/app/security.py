from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from secrets import token_hex

from app.config import settings


class AuthenticationError(ValueError):
    pass


def verify_reviewer_token(token: str | None) -> str:
    if token:
        try:
            return verify_admin_token(token)
        except AuthenticationError:
            pass
    if not settings.reviewer_token:
        if settings.app_env == "development":
            return "审核员"
        raise AuthenticationError("审核令牌未配置")
    if not token or not hmac.compare_digest(token, settings.reviewer_token):
        raise AuthenticationError("审核令牌无效")
    return "审核员"


def verify_admin_password(password: str) -> None:
    if not settings.admin_password:
        raise AuthenticationError("管理员口令尚未初始化")
    if not hmac.compare_digest(password, settings.admin_password):
        raise AuthenticationError("管理员口令错误")


def _session_secret() -> bytes:
    if settings.admin_session_secret:
        return settings.admin_session_secret.encode("utf-8")
    if settings.app_env == "development":
        return b"monthly-award-development-only"
    raise AuthenticationError("管理员会话密钥未配置")


def supplemental_share_token(month: str, row_id: str) -> str:
    """Read-only, row-scoped capability for links in archived Excel files."""
    return hmac.new(_session_secret(), f'supplemental-evidence:{month}:{row_id}'.encode(), hashlib.sha256).hexdigest()


def verify_supplemental_share(month: str, row_id: str, token: str) -> None:
    if not hmac.compare_digest(supplemental_share_token(month, row_id), token):
        raise AuthenticationError('增补附件链接无效')


def issue_admin_token(ttl_seconds: int = 8 * 60 * 60) -> str:
    payload = {
        "role": "admin",
        "exp": int(time.time()) + ttl_seconds,
        "nonce": token_hex(8),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    signature = hmac.new(_session_secret(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def verify_admin_token(token: str | None) -> str:
    if not token or "." not in token:
        raise AuthenticationError("管理员会话无效")
    body_text, signature_text = token.split(".", 1)
    body = body_text.encode()
    padding = "=" * (-len(signature_text) % 4)
    try:
        signature = base64.urlsafe_b64decode(signature_text + padding)
        expected = hmac.new(_session_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError("管理员会话无效")
        body_padding = "=" * (-len(body_text) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body_text + body_padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("管理员会话无效") from exc
    if payload.get("role") != "admin" or int(payload.get("exp", 0)) < int(time.time()):
        raise AuthenticationError("管理员会话已过期")
    return "管理员"


def issue_download_token(export_id: str, ttl_seconds: int = 10 * 60) -> str:
    expires = int(time.time()) + ttl_seconds
    body = f"{export_id}:{expires}"
    signature = hmac.new(_session_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_download_token(export_id: str, token: str | None) -> None:
    if not token or "." not in token:
        raise AuthenticationError("下载链接无效")
    expires_text, signature = token.split(".", 1)
    try:
        expires = int(expires_text)
    except ValueError as exc:
        raise AuthenticationError("下载链接无效") from exc
    if expires < int(time.time()):
        raise AuthenticationError("下载链接已过期")
    expected = hmac.new(_session_secret(), f"{export_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AuthenticationError("下载链接无效")
