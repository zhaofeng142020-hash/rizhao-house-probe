"""全局配置：只用标准库读取 .env，避免强依赖 python-dotenv。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（backend 的上一层）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.getenv("RZ_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    """把项目根目录下的 .env 读进环境变量（已存在的环境变量优先，不覆盖）。"""
    for env_path in (BASE_DIR / ".env", BASE_DIR.parent / ".env"):
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """运行期配置，全部来自环境变量（.env）。"""

    DB_PATH: Path = Path(os.getenv("RZ_DB_PATH", DATA_DIR / "rizhao.db"))

    # ---- 微信推送：通道优先级 企业微信Webhook > 企业微信应用 > 公众号模板 > 日志降级 ----
    WECOM_WEBHOOK: str = os.getenv("WECOM_WEBHOOK", "")           # 群机器人 Webhook 完整地址
    WECOM_CORP_ID: str = os.getenv("WECOM_CORP_ID", "")
    WECOM_AGENT_SECRET: str = os.getenv("WECOM_AGENT_SECRET", "")
    WECOM_AGENT_ID: str = os.getenv("WECOM_AGENT_ID", "")
    WECOM_TO_USER: str = os.getenv("WECOM_TO_USER", "@all")       # 应用消息接收人

    MP_APPID: str = os.getenv("MP_APPID", "")                     # 公众号 appid
    MP_SECRET: str = os.getenv("MP_SECRET", "")
    MP_TEMPLATE_ID: str = os.getenv("MP_TEMPLATE_ID", "")
    MP_OPENID: str = os.getenv("MP_OPENID", "")                   # 已授权用户 openid（可逗号分隔）
    MP_TEMPLATE_KEYS: str = os.getenv(
        "MP_TEMPLATE_KEYS",
        "first,keyword1,keyword2,keyword3,keyword4,remark",        # 模板字段名，按你的模板改
    )

    # ---- 兜底通道 ----
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "465"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    MAIL_TO: str = os.getenv("MAIL_TO", "")

    # ---- 抓取约束 ----
    CRAWL_MIN_INTERVAL: float = float(os.getenv("CRAWL_MIN_INTERVAL", "3.0"))  # 单站请求间隔（秒）
    CRAWL_TIMEOUT: float = float(os.getenv("CRAWL_TIMEOUT", "15"))
    CRAWL_ENABLED: bool = _bool("CRAWL_ENABLED", False)            # 默认关闭真实抓取，演示数据优先
    CRAWL_UA: str = os.getenv(
        "CRAWL_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 RZProbe/1.0 (+contact: local)",
    )

    # ---- 调度 ----
    ENABLE_SCHEDULER: bool = _bool("ENABLE_SCHEDULER", True)
    DAILY_RUN_AT: str = os.getenv("DAILY_RUN_AT", "02:30")         # 每日增量计算时间
    # 每天刷新真实房源的时间点（逗号分隔，支持多个）。每次刷新后自动重算评分
    LIVE_REFRESH_ENABLED: bool = _bool("LIVE_REFRESH_ENABLED", True)
    REFRESH_AT: str = os.getenv("REFRESH_AT", "07:30,19:30")
    REFRESH_PAGES: int = int(os.getenv("REFRESH_PAGES", "5"))      # 每个源抓几页
    TZ: str = os.getenv("TZ", "Asia/Shanghai")

    # ---- 管理权限（只有持口令的人能改数据/改配置，其他人只读） ----
    # 留空则首次启动自动生成一个随机口令，写入 data/admin_token.txt 并打印到日志
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "")
    ADMIN_ENABLED: bool = _bool("ADMIN_ENABLED", True)

    # ---- 购房成本测算默认值（可被 user_pref / 管理台覆盖） ----
    DOWN_RATIO: float = float(os.getenv("DOWN_RATIO", "0.30"))      # 首付比例
    LOAN_YEARS: int = int(os.getenv("LOAN_YEARS", "30"))            # 贷款年限
    LOAN_RATE: float = float(os.getenv("LOAN_RATE", "0.031"))       # 年利率
    AGENT_FEE_RATIO: float = float(os.getenv("AGENT_FEE_RATIO", "0.02"))   # 二手中介费
    NEW_DECOR_COST: float = float(os.getenv("NEW_DECOR_COST", "1200"))    # 毛坯新房装修成本 元/㎡

    # ---- 重点关注面积段（默认 120-150 改善三居/四居） ----
    TARGET_AREA_MIN: float = float(os.getenv("TARGET_AREA_MIN", "120"))
    TARGET_AREA_MAX: float = float(os.getenv("TARGET_AREA_MAX", "150"))

    # ---- 得房率参考（建筑面积 → 套内面积），用于新房「调整后倒挂」 ----
    NEW_EFFICIENCY: float = float(os.getenv("NEW_EFFICIENCY", "0.78"))    # 新盘公摊大
    USED_EFFICIENCY: float = float(os.getenv("USED_EFFICIENCY", "0.82"))  # 二手多层/小高层

    # ---- 前端 ----
    AMAP_KEY: str = os.getenv("AMAP_KEY", "")                      # 高德地图 Web端 JS API Key（留空则用替代视图）
    AMAP_SECURITY_CODE: str = os.getenv("AMAP_SECURITY_CODE", "")  # JS API 2.0 安全密钥 jscode（2021-12 后申请必配）
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

    # ---- 站点对外地址（企微/站内通知里生成「一键恢复」深链用；留空则只写页面名） ----
    SITE_BASE_URL: str = os.getenv("SITE_BASE_URL", "").rstrip("/")

    # ---- 评分模型可调参数 ----
    SCORE_MIN_SAMPLE: int = int(os.getenv("SCORE_MIN_SAMPLE", "3"))    # 可比样本低于此值降级置信度
    DEEP_DIP_PCT: float = float(os.getenv("DEEP_DIP_PCT", "0.12"))     # 深跌阈值 12%
    SUDDEN_DROP_PCT: float = float(os.getenv("SUDDEN_DROP_PCT", "0.05"))  # 单次降价阈值 5%
    LONG_LISTED_DAYS: int = int(os.getenv("LONG_LISTED_DAYS", "180"))  # 久挂阈值
    AUCTION_NET_DISCOUNT: float = float(os.getenv("AUCTION_NET_DISCOUNT", "0.15"))  # 法拍净折扣阈值


settings = Settings()
