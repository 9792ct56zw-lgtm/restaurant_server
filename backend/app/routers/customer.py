"""顾客端（微信小程序）接口：微信登录、菜单、下单、补单、三步进度。"""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_customer_optional
from ..security import create_token
from ..services import order_service as osv

router = APIRouter(prefix="/api/customer", tags=["customer"])


def _wx_code2session(code: str) -> str:
    """微信 code2session 换取 openid（本期开发默认走 mock）。"""
    if settings.wx_login_mock:
        return "mock_" + code
    if not settings.wx_appid or not settings.wx_secret:
        raise HTTPException(503, "未配置微信小程序凭据")
    try:
        resp = httpx.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": settings.wx_appid,
                "secret": settings.wx_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        data = resp.json()
    except Exception:
        raise HTTPException(502, "微信登录服务暂不可用")
    openid = data.get("openid")
    if not openid:
        raise HTTPException(401, "微信登录失败：" + str(data.get("errmsg", "未知错误")))
    return openid


@router.post("/login")
def login(body: schemas.CustomerLoginIn, db: Session = Depends(get_db)):
    openid = _wx_code2session(body.code)
    cust = db.query(models.Customer).filter_by(openid=openid).first()
    if cust is None:
        cust = models.Customer(openid=openid, nickname=body.nickname or "")
        db.add(cust)
        db.commit()
        db.refresh(cust)
    elif body.nickname and not cust.nickname:
        cust.nickname = body.nickname
        db.commit()
    token = create_token(str(cust.id), {"type": "customer"})
    return {"token": token, "customer": {"id": cust.id, "nickname": cust.nickname}}


@router.get("/menu")
def menu(db: Session = Depends(get_db)):
    """菜单 + 门店信息 + 桌台场景解析。"""
    shop = db.query(models.Shop).first()
    cats = (
        db.query(models.Category)
        .filter(models.Category.status == "active")
        .order_by(models.Category.sort)
        .all()
    )
    dishes = (
        db.query(models.Dish)
        .filter(models.Dish.status == models.DISH_ON)
        .all()
    )
    dish_by_cat = {}
    for cat in cats:
        dish_by_cat[cat.id] = []
    for d in dishes:
        dish_by_cat.setdefault(d.category_id, []).append(d)
    out_cats = []
    for cat in cats:
        lst = dish_by_cat.get(cat.id, [])
        if not lst:
            continue
        out_cats.append({
            "id": cat.id,
            "name": cat.name,
            "dishes": [{
                "id": d.id,
                "category_id": d.category_id,
                "name": d.name,
                "price": float(d.price),
                "image": d.image or "",
                "emoji": d.emoji or "",
                "description": d.description,
                "sales": d.sales,
                "status": d.status,
                "option_template": d.option_template or {},
            } for d in lst],
        })
    return {
        "shop": {
            "name": shop.name if shop else "",
            "notice": shop.notice if shop else "",
            "business_hours": shop.business_hours if shop else "",
        },
        "categories": out_cats,
    }


@router.get("/tables/by-scene/{scene}")
def table_by_scene(scene: str, db: Session = Depends(get_db)):
    table = (
        db.query(models.DiningTable)
        .filter((models.DiningTable.qr_scene == scene) | (models.DiningTable.name == scene))
        .first()
    )
    if table is None:
        raise HTTPException(404, "桌台码无效")
    if not table.enabled:
        raise HTTPException(400, "该桌台已停用，请联系服务员")
    return {"id": table.id, "name": table.name, "area": table.area, "seats": table.seats}


@router.get("/tables")
def tables(db: Session = Depends(get_db)):
    """桌台列表（扫码页开发调试用：未扫到码时手动选桌）。"""
    rows = (
        db.query(models.DiningTable)
        .filter(models.DiningTable.enabled.is_(True))
        .order_by(models.DiningTable.name)
        .all()
    )
    return [{"id": t.id, "name": t.name, "area": t.area, "seats": t.seats} for t in rows]


@router.post("/orders")
def create_order(
    body: schemas.OrderCreateIn,
    db: Session = Depends(get_db),
    cust=Depends(get_customer_optional),
):
    order = osv.create_order(
        db, body.table_id,
        cust.id if cust else None,
        body.items, body.remark, body.people, body.utensils,
    )
    return osv.customer_progress(db, order)


@router.get("/orders/active")
def active_order(table_id: int, db: Session = Depends(get_db)):
    """该桌当前进行中的订单（补单/进度页用）。"""
    order = (
        db.query(models.Order)
        .filter(
            models.Order.table_id == table_id,
            models.Order.status.in_([models.ORDER_PENDING, models.ORDER_CONFIRMED, models.ORDER_SERVED]),
        )
        .order_by(models.Order.id.desc())
        .first()
    )
    if order is None:
        raise HTTPException(404, "该桌当前没有进行中的订单")
    return osv.customer_progress(db, order)


@router.get("/orders/{order_id}")
def order_progress(order_id: int, db: Session = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    return osv.customer_progress(db, order)


@router.post("/orders/{order_id}/add")
def add_items(order_id: int, body: schemas.AddItemsIn, db: Session = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.add_items(db, order, body.items)
    return osv.customer_progress(db, order)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: schemas.CancelIn, db: Session = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    osv.cancel_order(db, order, body.reason)
    return osv.customer_progress(db, order)
