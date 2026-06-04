# -*- coding: utf-8 -*-
"""
auth_middleware.py — FastAPI Auth Middleware
- Cookie-based session
- User injection ใน routes
"""
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from user_manager import user_manager
from typing import Optional


def get_current_user(request: Request) -> Optional[dict]:
    """ดึง user จาก cookie token — คืน None ถ้าไม่ได้ login"""
    token = request.cookies.get("gemma_token")
    if not token:
        return None
    return user_manager.verify_token(token)


def require_user(request: Request) -> dict:
    """ต้อง login — raise 401 ถ้าไม่ได้ login"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบก่อน")
    return user


def require_admin(request: Request) -> dict:
    """ต้องเป็น admin"""
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึง")
    return user
