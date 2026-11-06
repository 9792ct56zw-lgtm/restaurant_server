"""16 张核心表：订单域 8（含支付预留）+ 供应链域 6 + 员工/门店 + AI 报告。"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, JSON,
                        Numeric, String, Text)

from .database import Base

# ---------------- 状态常量 ----------------
# 订单状态机（无「制作中」）：待确认 → 已确认 → 已出餐 → 已结账 / 已取消
ORDER_PENDING = "pending_confirm"   # 待确认
ORDER_CONFIRMED = "confirmed"       # 已确认
ORDER_SERVED = "served"             # 已出餐
ORDER_PAID = "paid"                 # 已结账
ORDER_CANCELLED = "cancelled"       # 已取消
ORDER_STATUSES = [ORDER_PENDING, ORDER_CONFIRMED, ORDER_SERVED, ORDER_PAID, ORDER_CANCELLED]

ITEM_PENDING = "pending"            # 单品：待出餐
ITEM_OUT = "out"                    # 单品：已出餐
ITEM_ADD_PENDING = "add_pending"    # 单品：补单加菜·待服务员确认（确认后才推 KDS）

DISH_ON = "on"
DISH_OFF = "off"
DISH_SOLDOUT = "soldout"

PAY_PENDING = "pending"
PAY_PAID = "paid"
PAY_METHODS = ["cash", "wechat", "alipay", "free"]  # 现金 / 微信 / 支付宝 / 免单
PAY_METHOD_TEXT = {"cash": "现金", "wechat": "微信", "alipay": "支付宝", "free": "免单"}

PO_DRAFT = "draft"        # 待下单
PO_ORDERED = "ordered"    # 已下单
PO_INBOUND = "inbound"    # 已入库

IL_PURCHASE_IN = "purchase_in"   # 采购入库
IL_ORDER_USE = "order_use"       # 出餐领用（含损耗）
IL_STOCKTAKE = "stocktake"       # 盘点调整
IL_WASTE = "waste"               # 报损

ROLE_ADMIN = "admin"       # 店长
ROLE_CASHIER = "cashier"   # 收银
ROLE_WAITER = "waiter"     # 服务员
ROLE_KITCHEN = "kitchen"   # 后厨
ROLES = [ROLE_ADMIN, ROLE_CASHIER, ROLE_WAITER, ROLE_KITCHEN]


def now():
    return datetime.now()


# ---------------- 订单域 ----------------
class Shop(Base):
    __tablename__ = "shop"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), default="川香居 · 家常菜")
    phone = Column(String(32), default="")
    address = Column(String(128), default="")
    business_hours = Column(String(64), default="10:00 - 22:00")
    notice = Column(String(255), default="")
    created_at = Column(DateTime, default=now)


class Staff(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, index=True)
    password_hash = Column(String(128))
    name = Column(String(32))
    role = Column(String(16), default=ROLE_WAITER)  # 店长/收银/服务员/后厨
    status = Column(String(16), default="active")   # active / disabled
    created_at = Column(DateTime, default=now)


class Customer(Base):
    __tablename__ = "customer"
    id = Column(Integer, primary_key=True)
    openid = Column(String(64), unique=True, index=True)
    nickname = Column(String(64), default="")
    phone = Column(String(20), default="")
    first_order_at = Column(DateTime)
    created_at = Column(DateTime, default=now)


class Category(Base):
    __tablename__ = "category"
    id = Column(Integer, primary_key=True)
    name = Column(String(32))
    sort = Column(Integer, default=0)
    status = Column(String(16), default="active")


class Dish(Base):
    __tablename__ = "dish"
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("category.id"))
    name = Column(String(64), index=True)
    price = Column(Numeric(10, 2))
    image = Column(String(255), default="")          # 菜品图片 URL（可选）
    emoji = Column(String(8), default="")            # 菜品图标（无图片时的展示兜底）
    description = Column(String(255), default="")
    sales = Column(Integer, default=0)              # 月售（种子数据展示用，可累计）
    option_template = Column(JSON)                  # {"葱":["要葱","不要葱"],"辣度":["微辣","中辣","特辣"]}
    status = Column(String(16), default=DISH_ON)    # on / off / soldout
    created_at = Column(DateTime, default=now)


class DiningTable(Base):
    __tablename__ = "dining_table"
    id = Column(Integer, primary_key=True)
    name = Column(String(16), unique=True)          # 桌号 A03
    area = Column(String(16), default="大厅")
    seats = Column(Integer, default=4)
    qr_scene = Column(String(32), unique=True)      # 小程序码场景值（默认=桌号）
    enabled = Column(Boolean, default=True)
    dirty = Column(Boolean, default=False)          # 待清台
    created_at = Column(DateTime, default=now)


class Order(Base):
    __tablename__ = "order"
    id = Column(Integer, primary_key=True)
    order_no = Column(String(32), unique=True, index=True)
    table_id = Column(Integer, ForeignKey("dining_table.id"), index=True)
    customer_id = Column(Integer, ForeignKey("customer.id"))
    status = Column(String(20), default=ORDER_PENDING, index=True)
    total_amount = Column(Numeric(10, 2), default=0)
    remark = Column(String(255), default="")
    people = Column(Integer, default=1)
    utensils = Column(Integer, default=0)
    utensil_fee = Column(Numeric(10, 2), default=0)  # 餐具费：用餐人数 × 1 元（堂食/外带统一）
    confirm_by = Column(Integer, ForeignKey("staff.id"))
    confirmed_at = Column(DateTime)
    urged = Column(Boolean, default=False)          # 服务员催单
    urged_at = Column(DateTime)
    pay_status = Column(String(16), default=PAY_PENDING)
    pay_method = Column(String(16))                 # cash / wechat / alipay
    discount = Column(Numeric(10, 2), default=0)    # 优惠金额
    paid_amount = Column(Numeric(10, 2))            # 实际收款金额（含优惠后实收）
    settle_note = Column(String(255), default="")   # 结账备注（免单时必填：描述免单原因）
    paid_at = Column(DateTime)
    cashier_id = Column(Integer, ForeignKey("staff.id"))
    cancel_reason = Column(String(255), default="")
    is_takeout = Column(Boolean, default=False)      # 外带单：不占桌台（table_id=0）、结账不置待清台
    created_at = Column(DateTime, default=now, index=True)

    @property
    def receivable(self):
        """应收金额 = 菜品合计 + 餐具费（结账/统计统一口径）。"""
        return (self.total_amount or 0) + (self.utensil_fee or 0)


class OrderItem(Base):
    __tablename__ = "order_item"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("order.id"), index=True)
    dish_id = Column(Integer, ForeignKey("dish.id"))
    name = Column(String(64))                       # 名称快照
    price = Column(Numeric(10, 2))                  # 价格快照
    qty = Column(Integer, default=1)
    options = Column(JSON)                          # 选项快照 {"葱":"不要葱","辣度":"中辣"}
    is_add = Column(Boolean, default=False)         # 补单加菜标「加」
    status = Column(String(16), default=ITEM_PENDING)  # 单品级出餐状态
    out_at = Column(DateTime)
    sort = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)


class Payment(Base):
    """微信支付记录（预留表：字段与回调流程已设计，本期不启用）。"""
    __tablename__ = "payment"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("order.id"))
    channel = Column(String(16), default="wxpay")
    pay_no = Column(String(64))                     # 商户订单号
    amount = Column(Numeric(10, 2))
    status = Column(String(16), default="created")
    raw = Column(JSON)                              # 回调原文
    created_at = Column(DateTime, default=now)


# ---------------- 供应链域 ----------------
class Supplier(Base):
    __tablename__ = "supplier"
    id = Column(Integer, primary_key=True)
    name = Column(String(64))
    contact = Column(String(32), default="")
    phone = Column(String(20), default="")
    status = Column(String(16), default="active")


class Ingredient(Base):
    __tablename__ = "ingredient"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), index=True)
    unit = Column(String(8), default="g")           # kg / g / 个 / 瓶
    stock = Column(Numeric(12, 3), default=0)
    safe_stock = Column(Numeric(12, 3), default=0)
    ref_price = Column(Numeric(10, 3), default=0)   # 参考单价（元/单位）
    status = Column(String(16), default="active")


class PurchaseOrder(Base):
    __tablename__ = "purchase_order"
    id = Column(Integer, primary_key=True)
    order_no = Column(String(32), unique=True)
    supplier_id = Column(Integer, ForeignKey("supplier.id"))
    status = Column(String(16), default=PO_DRAFT)   # draft / ordered / inbound
    total = Column(Numeric(10, 2), default=0)
    remark = Column(String(255), default="")
    created_at = Column(DateTime, default=now, index=True)


class PurchaseItem(Base):
    __tablename__ = "purchase_item"
    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_order.id"), index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id"))
    qty = Column(Numeric(12, 3), default=0)
    unit_price = Column(Numeric(10, 3), default=0)
    amount = Column(Numeric(10, 2), default=0)


class DishIngredient(Base):
    """菜品配方 BOM：标准用量 + 损耗率；出餐按 标准量×(1+损耗率) 扣库存。"""
    __tablename__ = "dish_ingredient"
    id = Column(Integer, primary_key=True)
    dish_id = Column(Integer, ForeignKey("dish.id"), index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id"))
    quantity = Column(Numeric(12, 3), default=0)    # 标准用量（按 ingredient.unit）
    loss_rate = Column(Numeric(6, 2), default=0)    # 损耗率 %（如 3.0）
    created_at = Column(DateTime, default=now)


class InventoryLog(Base):
    __tablename__ = "inventory_log"
    id = Column(Integer, primary_key=True)
    ingredient_id = Column(Integer, ForeignKey("ingredient.id"), index=True)
    type = Column(String(20))                       # purchase_in / order_use / stocktake / waste
    quantity = Column(Numeric(12, 3))               # 变动量（入正出负）
    balance = Column(Numeric(12, 3))                # 变动后结余
    ref_type = Column(String(20), default="")       # order / order_item / purchase
    ref_id = Column(Integer, default=0)
    remark = Column(String(255), default="")
    created_at = Column(DateTime, default=now, index=True)


# ---------------- AI 报告（聚合快照，无外键） ----------------
class AIReport(Base):
    __tablename__ = "ai_report"
    id = Column(Integer, primary_key=True)
    type = Column(String(16))                       # daily / weekly / monthly / yearly
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    content = Column(Text)
    created_at = Column(DateTime, default=now, index=True)


# ---------------- 营业班次（开门 / 结单对账） ----------------
class DaySession(Base):
    """营业日记录：开门时登记零钱箱与天气；结单时生成系统收款 vs 钱箱实点对账快照。"""
    __tablename__ = "day_session"
    id = Column(Integer, primary_key=True)
    biz_date = Column(String(10), index=True)       # 营业日期 YYYY-MM-DD
    type = Column(String(8), default="open")        # open 开门 / close 结单
    opened_at = Column(DateTime)
    closed_at = Column(DateTime)
    open_change = Column(Numeric(10, 2), default=100)   # 开门零钱箱金额（默认 100 元）
    city = Column(String(32), default="")
    weather = Column(String(64), default="")        # 开门天气快照
    weather_detail = Column(String(255), default="")
    # 结单对账快照：系统按收款记录汇总 vs 钱箱现金实点
    sys_cash = Column(Numeric(10, 2), default=0)    # 系统现金实收
    sys_wechat = Column(Numeric(10, 2), default=0)  # 系统微信收款
    sys_alipay = Column(Numeric(10, 2), default=0)  # 系统支付宝收款
    sys_total = Column(Numeric(10, 2), default=0)   # 系统合计实收（不含免单实收 0）
    free_amount = Column(Numeric(10, 2), default=0) # 当日免单金额（按应收计）
    order_count = Column(Integer, default=0)        # 当日结账订单数
    cash_box = Column(Numeric(10, 2))               # 钱箱现金实点
    diff = Column(Numeric(10, 2), default=0)        # 现金差额 = 钱箱 − (开门零钱 + 系统现金)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=now, index=True)
