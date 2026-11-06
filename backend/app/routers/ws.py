"""WebSocket 实时推送：waiter（服务员端/工作台）、kds（后厨大屏）、order:{id}（顾客端进度）。"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import models
from ..database import SessionLocal
from ..security import decode_token
from ..ws_hub import hub

logger = logging.getLogger("app.ws")

router = APIRouter(tags=["ws"])

WAITER_CHANNELS = ("waiter",)
KDS_CHANNELS = ("kds",)


def _check_staff(token: str, roles) -> bool:
    try:
        payload = decode_token(token)
        if payload.get("type") != "staff":
            return False
        db = SessionLocal()
        try:
            staff = db.get(models.Staff, int(payload["sub"]))
            return bool(staff and staff.status == "active" and staff.role in roles)
        finally:
            db.close()
    except Exception:
        return False


@router.websocket("/ws/{channel}")
async def ws_endpoint(websocket: WebSocket, channel: str):
    token = websocket.query_params.get("token") or ""
    client = websocket.client.host if websocket.client else "-"
    if channel in WAITER_CHANNELS:
        if not _check_staff(token, (models.ROLE_WAITER, models.ROLE_ADMIN, models.ROLE_CASHIER)):
            logger.warning("WS 鉴权失败 channel=%s client=%s", channel, client)
            await websocket.close(code=4401)
            return
    elif channel in KDS_CHANNELS:
        if not _check_staff(token, (models.ROLE_KITCHEN, models.ROLE_ADMIN)):
            logger.warning("WS 鉴权失败 channel=%s client=%s", channel, client)
            await websocket.close(code=4401)
            return
    elif channel.startswith("order:"):
        pass  # 顾客端进度频道：公开订阅（订单号本身即凭证）
    else:
        await websocket.close(code=4404)
        return

    await hub.connect(channel, websocket)
    logger.info("WS 连接 channel=%s client=%s", channel, client)
    try:
        while True:
            await websocket.receive_text()  # 心跳保活
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS 异常断开 channel=%s client=%s", channel, client)
    finally:
        hub.disconnect(channel, websocket)
        logger.info("WS 断开 channel=%s client=%s", channel, client)
