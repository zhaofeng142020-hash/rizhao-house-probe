"""管理员口令与运行期配置。

设计目标（对应用户要求「网站只有我可以管理，其他人不能随便改动」）：
- 全部写操作（POST/PUT/PATCH/DELETE）都要口令；
- 只读接口（GET 列表、详情、图表）不需要口令，发给家人朋友也能看；
- 口令优先级：环境变量 ADMIN_TOKEN > 数据库 app_setting > 首次自动生成；
- 自动生成的口令同时写 data/admin_token.txt 和启动日志，方便第一次拿到。

安全口径：这是「单人自用工具」级别的保护，不是多租户账号体系。
口令用 hmac.compare_digest 做常量时间比较，避免时序侧信道；
密钥类配置（企微 webhook）回显时只给尾号，不落前端 localStorage。
"""
from __future__ import annotations

import hmac
import logging
import secrets
from pathlib import Path

from ..config import DATA_DIR, settings
from ..db import get_conn, query_all, query_one

logger = logging.getLogger("rzprobe.auth")

TOKEN_KEY = "admin_token"
TOKEN_FILE: Path = DATA_DIR / "admin_token.txt"

# 不需要口令的写接口：登录本身，以及健康探活
WRITE_WHITELIST = {"/api/admin/login"}


# --------------------------------------------------------------------------
# app_setting 读写（运行期配置，管理台可改，无需重启）
# --------------------------------------------------------------------------
def get_setting(key: str, default: str | None = None) -> str | None:
    try:
        row = query_one("SELECT value FROM app_setting WHERE key=?", (key,))
    except Exception:  # noqa: BLE001  建表前调用不应炸掉整个启动流程
        return default
    if not row or row.get("value") in (None, ""):
        return default
    return str(row["value"])


def set_setting(key: str, value: str, is_secret: int = 0) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_setting (key, value, is_secret) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "is_secret=excluded.is_secret, updated_at=datetime('now','localtime')",
            (key, value, is_secret),
        )


def list_settings() -> dict[str, dict]:
    try:
        rows = query_all("SELECT key, value, is_secret, updated_at FROM app_setting")
    except Exception:  # noqa: BLE001
        return {}
    return {r["key"]: r for r in rows}


def mask_secret(value: str | None) -> str:
    """密钥脱敏：只保留尾部 4-6 位，足够确认「配没配对」而不泄露完整值。"""
    if not value:
        return ""
    value = str(value)
    if len(value) <= 8:
        return "•" * len(value)
    tail = value[-6:]
    return f"{'•' * 8}{tail}"


# --------------------------------------------------------------------------
# 口令
# --------------------------------------------------------------------------
def ensure_admin_token() -> str:
    """保证存在一个口令；返回当前生效的口令（供日志/文件提示用）。"""
    if settings.ADMIN_TOKEN:
        return settings.ADMIN_TOKEN
    existing = get_setting(TOKEN_KEY)
    if existing:
        return existing

    token = secrets.token_hex(8).upper()          # 16 位十六进制，方便手输
    try:
        set_setting(TOKEN_KEY, token, is_secret=1)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
        logger.warning(
            "已自动生成管理员口令：%s\n"
            "  已写入 %s；也可在 .env 里设 ADMIN_TOKEN=你的口令 固定下来。\n"
            "  没有这个口令只能查看，不能修改任何数据。",
            token, TOKEN_FILE,
        )
    except Exception:  # noqa: BLE001
        logger.exception("管理员口令写入失败，本次运行仅允许只读")
        return token
    return token


def current_token() -> str:
    return settings.ADMIN_TOKEN or get_setting(TOKEN_KEY) or ""


def verify_token(token: str | None) -> bool:
    """常量时间比较，避免通过响应时间猜口令。"""
    expected = current_token()
    if not expected or not token:
        return False
    return hmac.compare_digest(str(token).strip(), str(expected))


def admin_required() -> bool:
    return bool(settings.ADMIN_ENABLED)


# --------------------------------------------------------------------------
# 运行期配置生效：把 app_setting 的值覆盖到 settings 实例上
# --------------------------------------------------------------------------
# 这样管理台保存后立刻生效，不用改 .env、不用重启，
# 而 .env 仍然是「冷启动默认值」，两边不冲突。
SETTING_TO_ATTR: dict[str, str] = {
    "wecom_webhook": "WECOM_WEBHOOK",
    "wecom_corp_id": "WECOM_CORP_ID",
    "wecom_agent_secret": "WECOM_AGENT_SECRET",
    "wecom_agent_id": "WECOM_AGENT_ID",
    "wecom_to_user": "WECOM_TO_USER",
    "mp_appid": "MP_APPID",
    "mp_secret": "MP_SECRET",
    "mp_template_id": "MP_TEMPLATE_ID",
    "mp_openid": "MP_OPENID",
    "amap_key": "AMAP_KEY",
    "amap_security_code": "AMAP_SECURITY_CODE",
}


def apply_runtime_settings() -> dict[str, str]:
    """把数据库里的运行期配置写回 settings；返回实际生效的键值（已脱敏）。"""
    applied: dict[str, str] = {}
    for key, attr in SETTING_TO_ATTR.items():
        value = get_setting(key)
        if value:                                  # 空值不覆盖 .env 的默认
            setattr(settings, attr, value)
            applied[key] = mask_secret(value)
    return applied


def save_integration(values: dict[str, str]) -> dict[str, str]:
    """保存一批集成配置（None 表示不变，空串表示清除）。"""
    secrets = {f["key"]: f["secret"] for f in INTEGRATION_FIELDS}
    for key, value in values.items():
        if key not in SETTING_TO_ATTR or value is None:
            continue
        if str(value).strip() == "":
            with get_conn() as conn:
                conn.execute("DELETE FROM app_setting WHERE key=?", (key,))
            setattr(settings, SETTING_TO_ATTR[key], "")
        else:
            set_setting(key, str(value).strip(), is_secret=1 if secrets.get(key) else 0)
    return apply_runtime_settings()


def integration_snapshot() -> dict:
    """给管理台的回显：密钥脱敏 + 是否已配置。"""
    items = []
    for field in INTEGRATION_FIELDS:
        stored = get_setting(field["key"])
        env_value = getattr(settings, SETTING_TO_ATTR[field["key"]], "")
        effective = stored or env_value or ""
        items.append({
            "key": field["key"],
            "group": field.get("group", "wecom"),
            "label": field["label"],
            "secret": field["secret"],
            "placeholder": field["placeholder"],
            "help": field["help"],
            "configured": bool(effective),
            "from": "管理台" if stored else ("环境变量" if env_value else ""),
            "display": mask_secret(effective) if field["secret"] else (effective or ""),
        })
    return {
        "items": items,
        "webhook_ready": bool(get_setting("wecom_webhook") or settings.WECOM_WEBHOOK),
        "map_ready": bool(get_setting("amap_key") or settings.AMAP_KEY),
    }



def check_write(method: str, path: str, token: str | None) -> tuple[bool, str]:
    """判断一次写请求是否放行。返回 (是否放行, 拒绝原因)。"""
    if not admin_required():
        return True, ""
    if path in WRITE_WHITELIST:
        return True, ""
    if verify_token(token):
        return True, ""
    return False, "需要管理员口令：请到「管理台」登录后再操作"


# --------------------------------------------------------------------------
# 运行期配置项定义（管理台表单据此渲染，避免前后端各写一份）
# --------------------------------------------------------------------------
INTEGRATION_FIELDS: list[dict] = [
    {
        "key": "wecom_webhook",
        "label": "企业微信 · 群机器人 Webhook",
        "secret": True,
        "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx",
        "help": "企微内部群 → 右上角「…」→「消息推送」（老版本叫「群机器人」）→ 添加 → 复制 Webhook 地址。整条地址要粘全（含 key=）。保存即生效，不用重启。",
    },
    {
        "key": "wecom_corp_id",
        "label": "企业微信 · 企业 ID（CorpID）",
        "secret": False,
        "placeholder": "wwxxxxxxxxxxxxxxxx",
        "help": "仅「企微应用」通道需要，群机器人通道留空即可。",
    },
    {
        "key": "wecom_agent_secret",
        "label": "企业微信 · 应用 Secret",
        "secret": True,
        "placeholder": "应用管理 → 你的应用 → Secret",
        "help": "同上，仅企微应用通道需要。",
    },
    {
        "key": "wecom_agent_id",
        "label": "企业微信 · 应用 AgentId",
        "secret": False,
        "placeholder": "1000002",
        "help": "应用详情页里的 AgentId。",
    },
    {
        "key": "wecom_to_user",
        "label": "企业微信 · 接收人",
        "secret": False,
        "placeholder": "@all",
        "help": "企微应用通道的接收人，多个用 | 分隔；@all 表示应用可见范围内全部。",
    },
    # ---- 地图底图（可选；不配也能用，走示意底图） ----
    {
        "key": "amap_key",
        "group": "map",
        "label": "高德地图 · Web端(JS API) Key",
        "secret": False,
        "placeholder": "32 位字符，形如 1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p",
        "help": "高德开放平台 console.amap.com → 应用管理 → 我的应用 → 创建新应用 → 添加 Key，"
                "「服务平台」必须选 Web端(JS API)。填完保存，刷新页面即生效。",
    },
    {
        "key": "amap_security_code",
        "group": "map",
        "label": "高德地图 · 安全密钥（jscode）",
        "secret": True,
        "placeholder": "与 Key 在同一页下方「安全密钥」处获取",
        "help": "2021-12 之后申请的 JS API Key 必须配套安全密钥，否则地图报 INVALID_USER_SCODE 白屏。"
                "如果你在控制台里选了「代理服务器」方式，这里留空。",
    },
]
