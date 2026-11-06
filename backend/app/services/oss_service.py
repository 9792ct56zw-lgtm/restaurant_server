"""阿里云 OSS 图片存储：菜品图片上传（对象级 public-read），返回公开访问 URL。

- bucket 保持 private（列表/其余对象不可匿名访问），仅上传的图片对象带 public-read ACL
- 前提：bucket 需关闭「阻止公共访问」（首次接入时已通过 API 关闭，见 migrate_images_to_oss.py）
- 配置全部来自环境变量（settings）：OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET /
  OSS_BUCKET / OSS_ENDPOINT / OSS_PUBLIC_BASE；密钥只存服务端（deploy/secrets.env + systemd），不进 git
"""
import mimetypes
import os
import uuid

from ..config import settings

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_SIZE = 8 * 1024 * 1024  # 8MB
PREFIX = "dishes/"


def _bucket():
    """按配置构造 oss2.Bucket；未配置返回 None（上传接口据此报 503）。"""
    try:
        import oss2  # 延迟导入：未装 SDK 时其余功能照常
    except ImportError:
        return None
    if not (settings.oss_access_key_id and settings.oss_access_key_secret
            and settings.oss_bucket and settings.oss_endpoint):
        return None
    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    return oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)


def public_url(key: str) -> str:
    if settings.oss_public_base:
        return settings.oss_public_base.rstrip("/") + "/" + key
    host = settings.oss_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    return "https://%s.%s/%s" % (settings.oss_bucket, host, key)


def upload_dish_image(filename: str, data: bytes) -> str:
    """上传一张菜品图到 OSS dishes/ 目录（对象级 public-read），返回公开访问 URL。"""
    bucket = _bucket()
    if bucket is None:
        raise RuntimeError("OSS 未配置：请设置 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET / OSS_BUCKET 环境变量")
    ext = os.path.splitext(filename or "")[1].lower() or ".jpg"
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in ALLOWED_EXT:
        raise ValueError("仅支持 jpg/png/webp/gif 格式图片")
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_SIZE:
        raise ValueError("图片不能超过 8MB")
    content_type = mimetypes.types_map.get(ext, "image/jpeg")
    key = PREFIX + uuid.uuid4().hex + ext
    bucket.put_object(key, data, headers={"Content-Type": content_type,
                                         "x-oss-object-acl": "public-read"})
    return public_url(key)
