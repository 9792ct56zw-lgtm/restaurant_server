"""开发启动：python run.py [端口] [监听地址]。默认 127.0.0.1:8001；
真机联调可 python run.py 8001 0.0.0.0（需放行防火墙）。"""
import sys

# Windows 下无论控制台编码如何都输出 UTF-8，避免中文乱码/报错（无需 PYTHONUTF8=1）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import uvicorn

if __name__ == "__main__":
    port, host = 8001, "127.0.0.1"
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    if len(sys.argv) > 2:
        host = sys.argv[2]
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
