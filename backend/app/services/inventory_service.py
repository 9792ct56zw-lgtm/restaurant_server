"""供应链域服务：BOM 损耗扣库存、红黄两级预警、一键采购单、入库、盘点/报损。"""
from datetime import datetime
from decimal import Decimal, ROUND_CEILING

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..config import settings


def d(v):
    return float(v) if isinstance(v, Decimal) else v


def _log(db: Session, ingredient_id: int, log_type: str, delta: Decimal,
         balance: Decimal, ref_type: str = "", ref_id: int = 0, remark: str = ""):
    db.add(models.InventoryLog(
        ingredient_id=ingredient_id,
        type=log_type,
        quantity=delta,
        balance=balance,
        ref_type=ref_type,
        ref_id=ref_id,
        remark=remark,
    ))


def deduct_for_item(db: Session, item: models.OrderItem):
    """按 BOM「标准用量 ×(1+损耗率)」扣减原材料库存（逐道出餐时调用）。"""
    rows = (
        db.query(models.DishIngredient)
        .filter(models.DishIngredient.dish_id == item.dish_id)
        .all()
    )
    for r in rows:
        ing = db.get(models.Ingredient, r.ingredient_id)
        if ing is None:
            continue
        per = r.quantity * (Decimal("1") + r.loss_rate / Decimal("100"))
        use = (per * item.qty).quantize(Decimal("0.001"), rounding=ROUND_CEILING)
        balance = ing.stock - use
        ing.stock = balance
        _log(db, ing.id, models.IL_ORDER_USE, -use, balance,
             ref_type="order_item", ref_id=item.id,
             remark="出餐领用 %s ×%s（含损耗 %.0f%%）" % (item.name, item.qty, r.loss_rate))


def ingredient_dict(ing: models.Ingredient, level: str = "") -> dict:
    out = {
        "id": ing.id, "name": ing.name, "unit": ing.unit,
        "stock": d(ing.stock), "safe_stock": d(ing.safe_stock),
        "ref_price": d(ing.ref_price), "status": ing.status,
    }
    if level:
        out["level"] = level
    return out


def warnings(db: Session) -> list:
    """库存红黄两级预警：≤安全线红色，≤1.3 倍黄色。"""
    rows = (
        db.query(models.Ingredient)
        .filter(models.Ingredient.status == "active",
                models.Ingredient.safe_stock > 0)
        .all()
    )
    out = []
    for ing in rows:
        if ing.stock <= ing.safe_stock:
            level = "red"
            suggest = ing.safe_stock * Decimal("1.5") - ing.stock
        elif ing.stock <= ing.safe_stock * Decimal(str(settings.warn_yellow_ratio)):
            level = "yellow"
            suggest = ing.safe_stock * Decimal("1.5") - ing.stock
        else:
            continue
        suggest = max(suggest, Decimal("0")).quantize(Decimal("0.001"), rounding=ROUND_CEILING)
        it = ingredient_dict(ing, level)
        it["suggest_qty"] = d(suggest)
        out.append(it)
    out.sort(key=lambda x: 0 if x["level"] == "red" else 1)
    return out


def gen_po_no(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    cnt = (
        db.query(models.PurchaseOrder)
        .filter(models.PurchaseOrder.order_no.like("PO" + today + "-%"))
        .count()
    )
    return "PO%s-%04d" % (today, cnt + 1)


def po_dict(db: Session, po: models.PurchaseOrder) -> dict:
    supplier = db.get(models.Supplier, po.supplier_id) if po.supplier_id else None
    items = (
        db.query(models.PurchaseItem)
        .filter(models.PurchaseItem.purchase_order_id == po.id)
        .all()
    )
    item_list = []
    for it in items:
        ing = db.get(models.Ingredient, it.ingredient_id)
        item_list.append({
            "id": it.id,
            "ingredient_id": it.ingredient_id,
            "ingredient_name": ing.name if ing else "",
            "unit": ing.unit if ing else "",
            "qty": d(it.qty),
            "unit_price": d(it.unit_price),
            "amount": d(it.amount),
        })
    return {
        "id": po.id,
        "order_no": po.order_no,
        "supplier_id": po.supplier_id,
        "supplier_name": supplier.name if supplier else "",
        "status": po.status,
        "total": d(po.total),
        "remark": po.remark,
        "created_at": po.created_at.strftime("%m-%d %H:%M"),
        "items": item_list,
    }


def generate_purchase_from_warnings(db: Session, supplier_id: int = None,
                                    remark: str = "") -> models.PurchaseOrder:
    """一键生成采购单：把红黄预警的原料按建议采购量汇总（单价取参考价）。"""
    ws = warnings(db)
    if not ws:
        raise HTTPException(400, "当前没有需要补货的原料")
    po = models.PurchaseOrder(
        order_no=gen_po_no(db),
        supplier_id=supplier_id,
        remark=remark or "由库存预警一键生成",
    )
    db.add(po)
    db.flush()
    total = Decimal("0")
    for w in ws:
        qty = Decimal(str(w["suggest_qty"])).quantize(Decimal("0.001"), rounding=ROUND_CEILING)
        ing = db.get(models.Ingredient, w["id"])
        price = ing.ref_price if ing else Decimal("0")
        amount = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        total += amount
        db.add(models.PurchaseItem(
            purchase_order_id=po.id,
            ingredient_id=w["id"],
            qty=qty,
            unit_price=price,
            amount=amount,
        ))
    po.total = total
    db.commit()
    db.refresh(po)
    return po


def inbound_purchase(db: Session, po: models.PurchaseOrder) -> models.PurchaseOrder:
    """采购入库：逐项入库存并写流水，整单状态转已入库（幂等）。"""
    if po.status == models.PO_INBOUND:
        return po
    items = (
        db.query(models.PurchaseItem)
        .filter(models.PurchaseItem.purchase_order_id == po.id)
        .all()
    )
    for it in items:
        ing = db.get(models.Ingredient, it.ingredient_id)
        if ing is None:
            continue
        balance = ing.stock + it.qty
        ing.stock = balance
        _log(db, ing.id, models.IL_PURCHASE_IN, it.qty, balance,
             ref_type="purchase", ref_id=po.id,
             remark="采购入库 %s" % po.order_no)
    po.status = models.PO_INBOUND
    db.commit()
    return po


def adjust_stock(db: Session, ingredient_id: int, log_type: str, delta: Decimal,
                 remark: str = "") -> models.Ingredient:
    """盘点调整 / 报损：delta 为正入、负出；报损须为负。"""
    if log_type not in (models.IL_STOCKTAKE, models.IL_WASTE):
        raise HTTPException(400, "变动类型无效")
    if log_type == models.IL_WASTE and delta > 0:
        raise HTTPException(400, "报损数量应为负值")
    ing = db.get(models.Ingredient, ingredient_id)
    if ing is None:
        raise HTTPException(404, "原料不存在")
    balance = ing.stock + delta
    ing.stock = balance
    _log(db, ing.id, log_type, delta, balance, remark=remark)
    db.commit()
    db.refresh(ing)
    return ing


def bom_set(db: Session, dish_id: int, items) -> models.Dish:
    """整份替换菜品 BOM（标准用量 + 损耗率）。"""
    dish = db.get(models.Dish, dish_id)
    if dish is None:
        raise HTTPException(404, "菜品不存在")
    db.query(models.DishIngredient).filter_by(dish_id=dish_id).delete()
    for it in items:
        if it.ingredient_id is None:
            continue
        ing = db.get(models.Ingredient, it.ingredient_id)
        if ing is None:
            raise HTTPException(400, "原料不存在")
        if it.quantity < 0 or it.loss_rate < 0:
            raise HTTPException(400, "用量与损耗率不能为负")
        db.add(models.DishIngredient(
            dish_id=dish_id,
            ingredient_id=it.ingredient_id,
            quantity=it.quantity,
            loss_rate=it.loss_rate,
        ))
    db.commit()
    return dish


def bom_get(db: Session, dish_id: int) -> list:
    rows = (
        db.query(models.DishIngredient)
        .filter(models.DishIngredient.dish_id == dish_id)
        .all()
    )
    out = []
    for r in rows:
        ing = db.get(models.Ingredient, r.ingredient_id)
        out.append({
            "id": r.id,
            "ingredient_id": r.ingredient_id,
            "ingredient_name": ing.name if ing else "",
            "unit": ing.unit if ing else "",
            "quantity": d(r.quantity),
            "loss_rate": d(r.loss_rate),
        })
    return out
