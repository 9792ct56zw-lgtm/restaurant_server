"""统一日志系统：控制台 + 滚动文件（backend/logs/app.log，10MB×5 约 50MB 上限）。

- 启动时由 app.main 调用 setup_logging()，各模块用 logging.getLogger(__name__) 取 logger
- 服务器上 stdout 由 systemd 收进 journal（journalctl -u restaurant），文件日志可直接 tail
- 访问日志见 app.access（main.py 中间件），业务日志 app.order / app.day / app.ws / app.oss 等
- 日志目录可用环境变量 LOG_DIR 覆盖；目录不可写时降级为仅控制台输出
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = Path(os.getenv("LOG_DIR", str(BACKEND_DIR / "logs")))

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根日志器（幂等）：格式、控制台 handler、滚动文件 handler。"""
    global _configured
    if _configured:
        return
    _configured = True

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)

    # 控制台输出（systemd journal 会收集 stdout/stderr）
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 滚动文件：backend/logs/app.log，单个 10MB，保留 5 个备份
    try:
        DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(DEFAULT_LOG_DIR / "app.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).warning("日志目录不可写，仅输出到控制台：%s", DEFAULT_LOG_DIR)

    # 降噪：uvicorn 自带 access 日志与我们的请求日志重复；httpx 客户端 debug 太吵；
    # apscheduler 每次启动打印大量 "Added job" 无意义
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
