# -*- coding: utf-8 -*-
"""一次性迁移：web/uploads/dishes/ 菜品图上传到阿里云 OSS，并把 dish.image 相对路径改为 OSS 公开 URL。

前置条件（由 OSS 控制台/API 一次性完成）：
  1. bucket 关闭「阻止公共访问」（首次接入时已通过 API 关闭）
  2. bucket 本身保持 private —— 仅上传的图片对象带 public-read ACL

用法（本地 SQLite 默认库）：
  OSS_ACCESS_KEY_ID=xxx OSS_ACCESS_KEY_SECRET=xxx OSS_BUCKET=xpt123 \\
  OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com \\
  OSS_PUBLIC_BASE=https://xpt123.oss-cn-hangzhou.aliyuncs.com \\
  python migrate_images_to_oss.py

服务器 MySQL 需另传 DATABASE_URL（见 deploy/secrets.env 中 MYSQL_PASSWORD 对应连接串）。
幂等：已是 http(s) 开头的 image 不重复处理；对象存在时覆盖上传（内容一致，无害）。
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oss2  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.normpath(os.path.join(BACKEND_DIR, "..", "web", "uploads", "dishes"))


def main():
    ak = os.getenv("OSS_ACCESS_KEY_ID", "").strip()
    sk = os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
    bucket_name = os.getenv("OSS_BUCKET", "").strip()
    endpoint = os.getenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com").strip()
    public_base = os.getenv("OSS_PUBLIC_BASE", "").strip()
    if not (ak and sk and bucket_name):
        print("[FAIL] 缺少 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET / OSS_BUCKET 环境变量")
        sys.exit(1)

    bucket = oss2.Bucket(oss2.Auth(ak, sk), endpoint, bucket_name)
    if not public_base:
        host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        public_base = "https://%s.%s" % (bucket_name, host)
    public_base = public_base.rstrip("/")

    files = sorted(f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")))
    print("本地图片目录 %s 共 %d 张 -> 上传 OSS dishes/" % (UPLOAD_DIR, len(files)))
    uploaded = 0
    for f in files:
        data = open(os.path.join(UPLOAD_DIR, f), "rb").read()
        bucket.put_object("dishes/" + f, data, headers={"x-oss-object-acl": "public-read"})
        uploaded += 1
        print("  [OK] dishes/%s (%d 字节)" % (f, len(data)))
    print("OSS 上传完成 %d 张" % uploaded)

    db = SessionLocal()
    try:
        rows = db.query(models.Dish).all()
        changed = 0
        for d in rows:
            img = (d.image or "").strip()
            if not img or img.startswith("http"):
                continue
            if img.startswith("/web/uploads/dishes/"):
                fn = img.rsplit("/", 1)[-1]
                if fn in files or os.path.exists(os.path.join(UPLOAD_DIR, fn)):
                    d.image = "%s/dishes/%s" % (public_base, fn)
                    changed += 1
                    print("  [DB] 菜品 %s -> %s" % (d.name, d.image))
        db.commit()
        print("数据库更新完成：%d 道菜品图片改指 OSS" % changed)
    finally:
        db.close()


if __name__ == "__main__":
    main()
