"""员工登录与会话信息。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import current_staff
from ..security import create_token, verify_password

router = APIRouter(prefix="/api/staff", tags=["staff"])


@router.post("/login")
def login(body: schemas.StaffLoginIn, db: Session = Depends(get_db)):
    staff = db.query(models.Staff).filter_by(username=body.username).first()
    if staff is None or not verify_password(body.password, staff.password_hash):
        raise HTTPException(401, "账号或密码错误")
    if staff.status != "active":
        raise HTTPException(403, "账号已停用，请联系店长")
    token = create_token(str(staff.id), {"type": "staff"})
    return {
        "token": token,
        "staff": {"id": staff.id, "username": staff.username,
                  "name": staff.name, "role": staff.role},
    }


@router.get("/me")
def me(db: Session = Depends(get_db), staff=Depends(current_staff)):
    return {"id": staff.id, "username": staff.username, "name": staff.name, "role": staff.role}
