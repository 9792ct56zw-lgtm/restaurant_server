"""全局配置：全部可从环境变量覆盖，开发期零配置可跑（SQLite + 进程内广播 + 模拟登录）。"""
import os
from decimal import Decimal


class Settings:
    def __init__(self):
        self.app_name = "餐厅扫码点餐系统"
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./restaurant.db")
        self.redis_url = os.getenv("REDIS_URL", "").strip()
        self.jwt_secret = os.getenv("JWT_SECRET", "dev-secret-change-me")
        self.jwt_expire_minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))

        # 微信小程序
        self.wx_appid = os.getenv("WX_APPID", "").strip()
        self.wx_secret = os.getenv("WX_SECRET", "").strip()
        self.wx_login_mock = os.getenv("WX_LOGIN_MOCK", "1") == "1"

        # AI（OpenAI 兼容接口，DeepSeek/通义/智谱均可）
        self.ai_base_url = os.getenv("AI_BASE_URL", "").strip()
        self.ai_api_key = os.getenv("AI_API_KEY", "").strip()
        self.ai_model = os.getenv("AI_MODEL", "deepseek-chat")

        # 库存预警：≤安全线红色，≤安全线×1.3 黄色
        self.warn_yellow_ratio = 1.3
        # KDS 制作超时（分钟）
        self.kds_timeout_minutes = int(os.getenv("KDS_TIMEOUT_MINUTES", "15"))

        # 营业开门：零钱箱默认金额 / 天气（固定查询山西运城盐湖区，坐标直查 Open-Meteo 免费接口，断网降级）
        self.open_change_default = Decimal(os.getenv("OPEN_CHANGE_DEFAULT", "100"))
        self.weather_city = os.getenv("WEATHER_CITY", "山西运城盐湖区").strip() or "山西运城盐湖区"
        self.weather_lat = float(os.getenv("WEATHER_LAT", "35.03"))
        self.weather_lon = float(os.getenv("WEATHER_LON", "110.99"))

        # 阿里云 OSS 图片存储（bucket 保持 private，上传对象级 public-read；未配置时上传接口 503，其余功能不受影响）
        self.oss_access_key_id = os.getenv("OSS_ACCESS_KEY_ID", "").strip()
        self.oss_access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
        self.oss_bucket = os.getenv("OSS_BUCKET", "").strip()
        self.oss_endpoint = os.getenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com").strip()
        # 图片公开访问前缀，如 https://xpt123.oss-cn-hangzhou.aliyuncs.com；缺省按 endpoint+bucket 拼接
        self.oss_public_base = os.getenv("OSS_PUBLIC_BASE", "").strip()


settings = Settings()
