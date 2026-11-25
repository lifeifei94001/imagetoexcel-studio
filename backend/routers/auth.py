from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from database import DB_MODE
from services.auth_service import (
    auth_is_configured,
    auth_is_required,
    create_access_token,
    get_optional_user,
    verify_login_credentials,
)
from services.b2_storage import b2_is_enabled, get_b2_bucket_name
from services.ocr_service import get_active_model, has_gemini_key

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/health")
def health_check(request: Request) -> Dict[str, Any]:
    """基础健康检查：未登录时仅暴露存活状态与鉴权开关，登录后返回完整引擎状态。"""
    user = get_optional_user(request)
    base: Dict[str, Any] = {
        "status": "ok",
        "auth_required": auth_is_required(),
        "authenticated": bool(user),
    }
    if user:
        base.update(
            {
                "username": user,
                "db_mode": DB_MODE,
                "gemini_configured": has_gemini_key(),
                "model": get_active_model(),
                "b2_configured": b2_is_enabled(),
                "b2_bucket": get_b2_bucket_name(),
            }
        )
    return base


@router.get("/auth/status")
def auth_status(request: Request) -> Dict[str, Any]:
    user = get_optional_user(request)
    return {
        "auth_required": auth_is_required(),
        "auth_configured": auth_is_configured(),
        "authenticated": bool(user),
        "username": user or "",
    }


@router.post("/auth/login")
def login(req: LoginRequest) -> Dict[str, Any]:
    if not auth_is_required():
        return create_access_token(req.username.strip() or "local-dev")
    if not auth_is_configured():
        raise HTTPException(
            status_code=503,
            detail="服务端尚未配置 AUTH_USERNAME / AUTH_PASSWORD，请在 Vercel 环境变量中配置后重新部署。",
        )
    if not verify_login_credentials(req.username, req.password):
        raise HTTPException(status_code=401, detail="用户名或密码不正确，请检查后重试")
    return create_access_token(req.username.strip())
