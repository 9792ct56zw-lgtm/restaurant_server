"""营业班次服务：开门（零钱箱/天气/库存提醒）与结单（系统收款 vs 钱箱实点对账）。"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from . import inventory_service

logger = logging.getLogger("app.day")

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

WEATHER_TEXT = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪",
    95: "雷雨", 96: "雷雨伴冰雹", 99: "雷雨伴冰雹",
}


def d(v):
    return float(v) if isinstance(v, Decimal) else v


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _fetch_weather(city: str) -> dict:
    """Open-Meteo 免费天气接口（无需 Key）：默认按配置坐标直查（山西运城盐湖区，无需城市输入，
    WEATHER_LAT/WEATHER_LON 可覆盖）；未配置坐标时按城市名地理编码兜底；断网/失败降级「天气未获取」。"""
    try:
        lat = settings.weather_lat
        lon = settings.weather_lon
        if lat is None or lon is None:
            geo = httpx.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh", "format": "json"},
                timeout=5.0,
            )
            results = (geo.json() or {}).get("results") or []
            if not results:
                return {"weather": "天气未获取", "weather_detail": "（未找到该城市）"}
            loc = results[0]
            lat, lon = loc["latitude"], loc["longitude"]
        w = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "current_weather": "true"},
            timeout=5.0,
        )
        cur = (w.json() or {}).get("current_weather") or {}
        text = WEATHER_TEXT.get(int(cur.get("weathercode", -1)), "天气未获取")
        temp = cur.get("temperature")
        wind = cur.get("windspeed")
        detail = "气温 %s°C · 风速 %s km/h" % (temp, wind) if temp is not None else ""
        return {"weather": text, "weather_detail": detail}
    except Exception:
        logger.warning("天气获取失败（城市 %s，坐标 %s,%s），降级「天气未获取」",
                       city, settings.weather_lat, settings.weather_lon)
        return {"weather": "天气未获取", "weather_detail": "（网络不可用）"}


def _get_open(db: Session, biz: str):
    return (
        db.query(models.DaySession)
        .filter_by(biz_date=biz, type="open")
        .first()
    )


def _get_close(db: Session, biz: str):
    return (
        db.query(models.DaySession)
        .filter_by(biz_date=biz, type="close")
        .first()
    )


def open_status(db: Session, rec) -> dict:
    """开门页负载：日期/星期、零钱箱、天气、原料库存预警、注意事项。"""
    now = datetime.now()
    warns = inventory_service.warnings(db)
    red = [w for w in warns if w["level"] == "red"]
    yellow = [w for w in warns if w["level"] == "yellow"]
    attentions = []
    attentions.append("零钱箱按 %s 元准备，请当面清点确认" % d(rec.open_change))
    if red:
        attentions.append("库存告急：%s 低于安全线，建议优先补货" % "、".join(w["name"] for w in red[:4]))
    if yellow:
        attentions.append("库存偏低：%s 接近安全线" % "、".join(w["name"] for w in yellow[:4]))
    if rec.weather in ("雨", "小雨", "中雨", "大雨", "阵雨", "雷雨", "雪", "小雪", "中雪", "大雪", "阵雪"):
        attentions.append("今日%s，注意门口防滑与外卖打包保温" % rec.weather)
    attentions.append("营业结束后请到「结单对账」核对钱箱现金与系统收款")
    return {
        "biz_date": rec.biz_date,
        "date_text": now.strftime("%Y-%m-%d"),
        "weekday": WEEKDAYS[now.weekday()],
        "opened_at": rec.opened_at.strftime("%H:%M") if rec.opened_at else None,
        "open_change": d(rec.open_change),
        "city": rec.city or "",
        "weather": rec.weather or "天气未获取",
        "weather_detail": rec.weather_detail or "",
        "attentions": attentions,
        "inventory": warns,
    }


def open_shop(db: Session, change=None, city: str = None) -> dict:
    """开门：登记零钱箱金额（默认 100 元）与天气快照；同日重复开门则更新（幂等）。"""
    biz = today_str()
    change = Decimal(str(change)) if change is not None else settings.open_change_default
    city = (city or "").strip() or settings.weather_city
    w = _fetch_weather(city)
    rec = _get_open(db, biz)
    if rec is None:
        rec = models.DaySession(biz_date=biz, type="open", opened_at=datetime.now())
        db.add(rec)
    rec.open_change = change
    rec.city = city
    rec.weather = w["weather"]
    rec.weather_detail = w["weather_detail"]
    db.commit()
    logger.info("开门登记 %s 零钱箱 %.2f 天气 %s%s", biz, change,
                w["weather"], (" · " + w["weather_detail"]) if w["weather_detail"] else "")
    return open_status(db, rec)


def _live_aggregates(db: Session, biz: str) -> dict:
    """按当日已结账订单汇总系统收款：现金/微信/支付宝实收 + 免单金额（按应收计）。"""
    start = datetime.strptime(biz, "%Y-%m-%d")
    end = start + timedelta(days=1)
    rows = (
        db.query(models.Order)
        .filter(models.Order.pay_status == models.PAY_PAID,
                models.Order.paid_at >= start,
                models.Order.paid_at < end)
        .all()
    )
    agg = {
        "sys_cash": Decimal("0"), "sys_wechat": Decimal("0"),
        "sys_alipay": Decimal("0"), "free_amount": Decimal("0"),
        "order_count": len(rows),
    }
    for o in rows:
        if o.pay_method == "free":
            agg["free_amount"] += o.receivable
            continue
        amt = o.paid_amount if o.paid_amount is not None else o.receivable
        key = {"cash": "sys_cash", "wechat": "sys_wechat", "alipay": "sys_alipay"}.get(o.pay_method)
        if key:
            agg[key] += amt
    agg["sys_total"] = agg["sys_cash"] + agg["sys_wechat"] + agg["sys_alipay"]
    return agg


def _close_dict(rec) -> dict:
    return {
        "biz_date": rec.biz_date,
        "closed_at": rec.closed_at.strftime("%m-%d %H:%M") if rec.closed_at else None,
        "open_change": d(rec.open_change),
        "sys_cash": d(rec.sys_cash),
        "sys_wechat": d(rec.sys_wechat),
        "sys_alipay": d(rec.sys_alipay),
        "sys_total": d(rec.sys_total),
        "free_amount": d(rec.free_amount),
        "order_count": rec.order_count or 0,
        "cash_box": d(rec.cash_box) if rec.cash_box is not None else None,
        "diff": d(rec.diff),
        "note": rec.note or "",
    }


def history(db: Session, days: int = 7) -> list:
    """近 N 日结单记录（钱箱统计）。"""
    start = datetime.strptime(today_str(), "%Y-%m-%d") - timedelta(days=days - 1)
    rows = (
        db.query(models.DaySession)
        .filter(models.DaySession.type == "close",
                models.DaySession.created_at >= start)
        .order_by(models.DaySession.biz_date.desc())
        .all()
    )
    return [_close_dict(r) for r in rows]


def close_shop(db: Session, cash_box, note: str = "") -> dict:
    """结单（营业结束）：现金实点输入，系统按收款记录自动汇总，差额 = 钱箱 − (开门零钱 + 系统现金)。
    每日一次：同日重复结单为更新对账（幂等）。"""
    biz = today_str()
    cash_box = Decimal(str(cash_box))
    agg = _live_aggregates(db, biz)
    open_rec = _get_open(db, biz)
    open_change = open_rec.open_change if open_rec else settings.open_change_default
    diff = cash_box - open_change - agg["sys_cash"]
    rec = _get_close(db, biz)
    if rec is None:
        rec = models.DaySession(
            biz_date=biz, type="close",
            opened_at=open_rec.opened_at if open_rec else None,
            closed_at=datetime.now(),
        )
        db.add(rec)
    rec.open_change = open_change
    rec.sys_cash = agg["sys_cash"]
    rec.sys_wechat = agg["sys_wechat"]
    rec.sys_alipay = agg["sys_alipay"]
    rec.sys_total = agg["sys_total"]
    rec.free_amount = agg["free_amount"]
    rec.order_count = agg["order_count"]
    rec.cash_box = cash_box
    rec.diff = diff
    rec.note = (note or "").strip()[:255]
    db.commit()
    logger.info("结单对账 %s 钱箱 %.2f 系统现金 %.2f 差额 %.2f 订单 %d 营业额 %.2f",
                biz, cash_box, agg["sys_cash"], diff, agg["order_count"], agg["sys_total"])
    return {
        "closed": _close_dict(rec),
        "expected_cash_box": d(open_change + agg["sys_cash"]),  # 期望钱箱现金：开门零钱 + 系统现金实收
        "history": history(db, 7),
    }


def day_status(db: Session) -> dict:
    """营业状态：开门信息（未开门则空）+ 结单信息（未结单则空）+ 实时系统收款汇总 + 近 7 日钱箱统计。"""
    biz = today_str()
    open_rec = _get_open(db, biz)
    close_rec = _get_close(db, biz)
    agg = _live_aggregates(db, biz)
    open_change = open_rec.open_change if open_rec else settings.open_change_default
    live = {
        "open_change": d(open_change),
        "sys_cash": d(agg["sys_cash"]),
        "sys_wechat": d(agg["sys_wechat"]),
        "sys_alipay": d(agg["sys_alipay"]),
        "sys_total": d(agg["sys_total"]),
        "free_amount": d(agg["free_amount"]),
        "order_count": agg["order_count"],
    }
    return {
        "biz_date": biz,
        "opened": open_status(db, open_rec) if open_rec else None,
        "closed": _close_dict(close_rec) if close_rec else None,
        "live": live,
        "expected_cash_box": d(open_change + agg["sys_cash"]),
        "history": history(db, 7),
    }
