"""推送模块（独立、可插拔、可降级）。

通道优先级：
  1. WecomWebhookChannel   企业微信群机器人 Webhook（最轻量，推荐先用这个）
  2. WecomAppChannel       企业微信应用消息（需 corpid / secret / agentid）
  3. MpTemplateChannel     微信公众号模板消息（需 appid / secret / 模板ID / openid）
  4. TelegramChannel       兜底
  5. EmailChannel          兜底
  6. LogChannel            永远可用（写推送日志 + 站内铃铛），未配置微信时不报错

关键行为：
  - 未配置任何微信通道 → 自动降级 LogChannel，status=logged，不抛异常
  - 推送去重：同一房源在 dedup_hours 内不重复推送（除非又降了 ≥3%）
  - 静默时段：非红级消息在 quiet_hours 内不推（红级实时推，符合"红级实时推"要求）
  - 每次推送都写 alert_log，前端站内铃铛直接读这张表
"""
from __future__ import annotations

import json
import logging
import smtplib
import sqlite3
from datetime import date, datetime, time, timedelta
from email.header import Header
from email.mime.text import MIMEText
from typing import Any

from ..config import settings
from ..db import get_conn, query_one
from . import templates

logger = logging.getLogger("rzprobe.alert")

PRIORITY = ["wecom_webhook", "wecom_app", "mp_template", "telegram", "email", "log"]


# =============================================================================
# 通道基类
# =============================================================================
class BaseChannel:
    key = "base"
    label = "基础通道"

    def available(self) -> bool:  # noqa: D401
        return False

    def send(self, msg: dict) -> tuple[bool, str]:  # pragma: no cover - 由子类实现
        raise NotImplementedError


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> tuple[bool, str]:
    """延迟 import requests：没装依赖也不影响模块导入与降级。"""
    try:
        import requests  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return False, "requests 未安装"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        return resp.ok, resp.text[:500]
    except Exception as exc:  # noqa: BLE001  网络异常统一吞掉，走降级
        return False, f"{type(exc).__name__}: {exc}"


def _post_wecom(url: str, payload: dict, timeout: float = 10.0) -> tuple[bool, str]:
    """企业微信 / 公众号接口专用：HTTP 200 不代表发送成功。

    key 写错、机器人被移出群、应用无权限，接口都会返回 200 + errcode!=0。
    只认 errcode==0，否则「测试成功但手机没动静」会把人绕死。
    """
    ok, resp = _post_json(url, payload, timeout)
    if not ok:
        return False, resp
    try:
        data = json.loads(resp)
    except (ValueError, TypeError):
        return True, resp                       # 非 JSON（如网关错误页）交回原文
    code = data.get("errcode")
    if code is None or int(code) == 0:
        return True, resp
    return False, f"errcode={code} {data.get('errmsg', '')}".strip()


def _get_json(url: str, timeout: float = 10.0) -> dict:
    try:
        import requests  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return {}
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.json() if resp.ok else {}
    except Exception:  # noqa: BLE001
        return {}


class WecomWebhookChannel(BaseChannel):
    """企业微信群机器人 Webhook —— 最轻量，一条 URL 搞定。"""

    key = "wecom_webhook"
    label = "企业微信群机器人"

    def available(self) -> bool:
        return bool(settings.WECOM_WEBHOOK)

    def send(self, msg: dict) -> tuple[bool, str]:
        payload = {"msgtype": "markdown", "markdown": {"content": msg.get("markdown") or msg.get("text")}}
        return _post_wecom(settings.WECOM_WEBHOOK, payload)


class WecomAppChannel(BaseChannel):
    """企业微信应用消息（需要 corpid / secret / agentid）。"""

    key = "wecom_app"
    label = "企业微信应用消息"
    TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"

    def available(self) -> bool:
        return bool(settings.WECOM_CORP_ID and settings.WECOM_AGENT_SECRET and settings.WECOM_AGENT_ID)

    def send(self, msg: dict) -> tuple[bool, str]:
        token_data = _get_json(
            f"{self.TOKEN_URL}?corpid={settings.WECOM_CORP_ID}&corpsecret={settings.WECOM_AGENT_SECRET}"
        )
        token = token_data.get("access_token")
        if not token:
            return False, f"获取 access_token 失败: {token_data}"
        payload = {
            "touser": settings.WECOM_TO_USER,
            "msgtype": "markdown",
            "agentid": int(settings.WECOM_AGENT_ID),
            "markdown": {"content": msg.get("markdown") or msg.get("text")},
        }
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        return _post_wecom(url, payload)


class MpTemplateChannel(BaseChannel):
    """微信公众号模板消息（服务号/订阅号需已认证并开通模板消息）。"""

    key = "mp_template"
    label = "微信公众号模板消息"
    TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"

    def available(self) -> bool:
        return bool(settings.MP_APPID and settings.MP_SECRET and settings.MP_TEMPLATE_ID and settings.MP_OPENID)

    def send(self, msg: dict) -> tuple[bool, str]:
        token_data = _get_json(
            f"{self.TOKEN_URL}?grant_type=client_credential&appid={settings.MP_APPID}&secret={settings.MP_SECRET}"
        )
        token = token_data.get("access_token")
        if not token:
            return False, f"获取 access_token 失败: {token_data}"

        keys = [k.strip() for k in settings.MP_TEMPLATE_KEYS.split(",") if k.strip()]
        first = msg.get("title", "")
        text = msg.get("text", "")
        body_lines = [ln for ln in text.splitlines()[1:] if ln.strip()]
        values: dict[str, Any] = {}
        if keys:
            values[keys[0]] = {"value": first[:20]}
            for i, key in enumerate(keys[1:-1], start=1):
                values[key] = {"value": (body_lines[i - 1] if i - 1 < len(body_lines) else "详见站内")[:60]}
            values[keys[-1]] = {"value": templates.DISCLAIMER[:60]}

        results: list[str] = []
        ok_all = True
        url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={token}"
        for openid in [o.strip() for o in settings.MP_OPENID.split(",") if o.strip()]:
            payload = {
                "touser": openid,
                "template_id": settings.MP_TEMPLATE_ID,
                "url": "",
                "data": values,
            }
            ok, resp = _post_wecom(url, payload)
            ok_all = ok_all and ok
            results.append(resp)
        return ok_all, " | ".join(results)[:500]


class TelegramChannel(BaseChannel):
    key = "telegram"
    label = "Telegram"

    def available(self) -> bool:
        return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)

    def send(self, msg: dict) -> tuple[bool, str]:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": settings.TELEGRAM_CHAT_ID, "text": msg.get("text"), "disable_web_page_preview": True}
        return _post_json(url, payload)


class EmailChannel(BaseChannel):
    key = "email"
    label = "邮件"

    def available(self) -> bool:
        return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.MAIL_TO)

    def send(self, msg: dict) -> tuple[bool, str]:
        try:
            mime = MIMEText(msg.get("text", ""), "plain", "utf-8")
            mime["Subject"] = Header(msg.get("title", "日照房价探针"), "utf-8")
            mime["From"] = settings.SMTP_USER
            mime["To"] = settings.MAIL_TO
            if settings.SMTP_PORT == 465:
                server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
                server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, [x.strip() for x in settings.MAIL_TO.split(",")], mime.as_string())
            server.quit()
            return True, "smtp ok"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"


class LogChannel(BaseChannel):
    """降级通道：只写日志 + alert_log（站内铃铛可读到）。永远可用。"""

    key = "log"
    label = "站内提醒（降级）"

    def available(self) -> bool:
        return True

    def send(self, msg: dict) -> tuple[bool, str]:
        logger.warning("[推送降级·站内提醒] %s\n%s", msg.get("title"), msg.get("text"))
        return True, "logged"


CHANNELS: dict[str, BaseChannel] = {
    c.key: c for c in [
        WecomWebhookChannel(), WecomAppChannel(), MpTemplateChannel(),
        TelegramChannel(), EmailChannel(), LogChannel(),
    ]
}


# =============================================================================
# 发送器
# =============================================================================
class AlertSender:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()

    # -------------------- 通道选择 --------------------
    def resolve_channel(self) -> BaseChannel:
        want = (self.config.get("channel") or "auto").strip()
        if want != "auto":
            ch = CHANNELS.get(want)
            if ch and ch.available():
                return ch
            logger.warning("指定通道 %s 不可用，按优先级降级", want)
        for key in PRIORITY:
            ch = CHANNELS[key]
            if ch.available():
                return ch
        return CHANNELS["log"]

    def channel_status(self) -> list[dict]:
        active = self.resolve_channel().key
        return [
            {"key": c.key, "label": c.label, "available": c.available(), "active": c.key == active}
            for c in CHANNELS.values()
        ]

    # -------------------- 阈值 / 静默 / 去重 --------------------
    def should_push(self, grade: str, score: float) -> tuple[bool, str]:
        if not int(self.config.get("enabled", 1)):
            return False, "推送总开关关闭"
        if grade == "red" and not int(self.config.get("push_red", 1)):
            return False, "红级推送已关闭"
        if grade == "yellow" and not int(self.config.get("push_yellow", 1)):
            return False, "黄级推送已关闭"
        if grade == "white":
            return False, "白级仅记录，不主动推送"
        if float(score or 0) < float(self.config.get("min_score", 60)):
            return False, f"分数 {score} 低于最低推送分数 {self.config.get('min_score')}"
        if grade != "red" and self.in_quiet_hours():
            return False, "静默时段，非红级延迟到次日汇总"
        return True, "ok"

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        raw = (self.config.get("quiet_hours") or "").strip()
        if not raw or "-" not in raw:
            return False
        now = now or datetime.now()
        try:
            start_s, end_s = raw.split("-", 1)
            start = time.fromisoformat(start_s.strip())
            end = time.fromisoformat(end_s.strip())
        except ValueError:
            return False
        cur = now.time()
        if start <= end:
            return start <= cur <= end
        return cur >= start or cur <= end

    def is_duplicate(self, target_type: str, target_id: int, grade: str, price: float) -> bool:
        hours = int(self.config.get("dedup_hours", 24) or 24)
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = query_one(
            "SELECT id, message FROM alert_log "
            "WHERE target_type=? AND target_id=? AND created_at>=? "
            "ORDER BY id DESC LIMIT 1",
            (target_type, target_id, since),
        )
        if not row:
            return False
        # 若价格又明显下探（≥3%），允许再次推送
        last = query_one(
            "SELECT total_price FROM listing WHERE id=?" if target_type == "listing"
            else "SELECT start_price AS total_price FROM auction WHERE id=?",
            (target_id,),
        )
        try:
            last_price = float((last or {}).get("total_price") or 0)
        except (TypeError, ValueError):
            last_price = 0.0
        if last_price > 0 and price > 0 and price <= last_price * 0.97:
            return False
        return True

    # -------------------- 发送 --------------------
    def send_message(self, msg: dict, target_type: str, target_id: int,
                     grade: str, price: float, force: bool = False) -> dict:
        ok_pre, reason = (True, "force") if force else self.should_push(grade, msg.get("score", 100))
        channel = self.resolve_channel()

        alert_key = f"{target_type}|{target_id}|{grade}|{int(price)}|{date.today().isoformat()}"
        if not ok_pre:
            self._log_alert(alert_key, target_type, target_id, grade, channel.key,
                            "skipped", msg.get("title", ""), msg.get("text", ""), reason)
            return {"status": "skipped", "reason": reason, "channel": channel.key}

        if not force and self.is_duplicate(target_type, target_id, grade, price):
            self._log_alert(alert_key, target_type, target_id, grade, channel.key,
                            "skipped", msg.get("title", ""), msg.get("text", ""), "24h 内已推送过去重")
            return {"status": "skipped", "reason": "dedup", "channel": channel.key}

        ok, resp = channel.send(msg)
        if not ok and channel.key != "log":
            # 主通道失败 → 立刻降级，不静默丢消息
            fallback = CHANNELS["log"]
            ok2, resp2 = fallback.send(msg)
            self._log_alert(alert_key, target_type, target_id, grade, fallback.key,
                            "logged", msg.get("title", ""), msg.get("text", ""),
                            f"主通道失败({resp})，已降级站内")
            return {"status": "logged", "reason": f"主通道失败已降级: {resp}", "channel": fallback.key}

        status = "sent" if channel.key != "log" else "logged"
        self._log_alert(alert_key, target_type, target_id, grade, channel.key,
                        status, msg.get("title", ""), msg.get("text", ""), resp)
        return {"status": status, "channel": channel.key, "response": resp}

    def push_target(self, target_type: str, item: dict, score: dict) -> dict:
        """给一条房源/法拍推送。score 为 scoring 结果。"""
        if target_type == "auction":
            msg = templates.build_auction_messages(item, score)
        else:
            msg = templates.build_listing_messages(item, score)
        msg["score"] = score.get("score")
        price = float(item.get("total_price") or score.get("landing_price") or 0)
        return self.send_message(msg, target_type, int(item.get("id") or 0), score.get("grade", "white"), price)

    def push_digest(self, entries: list[dict]) -> dict:
        """黄级每日汇总。entries: [{"item":..., "score":...}]"""
        msg = templates.build_digest(entries)
        channel = self.resolve_channel()
        ok, resp = channel.send(msg)
        status = "sent" if ok and channel.key != "log" else ("logged" if ok else "failed")
        self._log_alert(
            f"digest|{date.today().isoformat()}", "digest", 0, "yellow",
            channel.key, status, msg["title"], msg["text"], resp,
        )
        return {"status": status, "channel": channel.key, "count": len(entries)}

    # -------------------- 日志 --------------------
    @staticmethod
    def _log_alert(alert_key: str, target_type: str, target_id: int, grade: str,
                   channel: str, status: str, title: str, message: str, response: str) -> None:
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO alert_log "
                    "(alert_key,target_type,target_id,grade,channel,status,title,message,response) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (alert_key, target_type, target_id, grade, channel, status, title, message, str(response)[:500]),
                )
        except sqlite3.Error as exc:  # 推送失败不能影响主流程
            logger.error("写推送日志失败: %s", exc)


# =============================================================================
# 配置读取 / 更新
# =============================================================================
DEFAULT_CONFIG = {
    "enabled": 1, "channel": "auto", "min_score": 60.0,
    "push_red": 1, "push_yellow": 1, "red_realtime": 1, "yellow_daily": 1,
    "quiet_hours": "23:00-07:00", "dedup_hours": 24,
}


def load_config() -> dict:
    try:
        row = query_one("SELECT * FROM alert_config WHERE id=1")
    except sqlite3.Error:
        return dict(DEFAULT_CONFIG)
    if not row:
        return dict(DEFAULT_CONFIG)
    return {**DEFAULT_CONFIG, **row}


def save_config(patch: dict) -> dict:
    cfg = {**load_config(), **{k: v for k, v in patch.items() if v is not None}}
    keep = ["enabled", "channel", "min_score", "push_red", "push_yellow",
            "red_realtime", "yellow_daily", "quiet_hours", "dedup_hours"]
    values = [cfg.get(k) for k in keep]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE alert_config SET {', '.join(f'{k}=?' for k in keep)}, "
            f"updated_at=datetime('now','localtime') WHERE id=1",
            values,
        )
    return load_config()


def test_channel() -> dict:
    """推送设置页的「发送测试」按钮。"""
    sender = AlertSender()
    msg = {
        "title": "【日照房价探针·捡漏雷达】通道测试",
        "text": "这是一条通道连通性测试消息。收到即表示推送配置可用。\n" + templates.DISCLAIMER,
        "markdown": "### ✅ 通道测试成功\n日照房价探针·捡漏雷达已联通。\n" + templates.DISCLAIMER,
        "score": 100,
    }
    channel = sender.resolve_channel()
    ok, resp = channel.send(msg)
    sender._log_alert(
        f"test|{datetime.now().isoformat(timespec='seconds')}", "test", 0, "yellow",
        channel.key, "sent" if ok else "failed", msg["title"], msg["text"], resp,
    )
    return {
        "ok": ok,
        "channel": channel.key,
        "channel_label": channel.label,
        "response": str(resp)[:300],
        "hint": "" if ok else _explain_error(str(resp)),
    }


# 企微/公众号最常见的几个报错，翻译成人话——配置时八成会撞上其中一条
ERROR_HINTS = {
    "93000": "Webhook 地址无效：key 复制不全，或机器人已被移出群 / 已重置链接。回群里重新取一次地址。",
    "40001": "Secret 不对或已过期：到企微管理后台「应用管理」里重置 Secret 后重新填。",
    "40013": "CorpID 不对：填的是「我的企业」页最下面的企业 ID，不是企业名称。",
    "60020": "来源 IP 不在可信 IP 列表：企微管理后台 → 应用详情 → 企业可信 IP，加上服务器出口 IP。",
    "81013": "接收人无效：检查接收人填的是企微账号（不是手机号/昵称），或直接填 @all。",
    "45009": "接口调用超过频率限制：群机器人每分钟上限 20 条，等一分钟再试。",
    "40058": "参数不合法：多为 AgentId 填成了字符串以外的内容，或 agentid 与 secret 不是同一个应用。",
}


def _explain_error(resp: str) -> str:
    import re  # noqa: PLC0415

    for code in re.findall(r"errcode[\"'\s:=]+(-?\d+)", resp):
        if code in ERROR_HINTS:
            return ERROR_HINTS[code]
    if "invalid webhook url" in resp.lower():
        return ERROR_HINTS["93000"]
    if "Name or service not known" in resp or "Connection" in resp:
        return "网络不通：服务器访问不了 qyapi.weixin.qq.com，检查出网 / DNS / 代理。"
    return "未识别的失败原因，把上面的原始返回贴给我，我帮你看。"
