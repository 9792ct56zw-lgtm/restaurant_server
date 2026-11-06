"""后台管理接口：工作台、订单收银、菜品、桌台、供应商、原料、采购、统计、员工、门店。"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import require_roles
from ..security import hash_password
from ..services import inventory_service as inv
from ..services import stats_service as stats
from ..services import order_service as osv
from ..services import day_service as days

logger = logging.getLogger("app.oss")

router = APIRouter(prefix="/api/admin", tags=["admin"])

admin_only = require_roles(models.ROLE_ADMIN)
admin_cashier = require_roles(models.ROLE_ADMIN, models.ROLE_CASHIER)


def _get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, "记录不存在")
    return obj


# ---------------- 工作台 ----------------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), staff=Depends(admin_cashier)):
    return stats.dashboard(db)


# ---------------- 订单与收银 ----------------
@router.get("/orders")
def orders(
    status: str = "",
    table_id: int = 0,
    date: str = "",
    db: Session = Depends(get_db),
    staff=Depends(admin_cashier),
):
    q = db.query(models.Order)
    if status:
        q = q.filter(models.Order.status == status)
    if table_id:
        q = q.filter(models.Order.table_id == table_id)
    if date:
        q = q.filter(models.Order.order_no.like(date.replace("-", "") + "-%"))
    rows = q.order_by(models.Order.id.desc()).limit(200).all()
    return [osv.order_dict(db, o) for o in rows]


@router.get("/orders/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db), staff=Depends(admin_cashier)):
    return osv.order_dict(db, _get_or_404(db, models.Order, order_id))


@router.post("/orders/{order_id}/settle")
def settle(order_id: int, body: schemas.SettleIn, db: Session = Depends(get_db),
           staff=Depends(admin_cashier)):
    order = _get_or_404(db, models.Order, order_id)
    osv.settle_order(db, order, body.method, staff, body.discount, body.paid_amount,
                     body.settle_note or "")
    return osv.order_dict(db, order)


@router.post("/orders/{order_id}/cancel")
def cancel(order_id: int, body: schemas.CancelIn, db: Session = Depends(get_db),
           staff=Depends(admin_only)):
    order = _get_or_404(db, models.Order, order_id)
    osv.cancel_order(db, order, body.reason)
    return osv.order_dict(db, order)


# ---------------- 分类 ----------------
@router.get("/categories")
def categories(db: Session = Depends(get_db), staff=Depends(admin_only)):
    return [
        {"id": c.id, "name": c.name, "sort": c.sort, "status": c.status}
        for c in db.query(models.Category).order_by(models.Category.sort).all()
    ]


@router.post("/categories")
def create_category(body: schemas.CategoryIn, db: Session = Depends(get_db),
                    staff=Depends(admin_only)):
    cat = models.Category(name=body.name, sort=body.sort)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return {"id": cat.id, "name": cat.name, "sort": cat.sort}


@router.put("/categories/{cat_id}")
def update_category(cat_id: int, body: schemas.CategoryIn, db: Session = Depends(get_db),
                    staff=Depends(admin_only)):
    cat = _get_or_404(db, models.Category, cat_id)
    cat.name = body.name
    cat.sort = body.sort
    db.commit()
    return {"id": cat.id, "name": cat.name, "sort": cat.sort}


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    cat = _get_or_404(db, models.Category, cat_id)
    n = db.query(models.Dish).filter_by(category_id=cat_id).count()
    if n:
        raise HTTPException(400, "该分类下还有 %s 道菜品，请先移出" % n)
    db.delete(cat)
    db.commit()
    return {"ok": True}


# ---------------- 菜品 ----------------
def dish_dict(d: models.Dish) -> dict:
    return {
        "id": d.id, "category_id": d.category_id, "name": d.name,
        "price": float(d.price), "image": d.image or "", "emoji": d.emoji or "",
        "description": d.description, "sales": d.sales,
        "option_template": d.option_template or {},
        "status": d.status,
        "bom_count": 0,
    }


@router.get("/dishes")
def dishes(db: Session = Depends(get_db), staff=Depends(admin_only)):
    rows = db.query(models.Dish).order_by(models.Dish.id).all()
    out = []
    for d in rows:
        it = dish_dict(d)
        it["bom_count"] = (
            db.query(models.DishIngredient).filter_by(dish_id=d.id).count()
        )
        out.append(it)
    return out


@router.post("/dishes")
def create_dish(body: schemas.DishIn, db: Session = Depends(get_db),
                staff=Depends(admin_only)):
    _get_or_404(db, models.Category, body.category_id)
    dish = models.Dish(
        category_id=body.category_id, name=body.name, price=body.price,
        image=body.image, emoji=body.emoji, description=body.description,
        option_template=body.option_template,
        status=body.status if body.status in (models.DISH_ON, models.DISH_OFF, models.DISH_SOLDOUT) else models.DISH_ON,
    )
    db.add(dish)
    db.commit()
    db.refresh(dish)
    return dish_dict(dish)


@router.put("/dishes/{dish_id}")
def update_dish(dish_id: int, body: schemas.DishIn, db: Session = Depends(get_db),
                staff=Depends(admin_only)):
    dish = _get_or_404(db, models.Dish, dish_id)
    _get_or_404(db, models.Category, body.category_id)
    dish.category_id = body.category_id
    dish.name = body.name
    dish.price = body.price
    dish.image = body.image
    dish.emoji = body.emoji
    dish.description = body.description
    dish.option_template = body.option_template
    db.commit()
    return dish_dict(dish)


@router.post("/dishes/{dish_id}/status")
def dish_status(dish_id: int, body: schemas.DishStatusIn, db: Session = Depends(get_db),
                staff=Depends(admin_only)):
    if body.status not in (models.DISH_ON, models.DISH_OFF, models.DISH_SOLDOUT):
        raise HTTPException(400, "状态无效")
    dish = _get_or_404(db, models.Dish, dish_id)
    dish.status = body.status
    db.commit()
    return dish_dict(dish)


@router.delete("/dishes/{dish_id}")
def delete_dish(dish_id: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    dish = _get_or_404(db, models.Dish, dish_id)
    dish.status = models.DISH_OFF
    db.commit()
    return {"ok": True, "status": dish.status}


# ---------------- 图片上传（阿里云 OSS 对象存储） ----------------
@router.post("/upload/image")
async def upload_image(file: UploadFile = File(...), staff=Depends(admin_only)):
    """上传菜品图片到 OSS（对象级 public-read），返回公开访问 URL。

    供后台菜品表单「上传」按钮使用；OSS 未配置或 SDK 未装时返回 503，不影响其余功能。
    """
    from ..services import oss_service  # 延迟导入：未装 oss2 时应用照常启动

    data = await file.read()
    try:
        url = oss_service.upload_dish_image(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # 网络/签名等 OSS 侧错误
        logger.exception("OSS 上传失败 filename=%s bytes=%d", file.filename, len(data))
        raise HTTPException(503, "OSS 上传失败，请稍后重试（%s）" % type(e).__name__)
    logger.info("菜品图片已上传 OSS %s（%d 字节，员工 %s）", url, len(data),
                getattr(staff, "name", "?"))
    return {"url": url}


@router.get("/dishes/{dish_id}/bom")
def get_bom(dish_id: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    _get_or_404(db, models.Dish, dish_id)
    return inv.bom_get(db, dish_id)


@router.put("/dishes/{dish_id}/bom")
def set_bom(dish_id: int, body: schemas.BomIn, db: Session = Depends(get_db),
            staff=Depends(admin_only)):
    inv.bom_set(db, dish_id, body.items)
    return inv.bom_get(db, dish_id)


# ---------------- 桌台 ----------------
@router.get("/tables")
def tables(db: Session = Depends(get_db), staff=Depends(admin_only)):
    # 过滤 id=0 外带占位行
    rows = db.query(models.DiningTable).filter(models.DiningTable.id != 0).order_by(models.DiningTable.name).all()
    return [
        {"id": t.id, "name": t.name, "area": t.area, "seats": t.seats,
         "qr_scene": t.qr_scene, "enabled": t.enabled, "dirty": t.dirty}
        for t in rows
    ]


@router.post("/tables")
def create_table(body: schemas.TableIn, db: Session = Depends(get_db),
                 staff=Depends(admin_only)):
    if db.query(models.DiningTable).filter_by(name=body.name).first():
        raise HTTPException(400, "桌号已存在")
    table = models.DiningTable(
        name=body.name, area=body.area, seats=body.seats,
        enabled=body.enabled, qr_scene=body.name,
    )
    db.add(table)
    db.commit()
    db.refresh(table)
    return {"id": table.id, "name": table.name}


@router.put("/tables/{table_id}")
def update_table(table_id: int, body: schemas.TableUpdateIn,
                 db: Session = Depends(get_db), staff=Depends(admin_only)):
    table = _get_or_404(db, models.DiningTable, table_id)
    if body.name and body.name != table.name:
        if db.query(models.DiningTable).filter_by(name=body.name).first():
            raise HTTPException(400, "桌号已存在")
        table.name = body.name
        table.qr_scene = body.name
    if body.area is not None:
        table.area = body.area
    if body.seats is not None:
        table.seats = body.seats
    if body.enabled is not None:
        table.enabled = body.enabled
    db.commit()
    return {"id": table.id, "name": table.name, "enabled": table.enabled}


@router.delete("/tables/{table_id}")
def delete_table(table_id: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    table = _get_or_404(db, models.DiningTable, table_id)
    active = (
        db.query(models.Order)
        .filter(models.Order.table_id == table_id,
                models.Order.status.in_([models.ORDER_PENDING, models.ORDER_CONFIRMED, models.ORDER_SERVED]))
        .count()
    )
    if active:
        raise HTTPException(400, "该桌有进行中的订单，不能删除（可先停用）")
    db.delete(table)
    db.commit()
    return {"ok": True}


# ---------------- 供应商 ----------------
@router.get("/suppliers")
def suppliers(db: Session = Depends(get_db), staff=Depends(admin_only)):
    return [
        {"id": s.id, "name": s.name, "contact": s.contact, "phone": s.phone, "status": s.status}
        for s in db.query(models.Supplier).order_by(models.Supplier.id).all()
    ]


@router.post("/suppliers")
def create_supplier(body: schemas.SupplierIn, db: Session = Depends(get_db),
                    staff=Depends(admin_only)):
    s = models.Supplier(name=body.name, contact=body.contact, phone=body.phone)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "name": s.name}


@router.put("/suppliers/{sid}")
def update_supplier(sid: int, body: schemas.SupplierIn, db: Session = Depends(get_db),
                    staff=Depends(admin_only)):
    s = _get_or_404(db, models.Supplier, sid)
    s.name = body.name
    s.contact = body.contact
    s.phone = body.phone
    s.status = body.status
    db.commit()
    return {"id": s.id, "name": s.name}


@router.delete("/suppliers/{sid}")
def delete_supplier(sid: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    s = _get_or_404(db, models.Supplier, sid)
    s.status = "inactive"
    db.commit()
    return {"ok": True}


# ---------------- 原料与库存 ----------------
@router.get("/ingredients")
def ingredients(db: Session = Depends(get_db), staff=Depends(admin_only)):
    rows = db.query(models.Ingredient).order_by(models.Ingredient.id).all()
    return [inv.ingredient_dict(i) for i in rows]


@router.post("/ingredients")
def create_ingredient(body: schemas.IngredientIn, db: Session = Depends(get_db),
                      staff=Depends(admin_only)):
    ing = models.Ingredient(
        name=body.name, unit=body.unit, stock=body.stock,
        safe_stock=body.safe_stock, ref_price=body.ref_price,
    )
    db.add(ing)
    db.commit()
    db.refresh(ing)
    return inv.ingredient_dict(ing)


@router.put("/ingredients/{ing_id}")
def update_ingredient(ing_id: int, body: schemas.IngredientIn,
                      db: Session = Depends(get_db), staff=Depends(admin_only)):
    ing = _get_or_404(db, models.Ingredient, ing_id)
    ing.name = body.name
    ing.unit = body.unit
    ing.safe_stock = body.safe_stock
    ing.ref_price = body.ref_price
    # stock 直接改动走调整接口；此处仅在新建/纠错时允许覆盖
    if body.stock != ing.stock:
        delta = body.stock - ing.stock
        ing.stock = body.stock
        inv._log(db, ing.id, models.IL_STOCKTAKE, delta, body.stock, remark="后台直接修改库存")
    db.commit()
    return inv.ingredient_dict(ing)


@router.delete("/ingredients/{ing_id}")
def delete_ingredient(ing_id: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    ing = _get_or_404(db, models.Ingredient, ing_id)
    ing.status = "inactive"
    db.commit()
    return {"ok": True}


@router.post("/ingredients/{ing_id}/adjust")
def adjust_ingredient(ing_id: int, body: schemas.IngredientAdjustIn,
                      db: Session = Depends(get_db), staff=Depends(admin_only)):
    if body.type not in (models.IL_STOCKTAKE, models.IL_WASTE, models.IL_PURCHASE_IN):
        raise HTTPException(400, "变动类型无效")
    ing = _get_or_404(db, models.Ingredient, ing_id)
    if body.type == models.IL_WASTE and body.delta > 0:
        raise HTTPException(400, "报损数量应为负值")
    balance = ing.stock + body.delta
    ing.stock = balance
    inv._log(db, ing.id, body.type, body.delta, balance, remark=body.remark)
    db.commit()
    return inv.ingredient_dict(ing)


@router.get("/inventory/warnings")
def inventory_warnings(db: Session = Depends(get_db), staff=Depends(admin_only)):
    return inv.warnings(db)


@router.get("/inventory/logs")
def inventory_logs(ingredient_id: int = 0, limit: int = Query(100, le=500),
                   db: Session = Depends(get_db), staff=Depends(admin_only)):
    q = db.query(models.InventoryLog)
    if ingredient_id:
        q = q.filter(models.InventoryLog.ingredient_id == ingredient_id)
    rows = q.order_by(models.InventoryLog.id.desc()).limit(limit).all()
    out = []
    for lg in rows:
        ing = db.get(models.Ingredient, lg.ingredient_id)
        out.append({
            "id": lg.id, "ingredient_id": lg.ingredient_id,
            "ingredient_name": ing.name if ing else "",
            "type": lg.type, "quantity": float(lg.quantity),
            "balance": float(lg.balance), "remark": lg.remark,
            "created_at": lg.created_at.strftime("%m-%d %H:%M:%S"),
        })
    return out


# ---------------- 采购单 ----------------
@router.get("/purchases")
def purchases(db: Session = Depends(get_db), staff=Depends(admin_only)):
    rows = db.query(models.PurchaseOrder).order_by(models.PurchaseOrder.id.desc()).limit(100).all()
    return [inv.po_dict(db, po) for po in rows]


@router.post("/purchases")
def create_purchase(body: schemas.PurchaseCreateIn, db: Session = Depends(get_db),
                    staff=Depends(admin_only)):
    _get_or_404(db, models.Supplier, body.supplier_id)
    po = models.PurchaseOrder(
        order_no=inv.gen_po_no(db), supplier_id=body.supplier_id, remark=body.remark,
    )
    db.add(po)
    db.flush()
    total = Decimal("0")
    for it in body.items:
        _get_or_404(db, models.Ingredient, it.ingredient_id)
        amount = (it.qty * it.unit_price).quantize(Decimal("0.01"))
        total += amount
        db.add(models.PurchaseItem(
            purchase_order_id=po.id, ingredient_id=it.ingredient_id,
            qty=it.qty, unit_price=it.unit_price, amount=amount,
        ))
    po.total = total
    db.commit()
    db.refresh(po)
    return inv.po_dict(db, po)


@router.post("/purchases/generate-from-warnings")
def generate_purchase(supplier_id: int = 0, remark: str = "",
                      db: Session = Depends(get_db), staff=Depends(admin_only)):
    if supplier_id:
        _get_or_404(db, models.Supplier, supplier_id)
    po = inv.generate_purchase_from_warnings(db, supplier_id or None, remark)
    return inv.po_dict(db, po)


@router.post("/purchases/{po_id}/inbound")
def inbound(po_id: int, db: Session = Depends(get_db), staff=Depends(admin_only)):
    po = _get_or_404(db, models.PurchaseOrder, po_id)
    inv.inbound_purchase(db, po)
    return inv.po_dict(db, po)


# ---------------- 统计报表 ----------------
@router.get("/stats/trend")
def revenue_trend(days: int = Query(7, ge=1, le=60), db: Session = Depends(get_db),
                  staff=Depends(admin_cashier)):
    return stats.revenue_trend(db, days)


@router.get("/stats/top-dishes")
def top_dishes(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
               staff=Depends(admin_cashier)):
    return stats.top_dishes(db, days)


@router.get("/stats/loss-compare")
def loss_compare(db: Session = Depends(get_db), staff=Depends(admin_cashier)):
    return stats.loss_compare(db)


# ---------------- 员工 ----------------
@router.get("/staff")
def staff_list(db: Session = Depends(get_db), staff=Depends(admin_only)):
    return [
        {"id": s.id, "username": s.username, "name": s.name,
         "role": s.role, "status": s.status}
        for s in db.query(models.Staff).order_by(models.Staff.id).all()
    ]


@router.post("/staff")
def create_staff(body: schemas.StaffCreateIn, db: Session = Depends(get_db),
                 staff=Depends(admin_only)):
    if body.role not in models.ROLES:
        raise HTTPException(400, "角色无效")
    if db.query(models.Staff).filter_by(username=body.username).first():
        raise HTTPException(400, "账号已存在")
    s = models.Staff(
        username=body.username, password_hash=hash_password(body.password),
        name=body.name, role=body.role,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "username": s.username, "name": s.name, "role": s.role}


@router.put("/staff/{staff_id}")
def update_staff(staff_id: int, body: schemas.StaffUpdateIn,
                 db: Session = Depends(get_db), staff=Depends(admin_only)):
    s = _get_or_404(db, models.Staff, staff_id)
    if s.id == staff.id and body.status == "disabled":
        raise HTTPException(400, "不能停用自己")
    if body.name is not None:
        s.name = body.name
    if body.role is not None:
        if body.role not in models.ROLES:
            raise HTTPException(400, "角色无效")
        s.role = body.role
    if body.status is not None:
        s.status = body.status
    db.commit()
    return {"id": s.id, "username": s.username, "name": s.name,
            "role": s.role, "status": s.status}


@router.post("/staff/{staff_id}/reset-password")
def reset_password(staff_id: int, body: schemas.ResetPasswordIn,
                   db: Session = Depends(get_db), staff=Depends(admin_only)):
    s = _get_or_404(db, models.Staff, staff_id)
    s.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True}


# ---------------- 门店 ----------------
@router.get("/shop")
def shop(db: Session = Depends(get_db), staff=Depends(admin_only)):
    s = db.query(models.Shop).first()
    if s is None:
        s = models.Shop()
        db.add(s)
        db.commit()
        db.refresh(s)
    return {"id": s.id, "name": s.name, "phone": s.phone, "address": s.address,
            "business_hours": s.business_hours, "notice": s.notice}


@router.put("/shop")
def update_shop(body: schemas.ShopIn, db: Session = Depends(get_db),
                staff=Depends(admin_only)):
    s = db.query(models.Shop).first()
    if s is None:
        s = models.Shop()
        db.add(s)
    for field in ("name", "phone", "address", "business_hours", "notice"):
        val = getattr(body, field)
        if val is not None:
            setattr(s, field, val)
    db.commit()
    return {"ok": True}


# ---------------- 营业管理（开门 / 结单对账） ----------------
@router.get("/day/status")
def day_status(db: Session = Depends(get_db), staff=Depends(admin_cashier)):
    return days.day_status(db)


@router.post("/day/open")
def day_open(body: schemas.DayOpenIn, db: Session = Depends(get_db),
             staff=Depends(admin_only)):
    return days.open_shop(db, body.change, body.city)


@router.post("/day/close")
def day_close(body: schemas.DayCloseIn, db: Session = Depends(get_db),
              staff=Depends(admin_only)):
    return days.close_shop(db, body.cash_box, body.note)
