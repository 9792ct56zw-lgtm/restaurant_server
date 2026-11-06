"""生成桌台测试二维码（开发调试用）。

内容 = 顾客端 H5 完整 URL（如 http://8.155.129.25/web/customer/index.html?table=A01），
微信「扫一扫」直接在内置浏览器打开网页点餐（小程序尚未注册时期的过渡方案）。
基地址可用环境变量 QR_BASE 覆盖（缺省 8.155.129.25 服务器公网 IP）。
输出：web/qrcodes/{桌号}.png + web/qrcodes.html 可打印展示页（后端静态托管）。

正式上线（小程序注册）后替换为微信小程序码（wxacode，scene=桌号）。
"""
import os
import sys
import sqlite3
from pathlib import Path

import segno

BACKEND_DIR = Path(__file__).resolve().parent
WEB_DIR = BACKEND_DIR.parent / "web"
OUT_DIR = WEB_DIR / "qrcodes"

# 顾客端 H5 基地址：默认服务器公网 IP（HTTP，域名/备案就绪后改 HTTPS 域名）
QR_BASE = os.getenv("QR_BASE", "http://8.155.129.25").rstrip("/")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def table_names():
    db_path = os.getenv("DATABASE_URL", "").replace("sqlite:///", "")
    if not db_path:
        db_path = str(BACKEND_DIR / "restaurant.db")
    if not os.path.exists(db_path):
        return ["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08",
                "B01", "B02", "B04", "B05", "C01", "C02", "C03"]
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM dining_table WHERE enabled=1 ORDER BY name"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def gen_qr(text, path, scale=8, border=2):
    qr = segno.make(text, error="m")
    qr.save(str(path), scale=scale, border=border, dark="#1F2329", light="#FFFFFF")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # QR_TABLES 逗号分隔覆盖（如与服务器实际启用桌台保持一致时用）；缺省读本机库
    if os.getenv("QR_TABLES"):
        names = [n.strip() for n in os.getenv("QR_TABLES").split(",") if n.strip()]
    else:
        names = table_names()
    for n in names:
        url = QR_BASE + "/web/customer/index.html?table=" + n
        gen_qr(url, OUT_DIR / (n + ".png"))

    # 可打印展示页（浏览器打开后直接打印，贴到餐桌上即可测试）
    cards = "".join(
        '<div class="card"><img src="qrcodes/{n}.png" alt="{n}"><div class="no">{n}</div>'
        '<div class="tip">微信扫一扫 → 网页点餐</div></div>'.format(n=n)
        for n in names
    )
    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>桌台二维码 · 测试用（川香居）</title>
<style>
body { font-family:"PingFang SC","Microsoft YaHei",sans-serif; background:#F6F4F1; margin:0; padding:24px; }
h1 { text-align:center; font-size:20px; margin:0 0 4px; }
p.sub { text-align:center; color:#8A94A6; font-size:13px; margin:0 0 20px; }
.grid { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; max-width:960px; margin:0 auto; }
.card { background:#fff; border-radius:14px; padding:18px 10px 14px; text-align:center; box-shadow:0 2px 10px rgba(31,35,41,.06); }
.card img { width:130px; height:130px; }
.card .no { font-size:20px; font-weight:800; margin-top:6px; }
.card .tip { font-size:11.5px; color:#8A94A6; margin-top:2px; }
@media print { body { background:#fff; padding:0; } .card { box-shadow:none; border:1px solid #eee; } }
</style></head><body>
<h1>川香居 · 桌台点餐二维码（测试用）</h1>
<p class="sub">共 __COUNT__ 桌 · 微信「扫一扫」打开网页点餐 · 小程序注册上线后请替换为正式小程序码</p>
<div class="grid">__CARDS__</div>
</body></html>"""
    html = html.replace("__CARDS__", cards).replace("__COUNT__", str(len(names)))
    (WEB_DIR / "qrcodes.html").write_text(html, encoding="utf-8")

    print("基地址: %s" % QR_BASE)
    print("已生成 %d 张桌台二维码 -> %s" % (len(names), OUT_DIR))
    print("可打印展示页 -> %s" % (WEB_DIR / "qrcodes.html"))


if __name__ == "__main__":
    main()

