import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from database import IS_VERCEL, _get_clean_env

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 天免密登录态


def _get_auth_credentials() -> tuple[str, str]:
    username = _get_clean_env("AUTH_USERNAME")
    password = _get_clean_env("AUTH_PASSWORD")
    return username, password


def auth_is_configured() -> bool:
    username, password = _get_auth_credentials()
    return bool(username and password)


def auth_is_required() -> bool:
    """
    线上 Vercel 环境默认强制开启登录拦截（即使漏配环境变量也 fail-closed 拒绝裸奔）；
    本地开发仅在配置了 AUTH_USERNAME + AUTH_PASSWORD 时启用鉴权。
    """
    if auth_is_configured():
        return True
    return IS_VERCEL


def _get_signing_key() -> bytes:
    custom_secret = _get_clean_env("AUTH_SECRET")
    username, password = _get_auth_credentials()
    seed = custom_secret or f"{username}:{password}:i2e-studio-auth-v1"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def verify_login_credentials(username: str, password: str) -> bool:
    cfg_user, cfg_pass = _get_auth_credentials()
    if not cfg_user or not cfg_pass:
        return False
    u_ok = hmac.compare_digest((username or "").strip(), cfg_user)
    p_ok = hmac.compare_digest(password or "", cfg_pass)
    return u_ok and p_ok


def create_access_token(username: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> Dict[str, Any]:
    now = int(time.time())
    exp = now + ttl_seconds
    payload = {"sub": username, "iat": now, "exp": exp}
    raw_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_payload).decode("ascii").rstrip("=")
    sig = hmac.new(_get_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return {
        "access_token": f"{payload_b64}.{sig}",
        "token_type": "bearer",
        "username": username,
        "expires_at": exp,
    }


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    payload_b64, _, provided_sig = token.partition(".")
    if not payload_b64 or not provided_sig:
        return None
    expected_sig = hmac.new(_get_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        cfg_user, _ = _get_auth_credentials()
        if cfg_user and payload.get("sub") != cfg_user:
            return None
        return payload
    except Exception:
        return None


def extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def get_optional_user(request: Request) -> Optional[str]:
    if not auth_is_required():
        return "local-dev"
    token = extract_bearer_token(request)
    payload = verify_access_token(token)
    if not payload:
        return None
    return str(payload.get("sub") or "")


def require_auth(request: Request) -> str:
    """FastAPI 全局鉴权依赖：未登录或 Token 失效时返回 401。"""
    if not auth_is_required():
        return "local-dev"
    if not auth_is_configured():
        raise HTTPException(
            status_code=503,
            detail="服务端尚未配置登录环境变量 AUTH_USERNAME / AUTH_PASSWORD，已自动启用安全拦截。",
        )
    token = extract_bearer_token(request)
    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="请先登录账号后再使用（登录态无效或已过期）",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return str(payload.get("sub") or "admin")
