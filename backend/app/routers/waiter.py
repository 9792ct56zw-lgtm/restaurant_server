"""服务员端接口：待确认队列、确认/拒单/修改、催单、代客下单、桌台总览、清台、结账。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import require_roles
from ..services import order_service as osv

router = APIRouter(prefix="/api/waiter", tags=["waiter"])

service_staff = require_roles(models.ROLE_WAITER, models.ROLE_ADMIN, models.ROLE_CASHIER)


@router.get("/queue")
def queue(db: Session = Depends(get_db), staff=Depends(service_staff)):
    """待确认队列（顾客下单，需二次确认）。"""
    orders = (
        db.query(models.Order)
        .filter(models.Order.status == models.ORDER_PENDING)
        .order_by(models.Order.id)
        .all()
    )
    return [osv.order_dict(db, o) for o in orders]


@router.get("/orders")
def list_orders(
    status: str = "",
    table_id: int = 0,
    db: Session = Depends(get_db),
    staff=Depends(service_staff),
):
    q = db.query(models.Order)
    if status:
        q = q.filter(models.Order.status == status)
    if table_id:
        q = q.filter(models.Order.table_id == table_id)
    orders = q.order_by(models.Order.id.desc()).limit(100).all()
    return [osv.order_dict(db, o) for o in orders]


@router.get("/settle-queue")
def settle_queue(db: Session = Depends(get_db), staff=Depends(service_staff)):
    """收银台队列：已确认/已出餐且未结账的订单（服务员可直接收款）。"""
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.status.in_([models.ORDER_CONFIRMED, models.ORDER_SERVED]),
            models.Order.pay_status != models.PAY_PAID,
        )
        .order_by(models.Order.id)
        .all()
    )
    return [osv.order_dict(db, o) for o in orders]


@router.get("/add-queue")
def add_queue(db: Session = Depends(get_db), staff=Depends(service_staff)):
    """加菜待确认队列：顾客补单加菜，需服务员确认后才推送后厨。"""
    return osv.add_queue(db)


@router.post("/orders/{order_id}/confirm-add")
def confirm_add(order_id: int, db: Session = Depends(get_db), staff=Depends(service_staff)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.confirm_add(db, order)
    return osv.order_dict(db, order)


@router.post("/orders/{order_id}/add")
def add_dishes(order_id: int, body: schemas.AddItemsIn, db: Session = Depends(get_db),
               staff=Depends(service_staff)):
    """代客加菜：服务员替顾客追加菜品，直达 KDS（标「加」）。"""
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.waiter_add_items(db, order, body.items)
    return osv.order_dict(db, order)


@router.get("/orders/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db), staff=Depends(service_staff)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    return osv.order_dict(db, order)


@router.post("/orders/{order_id}/confirm")
def confirm(order_id: int, db: Session = Depends(get_db), staff=Depends(service_staff)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.confirm_order(db, order, staff)
    return osv.order_dict(db, order)


@router.post("/orders/{order_id}/reject")
def reject(order_id: int, body: schemas.RejectIn, db: Session = Depends(get_db),
           staff=Depends(service_staff)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.reject_order(db, order, body.reason)
    return osv.order_dict(db, order)


@router.post("/orders/{order_id}/modify")
def modify(order_id: int, body: schemas.ModifyIn, db: Session = Depends(get_db),
           staff=Depends(service_staff)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.modify_order(db, order, body.remark, body.items, body.people, body.utensils)
    return osv.order_dict(db, order)


@router.post("/orders/{order_id}/urge")
def urge(order_id: int, db: Session = Depends(get_db), staff=Depends(service_staff)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.urge_order(db, order)
    return osv.order_dict(db, order)


# 注：服务员端无结账收款能力，结账统一由后台（admin/cashier）订单收银完成，
# 故此处不再提供 settle 接口（见 /api/admin/orders/{order_id}/settle）。


@router.post("/orders")
def create_for_table(body: schemas.OrderCreateIn, db: Session = Depends(get_db),
                     staff=Depends(service_staff)):
    """代客下单/外带下单：提交即已确认，直接推后厨。"""
    order = osv.create_order(
        db, body.table_id, None, body.items, body.remark, body.people, body.utensils,
        source="waiter", staff=staff, takeout=body.takeout,
    )
    return osv.order_dict(db, order)


@router.get("/tables")
def tables(db: Session = Depends(get_db), staff=Depends(service_staff)):
    return osv.table_overview(db)


@router.post("/tables/{table_id}/clean")
def clean_table(table_id: int, db: Session = Depends(get_db), staff=Depends(service_staff)):
    table = db.get(models.DiningTable, table_id)
    if table is None:
        raise HTTPException(404, "桌台不存在")
    return {"id": table.id, "name": table.name, "dirty": osv.clean_table(db, table).dirty}
