"""应用入口：FastAPI 单体后端（REST + WebSocket + 定时任务 + 前端静态托管）。"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401 确保建表前加载全部模型
from . import ws_hub
from .database import Base, engine, ensure_schema
from .logging_setup import setup_logging
from .routers import admin, ai, customer, kds, payment, staff, waiter, ws
from .scheduler import start_scheduler, stop_scheduler
from .security import decode_token

setup_logging()
logger = logging.getLogger("app")
access_logger = logging.getLogger("app.access")
error_logger = logging.getLogger("app.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("服务启动：餐厅扫码点餐系统 API v%s（工作目录 %s）", app.version, Path.cwd())
    ws_hub.set_main_loop(asyncio.get_running_loop())
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("服务停止")


Base.metadata.create_all(bind=engine)
ensure_schema()  # 轻量迁移：老库补新列（如 dish.emoji）

app = FastAPI(title="餐厅扫码点餐系统 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _client_identity(request: Request) -> str:
    """尽力解析调用者身份：staff:3 / customer:7 / bad-token / anon。"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return "anon"
    try:
        payload = decode_token(auth[7:])
        return "%s:%s" % (payload.get("type", "?"), payload.get("sub", "?"))
    except Exception:
        return "bad-token"


@app.middleware("http")
async def access_log(request: Request, call_next):
    """请求/访问日志：IP、身份、方法、路径、状态码、耗时；4xx 警示、5xx 错误、>1s 慢请求标注。"""
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        cost_ms = (time.perf_counter() - start) * 1000
        ip = request.client.host if request.client else "-"
        path = request.url.path + (("?" + request.url.query) if request.url.query else "")
        msg = "%s %s %s %s -> %d %.0fms" % (
            ip, _client_identity(request), request.method, path, status, cost_ms)
        if status >= 500:
            access_logger.error(msg)
        elif status >= 400:
            access_logger.warning(msg)
        elif cost_ms > 1000:
            access_logger.warning(msg + " [slow]")
        else:
            access_logger.info(msg)


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    """参数校验失败：记录错误摘要（默认响应格式保持不变）。"""
    access_logger.warning("参数校验失败 %s %s: %s", request.method, request.url.path,
                          str(exc.errors())[:300])
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    """全局兜底：未处理异常记录完整堆栈，对外统一返回 500（不泄露内部细节）。"""
    error_logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})

app.include_router(customer.router)
app.include_router(waiter.router)
app.include_router(kds.router)
app.include_router(admin.router)
app.include_router(ai.router)
app.include_router(staff.router)
app.include_router(payment.router)
app.include_router(ws.router)

# 前端静态托管：web/{waiter,kds,admin}/ 三个免构建页面 + vendor 依赖
_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")


@app.get("/")
def root():
    """访问根路径直接进入服务员端（含各端入口链接）。"""
    return RedirectResponse("/web/waiter/index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "restaurant-api", "version": app.version}
