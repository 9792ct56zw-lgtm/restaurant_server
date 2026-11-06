"""微信支付（预留）：表结构与回调流程已设计，本期不启用，统一线下收银台结账。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_customer_optional

router = APIRouter(prefix="/api/payment", tags=["payment"])


@router.get("/status/{order_id}")
def pay_status(order_id: int, db: Session = Depends(get_db),
               cust=Depends(get_customer_optional)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    return {
        "order_id": order.id,
        "pay_status": order.pay_status,
        "pay_method": order.pay_method,
        "total_amount": float(order.receivable),
    }


@router.post("/prepay")
def prepay():
    """预留：本期不接入微信支付。"""
    raise HTTPException(501, "本期暂未启用线上支付，请至收银台结账（现金/商户收款码）")
