"""密码哈希（stdlib pbkdf2，无额外依赖）与 JWT。"""
import hashlib
import hmac
import os
import time

import jwt

from .config import settings

_ITER = 100_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITER)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_token(subject: str, extra: dict) -> str:
    payload = dict(extra)
    payload.update({"sub": str(subject), "exp": int(time.time()) + settings.jwt_expire_minutes * 60})
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
