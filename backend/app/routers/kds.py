"""后厨大屏 KDS 接口：三栏看板（待出餐/出餐中/已出餐）、逐道出餐。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import require_roles
from ..services import order_service as osv

router = APIRouter(prefix="/api/kds", tags=["kds"])

kds_staff = require_roles(models.ROLE_KITCHEN, models.ROLE_ADMIN)


@router.get("/columns")
def columns(db: Session = Depends(get_db), staff=Depends(kds_staff)):
    return osv.kds_columns(db)


@router.get("/done")
def done(db: Session = Depends(get_db), staff=Depends(kds_staff)):
    """当日全部出餐完成的订单（整单出完即入列，含已结账），按整单出完时间倒序。"""
    return osv.kds_done(db)


@router.post("/items/{item_id}/out")
def item_out(item_id: int, db: Session = Depends(get_db), staff=Depends(kds_staff)):
    """逐道出餐（幂等）：重复点击不会重复扣库存。"""
    item = db.get(models.OrderItem, item_id)
    if item is None:
        raise HTTPException(404, "菜品明细不存在")
    order = osv.item_out(db, item)
    return {"item_id": item.id, "status": item.status, "order_status": order.status}
