"""依赖注入：数据库会话与身份认证。"""
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def _get_staff_from_cred(cred: Optional[HTTPAuthorizationCredentials], db: Session) -> models.Staff:
    if cred is None:
        raise HTTPException(401, "未登录")
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(401, "登录已过期，请重新登录")
    if payload.get("type") != "staff":
        raise HTTPException(403, "无权限")
    staff = db.get(models.Staff, int(payload["sub"]))
    if staff is None or staff.status != "active":
        raise HTTPException(401, "账号不可用")
    return staff


def current_staff(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.Staff:
    return _get_staff_from_cred(cred, db)


def require_roles(*roles):
    def checker(staff: models.Staff = Depends(current_staff)) -> models.Staff:
        if staff.role not in roles:
            raise HTTPException(403, "无权限")
        return staff

    return checker


def get_customer_optional(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> Optional[models.Customer]:
    """顾客 token 可选（无 token 也能点餐，customer_id 置空）。"""
    if cred is None:
        return None
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        return None
    if payload.get("type") != "customer":
        return None
    return db.get(models.Customer, int(payload["sub"]))
