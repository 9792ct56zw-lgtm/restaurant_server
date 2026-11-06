"""实时推送中心：进程内频道广播；配置 REDIS_URL 时自动桥接（多进程部署用）。"""
import asyncio
import json
from typing import Dict, Set

from fastapi import WebSocket

from .config import settings

_main_loop = None


def set_main_loop(loop):
    global _main_loop
    _main_loop = loop


def emit_sync(channel: str, data: dict):
    """线程安全广播：同步路由（线程池）中调用，把广播调度回主事件循环。"""
    global _main_loop
    if _main_loop is None or _main_loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(hub.broadcast(channel, data), _main_loop)
    except Exception:
        pass


class Hub:
    def __init__(self):
        self.channels: Dict[str, Set[WebSocket]] = {}
        self._redis = None
        self._sub = None
        self._relay_task = None
        if settings.redis_url:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(settings.redis_url)
            except Exception:
                self._redis = None

    async def connect(self, channel: str, ws: WebSocket):
        await ws.accept()
        self.channels.setdefault(channel, set()).add(ws)
        if self._redis and self._sub is None:
            try:
                self._sub = self._redis.pubsub()
                await self._sub.subscribe("rt_events")
                self._relay_task = asyncio.get_event_loop().create_task(self._relay())
            except Exception:
                self._sub = None

    def disconnect(self, channel: str, ws: WebSocket):
        group = self.channels.get(channel)
        if group and ws in group:
            group.discard(ws)

    async def broadcast(self, channel: str, data: dict):
        text = json.dumps(data, ensure_ascii=False, default=str)
        if self._redis:
            try:
                await self._redis.publish(
                    "rt_events",
                    json.dumps({"channel": channel, "data": data}, ensure_ascii=False, default=str),
                )
            except Exception:
                pass
        for ws in list(self.channels.get(channel, set())):
            try:
                await ws.send_text(text)
            except Exception:
                self.disconnect(channel, ws)

    async def _relay(self):
        while True:
            try:
                msg = await self._sub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    payload = json.loads(msg["data"])
                    text = json.dumps(payload["data"], ensure_ascii=False, default=str)
                    for ws in list(self.channels.get(payload.get("channel"), set())):
                        try:
                            await ws.send_text(text)
                        except Exception:
                            self.disconnect(payload.get("channel"), ws)
            except Exception:
                await asyncio.sleep(1)


hub = Hub()
