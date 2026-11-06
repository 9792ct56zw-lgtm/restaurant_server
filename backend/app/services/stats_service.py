"""统计域服务：工作台指标、营业额趋势、菜品排行、标准 vs 实际损耗对比。"""
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from .. import models
from .order_service import order_dict


def d(v):
    return float(v) if isinstance(v, Decimal) else v


def _revenue_of(o) -> Decimal:
    """营业额口径：免单单按应收金额计入营业额；其余按实收（有优惠/抹零时与总额不同）。"""
    if o.pay_method == "free":
        return o.receivable
    return o.paid_amount if o.paid_amount is not None else o.receivable


def _free_of(o) -> Decimal:
    """免单金额：仅免单订单，按应收金额计。"""
    return o.receivable if o.pay_method == "free" else Decimal("0")


def _day_start(offset_days: int = 0) -> datetime:
    now = datetime.now()
    base = datetime(now.year, now.month, now.day) - timedelta(days=offset_days)
    return base


def dashboard(db: Session) -> dict:
    today_start = _day_start(0)
    yesterday_start = _day_start(1)

    def revenue_between(start, end):
        rows = (
            db.query(models.Order)
            .filter(models.Order.pay_status == models.PAY_PAID,
                    models.Order.paid_at >= start,
                    models.Order.paid_at < end)
            .all()
        )
        revenue = sum((_revenue_of(o) for o in rows), Decimal("0"))
        free = sum((_free_of(o) for o in rows), Decimal("0"))
        return revenue, free

    today_paid, today_free = revenue_between(today_start, today_start + timedelta(days=1))
    yesterday_paid, _ = revenue_between(yesterday_start, today_start)

    today_orders = (
        db.query(models.Order)
        .filter(models.Order.created_at >= today_start)
        .all()
    )
    pending_confirm = [o for o in today_orders if o.status == models.ORDER_PENDING]
    active = [o for o in today_orders
              if o.status in (models.ORDER_CONFIRMED, models.ORDER_SERVED)]
    pending_amount = sum((o.receivable for o in pending_confirm + active), Decimal("0"))

    week_start = today_start - timedelta(days=6)
    week_paid_rows = (
        db.query(models.Order)
        .filter(models.Order.pay_status == models.PAY_PAID,
                models.Order.paid_at >= week_start)
        .all()
    )
    avg_price = (
        sum((_revenue_of(o) for o in week_paid_rows), Decimal("0")) / len(week_paid_rows)
        if week_paid_rows else Decimal("0")
    )

    tables = db.query(models.DiningTable).filter_by(enabled=True).all()
    occupied = set(o.table_id for o in today_orders
                   if o.status in (models.ORDER_PENDING, models.ORDER_CONFIRMED, models.ORDER_SERVED))
    dirty_count = sum(1 for t in tables if t.dirty)

    recent = (
        db.query(models.Order)
        .order_by(models.Order.id.desc())
        .limit(6)
        .all()
    )

    delta = d(today_paid - yesterday_paid)
    delta_pct = (delta / d(yesterday_paid) * 100) if yesterday_paid else 0
    return {
        "today_revenue": d(today_paid),
        "today_free": d(today_free),        # 免单金额（按应收计入营业额，单独列示）
        "today_revenue_delta_pct": round(delta_pct, 1),
        "today_orders": len(today_orders),
        "pending_confirm_count": len(pending_confirm),
        "pending_amount": d(pending_amount),
        "avg_price_7d": round(d(avg_price), 1),
        "table_total": len(tables),
        "table_occupied": len(occupied),
        "table_dirty": dirty_count,
        "recent_orders": [order_dict(db, o, include_items=False) for o in recent],
    }


def revenue_trend(db: Session, days: int = 7) -> list:
    out = []
    for i in range(days - 1, -1, -1):
        start = _day_start(i)
        end = start + timedelta(days=1)
        rows = (
            db.query(models.Order)
            .filter(models.Order.pay_status == models.PAY_PAID,
                    models.Order.paid_at >= start,
                    models.Order.paid_at < end)
            .all()
        )
        out.append({
            "date": start.strftime("%m-%d"),
            "revenue": d(sum((_revenue_of(o) for o in rows), Decimal("0"))),
            "free": d(sum((_free_of(o) for o in rows), Decimal("0"))),
            "orders": len(rows),
        })
    return out


def top_dishes(db: Session, days: int = 30) -> list:
    start = _day_start(days - 1)
    rows = (
        db.query(models.OrderItem)
        .join(models.Order, models.OrderItem.order_id == models.Order.id)
        .filter(models.Order.status != models.ORDER_CANCELLED,
                models.Order.created_at >= start)
        .all()
    )
    agg = {}
    for it in rows:
        key = (it.dish_id, it.name, it.price)
        cur = agg.setdefault(key, {"qty": 0, "amount": Decimal("0")})
        cur["qty"] += it.qty
        cur["amount"] += it.price * it.qty
    top = sorted(agg.items(), key=lambda kv: -kv[1]["qty"])[:10]
    return [
        {"dish_id": k[0], "name": k[1], "price": d(k[2]),
         "qty": v["qty"], "amount": d(v["amount"])}
        for k, v in top
    ]


def loss_compare(db: Session, start: datetime = None, end: datetime = None) -> list:
    """标准 vs 实际消耗对比：标准 = Σ(标准用量×出餐份数)，实际 = 出餐领用流水（含损耗）。"""
    items_q = (
        db.query(models.OrderItem)
        .filter(models.OrderItem.status == models.ITEM_OUT)
    )
    logs_q = (
        db.query(models.InventoryLog)
        .filter(models.InventoryLog.type == models.IL_ORDER_USE)
    )
    if start:
        items_q = items_q.filter(models.OrderItem.out_at >= start)
        logs_q = logs_q.filter(models.InventoryLog.created_at >= start)
    if end:
        items_q = items_q.filter(models.OrderItem.out_at < end)
        logs_q = logs_q.filter(models.InventoryLog.created_at < end)

    standard = {}
    for it in items_q.all():
        for r in (
            db.query(models.DishIngredient)
            .filter(models.DishIngredient.dish_id == it.dish_id)
            .all()
        ):
            standard[r.ingredient_id] = standard.get(r.ingredient_id, Decimal("0")) + r.quantity * it.qty

    actual = {}
    for lg in logs_q.all():
        actual[lg.ingredient_id] = actual.get(lg.ingredient_id, Decimal("0")) + abs(lg.quantity)

    out = []
    for ing_id in sorted(set(standard) | set(actual)):
        ing = db.get(models.Ingredient, ing_id)
        if ing is None:
            continue
        std = standard.get(ing_id, Decimal("0"))
        act = actual.get(ing_id, Decimal("0"))
        loss_rate = (act - std) / std * 100 if std else 0
        out.append({
            "ingredient_id": ing_id,
            "name": ing.name,
            "unit": ing.unit,
            "standard": d(std),
            "actual": d(act),
            "loss_rate": round(d(loss_rate), 1),
        })
    out.sort(key=lambda x: -abs(x["loss_rate"]))
    return out
