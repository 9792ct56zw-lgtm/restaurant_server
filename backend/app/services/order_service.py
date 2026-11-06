"""订单域核心服务：状态机、KDS 三栏派生（出餐中）、逐道出餐、补单、催单、结账。"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from .. import schemas
from ..config import settings
from ..ws_hub import emit_sync
from .inventory_service import deduct_for_item

logger = logging.getLogger("app.order")

STATUS_TEXT = {
    models.ORDER_PENDING: "待确认",
    models.ORDER_CONFIRMED: "已确认",
    models.ORDER_SERVED: "已出餐",
    models.ORDER_PAID: "已结账",
    models.ORDER_CANCELLED: "已取消",
}


def d(v):
    return float(v) if isinstance(v, Decimal) else v


# 餐具费：堂食按人数每人 1 元；外带不按人数，按打包盒数量每盒 1 元
UTENSIL_FEE_PER_PERSON = Decimal("1.00")
UTENSIL_FEE_PER_BOX = Decimal("1.00")


def utensil_fee(people: int) -> Decimal:
    return Decimal(int(people)) * UTENSIL_FEE_PER_PERSON


def takeout_fee(boxes: int) -> Decimal:
    """外带打包盒费：盒数 × 1 元。"""
    return Decimal(int(boxes)) * UTENSIL_FEE_PER_BOX


def emit(channel: str, data: dict):
    emit_sync(channel, data)


def gen_order_no(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    cnt = (
        db.query(models.Order)
        .filter(models.Order.order_no.like(today + "-%"))
        .count()
    )
    return "%s-%06d" % (today, cnt + 1)


def _validate_table(db: Session, table_id: int) -> models.DiningTable:
    table = db.get(models.DiningTable, table_id)
    if table is None:
        raise HTTPException(400, "桌台不存在")
    if not table.enabled:
        raise HTTPException(400, "该桌台已停用")
    # 待清台限制在 create_order 内处理：顾客下单拒绝，服务员代客下单自动解除待清台
    return table


def _snapshot_items(db: Session, items) -> list:
    """校验菜品并生成明细快照。"""
    out = []
    for it in items:
        dish = db.get(models.Dish, it.dish_id)
        if dish is None:
            raise HTTPException(400, "菜品不存在")
        if dish.status != models.DISH_ON:
            raise HTTPException(400, "「%s」已下架或售罄" % dish.name)
        options = dict(it.options) if it.options else {}
        tpl = dish.option_template or {}
        for k, v in options.items():
            if k in tpl and tpl[k] and v not in tpl[k]:
                raise HTTPException(400, "「%s」选项 %s 无效" % (dish.name, v))
        out.append({
            "dish": dish,
            "qty": it.qty,
            "options": options,
        })
    return out


def item_dict(item: models.OrderItem) -> dict:
    return {
        "id": item.id,
        "dish_id": item.dish_id,
        "name": item.name,
        "price": d(item.price),
        "qty": item.qty,
        "options": item.options or {},
        "is_add": item.is_add,
        "status": item.status,
        "out_at": item.out_at.strftime("%H:%M:%S") if item.out_at else None,
        "created_at": item.created_at.strftime("%H:%M") if item.created_at else None,
    }


def order_dict(db: Session, order: models.Order, include_items: bool = True) -> dict:
    table = db.get(models.DiningTable, order.table_id)
    takeout = bool(order.is_takeout)
    items = (
        db.query(models.OrderItem)
        .filter(models.OrderItem.order_id == order.id)
        .order_by(models.OrderItem.sort, models.OrderItem.id)
        .all()
        if include_items
        else []
    )
    return {
        "id": order.id,
        "order_no": order.order_no,
        "table_id": order.table_id,
        "table_name": "外带" if takeout else (table.name if table else ""),
        "is_takeout": takeout,
        "status": order.status,
        "status_text": STATUS_TEXT.get(order.status, order.status),
        "total_amount": d(order.total_amount),
        "utensil_fee": d(order.utensil_fee or 0),
        "fee_label": "打包盒费" if takeout else "餐具费",
        "utensil_label": "打包盒" if takeout else "餐具",
        "receivable": d(order.receivable),
        "remark": order.remark,
        "people": order.people,
        "utensils": order.utensils,
        "urged": order.urged,
        "confirm_by": order.confirm_by,
        "confirmed_at": order.confirmed_at.strftime("%m-%d %H:%M") if order.confirmed_at else None,
        "pay_status": order.pay_status,
        "pay_method": order.pay_method,
        "discount": d(order.discount or 0),
        "paid_amount": d(order.paid_amount) if order.paid_amount is not None else None,
        "settle_note": order.settle_note or "",
        "paid_at": order.paid_at.strftime("%H:%M:%S") if order.paid_at else None,
        "cancel_reason": order.cancel_reason,
        "created_at": order.created_at.strftime("%m-%d %H:%M"),
        "items": [item_dict(i) for i in items],
    }


def create_order(
    db: Session,
    table_id: int,
    customer_id: int,
    items,
    remark: str,
    people: int,
    utensils: int,
    source: str = "customer",          # customer / waiter(代客下单)
    staff: models.Staff = None,
    takeout: bool = False,             # 外带：不占桌台、无需扫码，服务员直接下单直达后厨
) -> models.Order:
    table = None
    if takeout:
        if source != "waiter":
            raise HTTPException(400, "外带单仅支持服务员代客下单")
        table_id = 0
        # 外带：不选择人数，只选打包盒数量，盒费 = 盒数 × 1 元
        people = 1
        utensils = min(30, max(0, int(utensils or 0)))
        fee = takeout_fee(utensils)
    else:
        table = _validate_table(db, table_id)
        if table.dirty:
            # 待清台桌台：顾客扫码下单被拒（需联系服务员），仅服务员代客下单可落单并自动解除待清台
            if source != "waiter":
                raise HTTPException(400, "该桌台待清台，请稍候或联系服务员")
            table.dirty = False
        fee = utensil_fee(people)
    snaps = _snapshot_items(db, items)
    if not snaps:
        raise HTTPException(400, "购物车为空")

    order = models.Order(
        order_no=gen_order_no(db),
        table_id=table_id,
        customer_id=customer_id,
        remark=remark,
        people=people,
        utensils=utensils,
        utensil_fee=fee,
        is_takeout=takeout,
    )
    if source == "waiter":
        # 代客下单：提交即已确认，直接推后厨
        order.status = models.ORDER_CONFIRMED
        order.confirm_by = staff.id if staff else None
        order.confirmed_at = datetime.now()
    # 顾客下单 → 待确认

    db.add(order)
    db.flush()  # 拿到 order.id 挂明细
    total = Decimal("0")
    for idx, s in enumerate(snaps):
        total += s["dish"].price * s["qty"]
        db.add(models.OrderItem(
            order_id=order.id,
            dish_id=s["dish"].id,
            name=s["dish"].name,
            price=s["dish"].price,
            qty=s["qty"],
            options=s["options"],
            sort=idx,
        ))
    order.total_amount = total

    if customer_id:
        cust = db.get(models.Customer, customer_id)
        if cust and cust.first_order_at is None:
            cust.first_order_at = datetime.now()
    db.commit()
    db.refresh(order)

    payload = order_dict(db, order)
    if source == "customer":
        emit("waiter", {"type": "new_order", "order": payload})
    else:
        emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("新订单 #%d %s 桌台%s%s 合计 %.2f 来源%s（%d 道菜）",
                order.id, order.order_no,
                ("外带" if takeout else (table.name if table else table_id)),
                " 顾客%d" % customer_id if customer_id else "",
                total, source, len(snaps))
    return order


def confirm_order(db: Session, order: models.Order, staff: models.Staff) -> models.Order:
    if order.status != models.ORDER_PENDING:
        raise HTTPException(400, "订单不是待确认状态")
    order.status = models.ORDER_CONFIRMED
    order.confirm_by = staff.id
    order.confirmed_at = datetime.now()
    db.commit()
    payload = order_dict(db, order)
    emit("waiter", {"type": "order_updated", "order": payload})
    emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("订单确认 #%d %s 由员工 %s(%d)", order.id, order.order_no, staff.name, staff.id)
    return order


def reject_order(db: Session, order: models.Order, reason: str) -> models.Order:
    if order.status != models.ORDER_PENDING:
        raise HTTPException(400, "订单不是待确认状态")
    order.status = models.ORDER_CANCELLED
    order.cancel_reason = reason or "服务员拒单"
    db.commit()
    payload = order_dict(db, order)
    emit("waiter", {"type": "order_updated", "order": payload})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("订单拒单 #%d %s 原因：%s", order.id, order.order_no, order.cancel_reason)
    return order


def modify_order(db: Session, order: models.Order, remark: str, items_ops: list,
                 people: int = None, utensils: int = None) -> models.Order:
    """订单修改：人数/餐具/备注未结账随时可改；菜品增删调量仅待确认阶段（确认后走加菜）。"""
    if order.status not in (models.ORDER_PENDING, models.ORDER_CONFIRMED, models.ORDER_SERVED):
        raise HTTPException(400, "已结账/已取消订单不能修改")
    if items_ops and order.status != models.ORDER_PENDING:
        raise HTTPException(400, "订单已确认，调整菜品请走代客加菜")
    if remark is not None:
        order.remark = remark
    if people is not None:
        order.people = min(30, max(1, int(people)))
        if not order.is_takeout:
            order.utensil_fee = utensil_fee(order.people)
    if utensils is not None:
        order.utensils = min(30, max(0, int(utensils)))
        if order.is_takeout:
            # 外带：打包盒数量即收费依据
            order.utensil_fee = takeout_fee(order.utensils)
    if not items_ops:
        db.commit()
        payload = order_dict(db, order)
        emit("waiter", {"type": "order_updated", "order": payload})
        emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
        return order
    # 既有明细：按 item_id 调数量（qty<=0 删除）
    ops = {op["item_id"]: op.get("qty", 0) for op in items_ops if op.get("item_id")}
    # 新增菜品：dish_id + qty + options
    adds = [
        schemas.OrderItemIn(
            dish_id=op["dish_id"], qty=op.get("qty", 1), options=op.get("options"),
        )
        for op in items_ops
        if op.get("dish_id") and not op.get("item_id")
    ]
    snaps = _snapshot_items(db, adds)
    base_sort = (
        db.query(models.OrderItem)
        .filter_by(order_id=order.id)
        .count()
    )
    total = Decimal("0")
    for it in db.query(models.OrderItem).filter_by(order_id=order.id).all():
        if it.id in ops:
            qty = ops[it.id]
            if qty <= 0:
                db.delete(it)
                continue
            it.qty = qty
        total += it.price * it.qty
    for idx, s in enumerate(snaps):
        total += s["dish"].price * s["qty"]
        db.add(models.OrderItem(
            order_id=order.id,
            dish_id=s["dish"].id,
            name=s["dish"].name,
            price=s["dish"].price,
            qty=s["qty"],
            options=s["options"],
            sort=base_sort + idx,
        ))
    order.total_amount = total
    db.commit()
    payload = order_dict(db, order)
    emit("waiter", {"type": "order_updated", "order": payload})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    return order


def add_items(db: Session, order: models.Order, items) -> models.Order:
    """补单加菜：追加明细标「加」，先进入待确认（add_pending），服务员确认后才推送 KDS。"""
    if order.status in (models.ORDER_PAID, models.ORDER_CANCELLED):
        raise HTTPException(400, "订单已结账/已取消，不能补单")
    if order.status not in (models.ORDER_CONFIRMED, models.ORDER_SERVED):
        raise HTTPException(400, "订单确认后才能补单")
    snaps = _snapshot_items(db, items)
    if not snaps:
        raise HTTPException(400, "加菜列表为空")
    base_sort = (
        db.query(models.OrderItem)
        .filter_by(order_id=order.id)
        .count()
    )
    total = order.total_amount
    for idx, s in enumerate(snaps):
        total += s["dish"].price * s["qty"]
        db.add(models.OrderItem(
            order_id=order.id,
            dish_id=s["dish"].id,
            name=s["dish"].name,
            price=s["dish"].price,
            qty=s["qty"],
            options=s["options"],
            is_add=True,
            status=models.ITEM_ADD_PENDING,
            sort=base_sort + idx,
        ))
    order.total_amount = total
    db.commit()
    payload = order_dict(db, order)
    emit("waiter", {"type": "add_request", "order": payload})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("顾客加菜 #%d %s 新增 %d 道（待服务员确认），合计 %.2f",
                order.id, order.order_no, len(snaps), total)
    return order


def confirm_add(db: Session, order: models.Order) -> models.Order:
    """服务员确认加菜：追加明细转待出餐并推送 KDS（标「加」）；已出餐订单回到已确认。"""
    rows = (
        db.query(models.OrderItem)
        .filter_by(order_id=order.id, status=models.ITEM_ADD_PENDING)
        .all()
    )
    if not rows:
        raise HTTPException(400, "该订单没有待确认的加菜")
    for it in rows:
        it.status = models.ITEM_PENDING
    if order.status == models.ORDER_SERVED:
        order.status = models.ORDER_CONFIRMED
    db.commit()
    payload = order_dict(db, order)
    emit("waiter", {"type": "order_updated", "order": payload})
    emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("加菜确认 #%d %s（%d 道进后厨）", order.id, order.order_no, len(rows))
    return order


def waiter_add_items(db: Session, order: models.Order, items) -> models.Order:
    """代客加菜：服务员替顾客追加菜品，标「加」直达 KDS（无需二次确认）；已出餐订单回到已确认。"""
    if order.status in (models.ORDER_PAID, models.ORDER_CANCELLED):
        raise HTTPException(400, "订单已结账/已取消，不能加菜")
    if order.status not in (models.ORDER_CONFIRMED, models.ORDER_SERVED):
        raise HTTPException(400, "订单确认后才能加菜")
    snaps = _snapshot_items(db, items)
    if not snaps:
        raise HTTPException(400, "加菜列表为空")
    base_sort = (
        db.query(models.OrderItem)
        .filter_by(order_id=order.id)
        .count()
    )
    total = order.total_amount
    for idx, s in enumerate(snaps):
        total += s["dish"].price * s["qty"]
        db.add(models.OrderItem(
            order_id=order.id,
            dish_id=s["dish"].id,
            name=s["dish"].name,
            price=s["dish"].price,
            qty=s["qty"],
            options=s["options"],
            is_add=True,
            status=models.ITEM_PENDING,
            sort=base_sort + idx,
        ))
    order.total_amount = total
    if order.status == models.ORDER_SERVED:
        order.status = models.ORDER_CONFIRMED
    db.commit()
    payload = order_dict(db, order)
    emit("waiter", {"type": "order_updated", "order": payload})
    emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("代客加菜 #%d %s 新增 %d 道（直达后厨），合计 %.2f",
                order.id, order.order_no, len(snaps), total)
    return order


def add_queue(db: Session) -> list:
    """服务员端「加菜待确认」队列。"""
    orders = (
        db.query(models.Order)
        .filter(models.Order.status.in_([models.ORDER_CONFIRMED, models.ORDER_SERVED]))
        .all()
    )
    out = []
    for o in orders:
        adds = [
            it for it in db.query(models.OrderItem)
            .filter_by(order_id=o.id)
            .order_by(models.OrderItem.sort, models.OrderItem.id)
            .all()
            if it.status == models.ITEM_ADD_PENDING
        ]
        if not adds:
            continue
        payload = order_dict(db, o)
        payload["add_items"] = [item_dict(it) for it in adds]
        out.append(payload)
    out.sort(key=lambda x: x["id"])
    return out


def urge_order(db: Session, order: models.Order) -> models.Order:
    if order.status not in (models.ORDER_CONFIRMED, models.ORDER_SERVED):
        raise HTTPException(400, "仅已确认订单可催单")
    order.urged = True
    order.urged_at = datetime.now()
    db.commit()
    emit("kds", {"type": "urge", "order_id": order.id, "table_name": order_dict(db, order)["table_name"]})
    emit("kds", {"type": "kds_refresh"})
    return order


def item_out(db: Session, item: models.OrderItem) -> models.Order:
    """逐道出餐（幂等）：单品已出餐 → 按 BOM 扣库存；整单出完 → 订单已出餐。"""
    if item.status == models.ITEM_ADD_PENDING:
        raise HTTPException(400, "该菜品加菜尚未经服务员确认")
    if item.status == models.ITEM_OUT:
        return db.get(models.Order, item.order_id)  # 幂等：重复点出餐不重复扣库存
    item.status = models.ITEM_OUT
    item.out_at = datetime.now()
    deduct_for_item(db, item)  # 按 BOM「标准用量×(1+损耗率)」扣减原材料库存
    order = db.get(models.Order, item.order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    pending = (
        db.query(models.OrderItem)
        .filter_by(order_id=order.id, status=models.ITEM_PENDING)
        .count()
    )
    if pending == 0 and order.status == models.ORDER_CONFIRMED:
        order.status = models.ORDER_SERVED
    db.commit()
    emit("kds", {"type": "item_out", "order_id": order.id, "item_id": item.id})
    emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    return order


def settle_order(
    db: Session,
    order: models.Order,
    method: str,
    cashier: models.Staff,
    discount: Decimal = Decimal("0"),
    paid_amount: Decimal = None,
    settle_note: str = "",
) -> models.Order:
    """线下结账（现金/微信/支付宝/免单）：优惠金额与实收金额联动（优惠 + 实收 = 应收），标记桌台待清台。
    应收 = 菜品合计 total_amount + 餐具费（外带为打包盒费）utensil_fee。
    免单：实收 0 元，必填免单原因，按应收金额计入营业额统计。"""
    if order.status == models.ORDER_CANCELLED:
        raise HTTPException(400, "已取消订单不能结账")
    if order.pay_status == models.PAY_PAID:
        raise HTTPException(400, "订单已结账")
    if method not in models.PAY_METHODS:
        raise HTTPException(400, "支付方式无效")
    receivable = order.receivable
    if discount < 0:
        raise HTTPException(400, "优惠金额不能为负")
    if discount > receivable:
        raise HTTPException(400, "优惠金额不能超过应收金额")
    free = method == "free" or (paid_amount is not None and paid_amount == 0)
    if free:
        # 免单：收费为 0，必须备注免单原因；优惠全额冲抵，实收 0
        if not (settle_note or "").strip():
            raise HTTPException(400, "免单需填写免单原因")
        method = "free"
        discount = receivable.quantize(Decimal("0.01"))
        paid_amount = Decimal("0")
    elif paid_amount is not None:
        if paid_amount < 0:
            raise HTTPException(400, "实收金额不能为负")
        if paid_amount > receivable:
            raise HTTPException(400, "实收金额不能超过应收金额")
        # 优惠与实收联动：实收确定后优惠自动补齐（优惠 + 实收 = 应收），前端联动展示也走此规则
        discount = (receivable - paid_amount).quantize(Decimal("0.01"))
    else:
        paid_amount = receivable - discount
    order.pay_status = models.PAY_PAID
    order.pay_method = method
    order.discount = discount
    order.paid_amount = paid_amount
    order.settle_note = (settle_note or "").strip()[:255]
    order.paid_at = datetime.now()
    order.cashier_id = cashier.id
    order.status = models.ORDER_PAID
    if not order.is_takeout:
        table = db.get(models.DiningTable, order.table_id)
        if table:
            table.dirty = True
    db.commit()
    emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("结账 #%d %s 方式%s 应收 %.2f 优惠 %.2f 实收 %.2f%s 收银 %s(%d)",
                order.id, order.order_no, method, receivable, discount, paid_amount,
                (" 免单原因：" + order.settle_note) if free else "",
                cashier.name, cashier.id)
    return order


def cancel_order(db: Session, order: models.Order, reason: str) -> models.Order:
    if order.status == models.ORDER_PAID:
        raise HTTPException(400, "已结账订单不能取消（请走退款流程）")
    order.status = models.ORDER_CANCELLED
    order.cancel_reason = reason or "取消订单"
    db.commit()
    emit("kds", {"type": "kds_refresh"})
    emit("order:%d" % order.id, {"type": "status", "order": customer_progress(db, order)})
    logger.info("取消订单 #%d %s 原因：%s", order.id, order.order_no, order.cancel_reason)
    return order


def clean_table(db: Session, table: models.DiningTable) -> models.DiningTable:
    table.dirty = False
    db.commit()
    emit("waiter", {"type": "table_refresh"})
    return table


def _kds_column(items: list) -> str:
    """KDS 三栏归属（派生，不落库）：new 全部待出餐 / cooking 部分已出 / done 整单出完。"""
    statuses = {i["status"] for i in items}
    if not statuses or statuses == {models.ITEM_PENDING}:
        return "new"
    if statuses == {models.ITEM_OUT}:
        return "done"
    return "cooking"


def _kds_annotate(db: Session, payload: dict) -> None:
    """明细附菜品分类（KDS 双页按餐品/主食过滤）与等待时长、超时/催单标记。"""
    dish_ids = [i["dish_id"] for i in payload["items"] if i.get("dish_id")]
    cat_map = {}
    if dish_ids:
        rows = (
            db.query(models.Dish.id, models.Category.name)
            .join(models.Category, models.Dish.category_id == models.Category.id)
            .filter(models.Dish.id.in_(dish_ids))
            .all()
        )
        cat_map = {r[0]: r[1] for r in rows}
    for i in payload["items"]:
        cname = cat_map.get(i.get("dish_id"), "")
        i["category_name"] = cname
        i["is_staple"] = cname == "主食"


def _kds_wait(payload: dict, base) -> None:
    wait = max(0, int((datetime.now() - base).total_seconds() // 60))
    payload["wait_min"] = wait
    payload["timeout"] = wait >= settings.kds_timeout_minutes
    payload["urgent"] = payload.get("urged") or (wait >= settings.kds_timeout_minutes)


def kds_payload(db: Session, order: models.Order) -> dict:
    """基础卡：只含原单明细；加菜全部出餐后并入基础卡（保留「加」标记），期间不占用原单出餐数据。"""
    payload = order_dict(db, order)
    # 未确认的加菜不出现在 KDS 上（服务员确认后才推送）
    items = [i for i in payload["items"] if i["status"] != models.ITEM_ADD_PENDING]
    base_items = [i for i in items if not i["is_add"]]
    add_items = [i for i in items if i["is_add"]]
    merged = bool(add_items) and all(i["status"] == models.ITEM_OUT for i in add_items)
    if merged:
        base_items = base_items + add_items
    payload["items"] = base_items
    payload["has_add_card"] = bool(add_items) and not merged  # 另有加菜卡在「待出餐」栏
    _kds_annotate(db, payload)
    _kds_wait(payload, order.confirmed_at or order.created_at)
    payload["column"] = _kds_column(payload["items"])
    return payload


def kds_add_payload(db: Session, order: models.Order):
    """加菜独立卡：加菜尚未全部出餐时，单独出现在「待出餐」栏（同桌号、标「加」），不占用出餐中数据。"""
    items = (
        db.query(models.OrderItem)
        .filter(models.OrderItem.order_id == order.id,
                models.OrderItem.is_add == True,  # noqa: E712
                models.OrderItem.status != models.ITEM_ADD_PENDING)
        .order_by(models.OrderItem.sort, models.OrderItem.id)
        .all()
    )
    if not items or all(i.status == models.ITEM_OUT for i in items):
        return None  # 加菜已全部出餐 → 并入基础卡，不再单列
    payload = order_dict(db, order)
    payload["items"] = [item_dict(i) for i in items]
    payload["is_add_card"] = True
    payload["has_add_card"] = False
    _kds_annotate(db, payload)
    # 等待时长按最早的待出餐加菜计时，与基础卡相互独立
    earliest = next((i for i in items if i.status == models.ITEM_PENDING), items[0])
    _kds_wait(payload, earliest.created_at or order.confirmed_at or order.created_at)
    payload["column"] = "new"
    return payload


def kds_columns(db: Session) -> dict:
    orders = (
        db.query(models.Order)
        .filter(models.Order.status.in_([models.ORDER_CONFIRMED, models.ORDER_SERVED]))
        .all()
    )
    cols = {"new": [], "cooking": [], "done": []}
    for o in orders:
        p = kds_payload(db, o)
        cols[p["column"]].append(p)
        add = kds_add_payload(db, o)
        if add is not None:
            cols["new"].append(add)  # 加菜卡固定待在「待出餐」栏
    for key in cols:
        cols[key].sort(key=lambda x: (not x["urged"], x["confirmed_at"] or x["created_at"]))
    return {
        "new": cols["new"],
        "cooking": cols["cooking"],
        "done": cols["done"],
        "pending_total": len(cols["new"]) + len(cols["cooking"]),
        "timeout_minutes": settings.kds_timeout_minutes,
    }


def kds_done(db: Session) -> list:
    """当日全部出餐完成的订单（整单出完即入列，含已结账），按整单出完时间倒序。"""
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    orders = (
        db.query(models.Order)
        .filter(models.Order.created_at >= start,
                models.Order.status != models.ORDER_CANCELLED)
        .all()
    )
    out = []
    for o in orders:
        items = (
            db.query(models.OrderItem)
            .filter(models.OrderItem.order_id == o.id,
                    models.OrderItem.status != models.ITEM_ADD_PENDING)
            .order_by(models.OrderItem.sort, models.OrderItem.id)
            .all()
        )
        if not items or {i.status for i in items} != {models.ITEM_OUT}:
            continue  # 只要还有未出餐的菜品就不算整单出完
        finished = max([i.out_at for i in items if i.out_at] or [o.created_at])
        p = order_dict(db, o)
        p["items"] = [item_dict(i) for i in items]
        p["finished_at"] = finished.strftime("%m-%d %H:%M:%S")
        p["paid"] = o.pay_status == models.PAY_PAID
        p["_sort"] = finished
        out.append(p)
    out.sort(key=lambda p: p["_sort"], reverse=True)
    for p in out:
        p.pop("_sort", None)
    return out


def customer_progress(db: Session, order: models.Order) -> dict:
    """顾客端三步进度：已下单 → 已确认 → 已出餐（不感知「出餐中」）。"""
    done1 = True
    done2 = order.status in (models.ORDER_CONFIRMED, models.ORDER_SERVED, models.ORDER_PAID)
    done3 = order.status in (models.ORDER_SERVED, models.ORDER_PAID)
    steps = [
        {"key": "placed", "label": "已下单", "done": done1, "now": False},
        {"key": "confirmed", "label": "已确认", "done": done2, "now": False},
        {"key": "served", "label": "已出餐", "done": done3, "now": False},
    ]
    if order.status == models.ORDER_CANCELLED:
        for s in steps:
            s["done"] = False
        steps[0]["done"] = True
    else:
        for s in steps:
            if not s["done"]:
                s["now"] = True
                break
    status_card = {
        models.ORDER_PENDING: ("订单已提交 · 等待确认", "服务员将与您核对订单，确认后为您备餐"),
        models.ORDER_CONFIRMED: ("订单已确认", "出餐后将提醒您 · 请留意取餐"),
        models.ORDER_SERVED: ("餐品已出餐", "请慢用，结账请至收银台"),
        models.ORDER_PAID: ("已结账", "感谢惠顾，欢迎下次光临"),
        models.ORDER_CANCELLED: ("订单已取消", order.cancel_reason or "如需用餐请重新下单"),
    }[order.status]
    base = order_dict(db, order)
    base["steps"] = steps
    base["status_card"] = {"title": status_card[0], "sub": status_card[1]}
    return base


def table_overview(db: Session) -> list:
    # 过滤 id=0 外带占位行：外带单不占桌台，不在桌台总览展示
    tables = (
        db.query(models.DiningTable)
        .filter(models.DiningTable.id != 0)
        .order_by(models.DiningTable.name)
        .all()
    )
    active_orders = (
        db.query(models.Order)
        .filter(models.Order.status.in_([models.ORDER_PENDING, models.ORDER_CONFIRMED, models.ORDER_SERVED]))
        .all()
    )
    by_table = {}
    for o in active_orders:
        by_table.setdefault(o.table_id, []).append(o)
    out = []
    for t in tables:
        if not t.enabled:
            state, state_text = "stopped", "停用"
        elif t.dirty:
            state, state_text = "dirty", "待清台"
        else:
            ods = by_table.get(t.id, [])
            if any(o.status == models.ORDER_PENDING for o in ods):
                state, state_text = "confirm", "待确认"
            elif ods:
                state, state_text = "dining", "用餐中"
            else:
                state, state_text = "free", "空闲"
        out.append({
            "id": t.id, "name": t.name, "area": t.area, "seats": t.seats,
            "state": state, "state_text": state_text,
            "order_count": len(by_table.get(t.id, [])),
        })
    return out
