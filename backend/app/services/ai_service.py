"""AI 经营分析：国产大模型 OpenAI 兼容 API（DeepSeek/通义千问/智谱 GLM），
只上传聚合后的最小必要数据；未配置密钥时用规则模板生成报告兜底。"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from . import stats_service
from .inventory_service import warnings as inventory_warnings

REPORT_TYPES = {
    "daily": ("今日经营日报", "daily"),
    "weekly": ("本周经营周报", "weekly"),
    "monthly": ("本月经营月报", "monthly"),
    "yearly": ("本年经营年报", "yearly"),
}


def _period(type_: str):
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    if type_ == "daily":
        return today, today + timedelta(days=1), today
    if type_ == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7), start
    if type_ == "monthly":
        start = datetime(now.year, now.month, 1)
        return start, start + timedelta(days=32), start
    start = datetime(now.year, 1, 1)
    return start, start + timedelta(days=366), start


def build_context(db: Session, type_: str) -> dict:
    """聚合快照（最小必要数据，不含任何个人信息）。"""
    start, end, label = _period(type_)
    rows = (
        db.query(models.Order)
        .filter(models.Order.created_at >= start,
                models.Order.created_at < end,
                models.Order.status != models.ORDER_CANCELLED)
        .all()
    )
    paid = [o for o in rows if o.pay_status == models.PAY_PAID]
    # 营业额口径：免单单按应收计入，其余按实收
    revenue = sum(
        (o.receivable if o.pay_method == "free"
         else (o.paid_amount if o.paid_amount is not None else o.receivable)
         for o in paid),
        Decimal("0"),
    )
    dishes = (
        db.query(models.OrderItem)
        .join(models.Order, models.OrderItem.order_id == models.Order.id)
        .filter(models.Order.created_at >= start,
                models.Order.created_at < end,
                models.Order.status != models.ORDER_CANCELLED)
        .all()
    )
    dish_agg = {}
    for it in dishes:
        dish_agg[it.name] = dish_agg.get(it.name, 0) + it.qty
    top = sorted(dish_agg.items(), key=lambda kv: -kv[1])[:8]
    ws = inventory_warnings(db)
    return {
        "period": type_,
        "date": label.strftime("%Y-%m-%d"),
        "orders": len(rows),
        "paid_orders": len(paid),
        "revenue": float(revenue),
        "avg_price": float(revenue / len(paid)) if paid else 0,
        "top_dishes": [{"name": k, "qty": v} for k, v in top],
        "inventory_warnings": [
            {"name": w["name"], "level": w["level"], "stock": w["stock"], "unit": w["unit"]}
            for w in ws[:10]
        ],
        "loss_compare": stats_service.loss_compare(db, start, end)[:8],
        "cancelled": sum(1 for o in rows if o.status == models.ORDER_CANCELLED),
    }


def call_llm(messages: list) -> str:
    """调用 OpenAI 兼容接口；未配置密钥或调用失败返回 None（触发规则兜底）。"""
    if not settings.ai_api_key or not settings.ai_base_url:
        return None
    url = settings.ai_base_url.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(
            url,
            json={
                "model": settings.ai_model,
                "messages": messages,
                "temperature": 0.6,
                "max_tokens": 1200,
            },
            headers={"Authorization": "Bearer " + settings.ai_api_key},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def rule_report(db: Session, type_: str) -> str:
    """规则模板兜底报告（无 AI 密钥时仍可出日报/周报/月报）。"""
    ctx = build_context(db, type_)
    title = REPORT_TYPES[type_][0]
    lines = [
        "【%s】%s" % (title, ctx["date"]),
        "一、经营概览",
        "· 订单 %s 单（已结账 %s 单，取消 %s 单），营业额 ¥%s，客单价 ¥%s"
        % (ctx["orders"], ctx["paid_orders"], ctx["cancelled"],
           "{:,.2f}".format(ctx["revenue"]), "{:,.2f}".format(ctx["avg_price"])),
    ]
    if ctx["top_dishes"]:
        lines.append("二、菜品排行")
        lines.append("· " + "、".join("%s ×%s" % (t["name"], t["qty"]) for t in ctx["top_dishes"][:5]))
    if ctx["inventory_warnings"]:
        lines.append("三、库存预警")
        for w in ctx["inventory_warnings"]:
            lines.append("· %s（%s预警）库存 %s%s" % (w["name"], "红色" if w["level"] == "red" else "黄色", w["stock"], w["unit"]))
    if ctx["loss_compare"]:
        worst = max(ctx["loss_compare"], key=lambda x: x["loss_rate"])
        lines.append("四、损耗情况")
        lines.append("· 实际损耗最高的原料：%s（损耗率 %.1f%%），建议核对配方用量与后厨操作" % (worst["name"], worst["loss_rate"]))
    lines.append("五、改进建议")
    if ctx["orders"] and ctx["avg_price"] < 40:
        lines.append("· 客单价偏低，建议推出组合套餐或加购引导")
    if ctx["cancelled"]:
        lines.append("· 取消单 %s 单，建议回顾取消原因，减少误单" % ctx["cancelled"])
    if ctx["inventory_warnings"]:
        lines.append("· 存在低库存原料，建议尽快生成采购单补货")
    lines.append("· 高峰时段订单集中，建议提前备料、合理排班")
    return "\n".join(lines)


def generate_report(db: Session, type_: str) -> models.AIReport:
    if type_ not in REPORT_TYPES:
        raise ValueError("报告类型无效")
    ctx = build_context(db, type_)
    start, end, _ = _period(type_)
    system = ("你是一家中小餐厅的资深经营顾问。基于给定的聚合经营数据，"
              "用简体中文生成一份简洁、务实的经营报告，分「经营概览/亮点与问题/改进建议」三部分，"
              "使用具体数字，建议可落地，不超过 400 字。只依据给定数据，不要编造。")
    llm_text = call_llm([
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
    ])
    content = llm_text or rule_report(db, type_)
    report = models.AIReport(
        type=type_,
        period_start=start,
        period_end=end,
        content=content,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def chat(db: Session, question: str) -> str:
    """自然语言查询经营数据：上传今日/本周聚合快照作为上下文。"""
    ctx = {
        "today": build_context(db, "daily"),
        "week": build_context(db, "weekly"),
    }
    system = ("你是餐厅的经营数据助手。基于给定的今日与本周聚合数据回答用户问题，"
              "回答简洁、用具体数字，若数据不足请说明。只依据给定数据，不要编造。")
    answer = call_llm([
        {"role": "system", "content": system},
        {"role": "user", "content": "数据：%s\n问题：%s" % (json.dumps(ctx, ensure_ascii=False), question)},
    ])
    if answer:
        return answer
    today = ctx["today"]
    week = ctx["week"]
    return (
        "（未配置 AI 服务，以下为本地规则回答）\n"
        "今日：订单 %s 单，营业额 ¥%s，客单价 ¥%s；本周累计营业额 ¥%s。"
        "如需更智能的分析，请在后台「系统设置」中配置 AI 接口。"
        % (today["orders"], "{:,.2f}".format(today["revenue"]),
           "{:,.2f}".format(today["avg_price"]), "{:,.2f}".format(week["revenue"]))
    )
