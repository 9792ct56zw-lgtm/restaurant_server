"""请求体模型。"""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StaffLoginIn(BaseModel):
    username: str
    password: str


class CustomerLoginIn(BaseModel):
    code: str
    nickname: Optional[str] = None


class OrderItemIn(BaseModel):
    dish_id: int
    qty: int = Field(ge=1, le=99)
    options: Optional[Dict[str, Any]] = None


class OrderCreateIn(BaseModel):
    table_id: Optional[int] = None    # 外带单无需桌台
    takeout: bool = False             # True=外带（服务员直接下单，不占桌台）
    items: List[OrderItemIn]
    remark: str = ""
    people: int = Field(default=1, ge=1, le=30)
    utensils: int = Field(default=0, ge=0, le=30)


class AddItemsIn(BaseModel):
    items: List[OrderItemIn]


class RejectIn(BaseModel):
    reason: str = ""


class ModifyIn(BaseModel):
    remark: Optional[str] = None
    # 用餐人数/餐具（未结账订单随时可改；改人数后餐具默认=人数，可再单独传 utensils 覆盖）
    people: Optional[int] = Field(default=None, ge=1, le=30)
    utensils: Optional[int] = Field(default=None, ge=0, le=30)
    # 既有明细调数量：[{"item_id": 1, "qty": 2}]（qty=0 删除）
    # 新增菜品：      [{"dish_id": 3, "qty": 1, "options": {...}}]
    items: List[Dict[str, Any]] = []


class SettleIn(BaseModel):
    method: str = "cash"                    # cash / wechat / alipay / free(免单)
    discount: Decimal = Decimal("0")        # 优惠金额（≥0）
    paid_amount: Optional[Decimal] = None   # 实际收款金额；缺省 = 应收 − 优惠；免单传 0
    settle_note: Optional[str] = None       # 结账备注；免单（实收 0）时必填免单原因


class CancelIn(BaseModel):
    reason: str = ""


class DayOpenIn(BaseModel):
    change: Optional[Decimal] = None    # 开门零钱箱金额，缺省 100 元
    city: Optional[str] = None          # 天气城市，缺省取配置


class DayCloseIn(BaseModel):
    cash_box: Decimal = Field(ge=0)     # 钱箱现金实点金额
    note: str = ""                      # 结单备注（差额说明等）


class CategoryIn(BaseModel):
    name: str
    sort: int = 0


class DishIn(BaseModel):
    category_id: int
    name: str
    price: Decimal = Field(ge=0)
    description: str = ""
    image: str = ""
    emoji: str = ""
    option_template: Optional[Dict[str, List[str]]] = None
    status: str = "on"


class DishStatusIn(BaseModel):
    status: str  # on / off / soldout


class TableIn(BaseModel):
    name: str
    area: str = "大厅"
    seats: int = Field(default=4, ge=1, le=30)
    enabled: bool = True


class TableUpdateIn(BaseModel):
    name: Optional[str] = None
    area: Optional[str] = None
    seats: Optional[int] = None
    enabled: Optional[bool] = None


class SupplierIn(BaseModel):
    name: str
    contact: str = ""
    phone: str = ""
    status: str = "active"


class IngredientIn(BaseModel):
    name: str
    unit: str = "g"
    stock: Decimal = Decimal("0")
    safe_stock: Decimal = Decimal("0")
    ref_price: Decimal = Decimal("0")


class IngredientAdjustIn(BaseModel):
    delta: Decimal
    type: str = "stocktake"  # stocktake / waste / purchase_in
    remark: str = ""


class PurchaseItemIn(BaseModel):
    ingredient_id: int
    qty: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseCreateIn(BaseModel):
    supplier_id: int
    items: List[PurchaseItemIn]
    remark: str = ""


class PurchaseInboundIn(BaseModel):
    items: List[PurchaseItemIn]


class BomItemIn(BaseModel):
    ingredient_id: int
    quantity: Decimal = Field(gt=0)
    loss_rate: Decimal = Field(ge=0)


class BomIn(BaseModel):
    items: List[BomItemIn]


class ShopIn(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    business_hours: Optional[str] = None
    notice: Optional[str] = None


class StaffCreateIn(BaseModel):
    username: str
    password: str
    name: str
    role: str


class StaffUpdateIn(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class ResetPasswordIn(BaseModel):
    password: str


class ReportGenIn(BaseModel):
    type: str = "daily"  # daily / weekly / monthly / yearly
    period_start: Optional[str] = None   # YYYY-MM-DD
    period_end: Optional[str] = None


class ChatIn(BaseModel):
    question: str
