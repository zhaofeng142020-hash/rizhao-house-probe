"""数据备份与恢复。

两类东西分开对待：

1. **可重建的数据**（小区 / 房源 / 法拍 / 新盘 / 评分 / 异动）——由 seed 或抓取生成，
   丢了重跑一次就有，不进备份。
2. **你自己攒的数据**（观察清单 / 自住偏好 / 推送配置 / 在线配置里的企微密钥）——
   丢了就真没了，这才是备份要保护的对象。

提供两级保险：
- `export_assets()` / `import_assets()` → 一份可读的 JSON，能下载、能跨机器搬
- `snapshot()` → 每日把整个 SQLite 库热备一份（保留最近 KEEP 份），用于「手滑搞坏了」快速回滚
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path

from ..config import settings
from ..db import get_conn, query_all, query_one

logger = logging.getLogger(__name__)

# 只备份「用户资产」表。房源类数据不进来：它们是可重建的，塞进来只会把备份撑大。
ASSET_TABLES: tuple[str, ...] = ("watch_item", "user_pref", "alert_config", "app_setting")

BACKUP_DIR: Path = settings.DB_PATH.parent / "backups"
KEEP_SNAPSHOTS = 14          # 保留最近 14 份日快照（约两周）


# --------------------------------------------------------------------------
# 导出 / 导入
# --------------------------------------------------------------------------
# 观察清单指向的对象靠自增 id 定位，而重灌演示数据 / 换库后 id 会变。
# 所以每条观察记录额外带一个「稳定标识」，恢复时用它重新找回目标。
_REF_SOURCE = {
    "listing": ("listing", "fingerprint"),
    "auction": ("auction", "fingerprint"),
    "community": ("community", "name"),
}


def _ref_of(conn, target_type: str, target_id: int) -> str | None:
    spec = _REF_SOURCE.get(target_type)
    if not spec:
        return None
    table, key = spec
    row = conn.execute(f"SELECT {key} AS v FROM {table} WHERE id=?", (target_id,)).fetchone()
    return (dict(row) or {}).get("v") if row else None


def _resolve_target(conn, target_type: str, target_id, ref) -> int | None:
    """先认原 id；对不上就按稳定标识找新 id。都失败返回 None。"""
    spec = _REF_SOURCE.get(target_type)
    if not spec:
        return None
    table, key = spec
    if target_id is not None:
        row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (target_id,)).fetchone()
        if row:
            return int(row["id"])
    if ref:
        # fingerprint 是唯一索引；community 用 name（可能重名，取第一条）
        row = conn.execute(f"SELECT id FROM {table} WHERE {key}=? LIMIT 1", (ref,)).fetchone()
        if row:
            return int(row["id"])
    return None


def export_assets() -> dict:
    """把用户资产打成一个可读的 JSON。包含口令与企微密钥，属于敏感文件。"""
    tables: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    with get_conn() as conn:
        for table in ASSET_TABLES:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
            if table == "watch_item":
                for row in rows:
                    row["_ref"] = _ref_of(conn, row.get("target_type"), row.get("target_id"))
            tables[table] = rows
            counts[table] = len(rows)

        seed = conn.execute("SELECT value FROM app_setting WHERE key='seed_version'").fetchone()

    return {
        "kind": "rizhao-house-radar-assets",
        "version": 2,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "seed_version": (dict(seed) or {}).get("value") if seed else None,
        "sensitive": True,
        "note": "此文件含管理口令与企业微信密钥，请勿外传或上传到公开网盘。",
        "counts": counts,
        "tables": tables,
    }


def _table_columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def import_assets(payload: dict, *, replace: bool = True) -> dict:
    """从导出文件恢复用户资产。

    replace=True（默认）先清空目标表再写入，语义是「恢复到这个备份的状态」；
    replace=False 则只补不删。

    只动 ASSET_TABLES，绝不碰房源 / 评分 / 异动表，所以恢复一次不会把房源数据卷坏。
    """
    if not isinstance(payload, dict) or payload.get("kind") != "rizhao-house-radar-assets":
        raise ValueError("文件格式不对：这不是本系统导出的资产备份")

    incoming: dict = payload.get("tables") or {}
    if not isinstance(incoming, dict):
        raise ValueError("文件里的 tables 字段格式不对")

    restored: dict[str, int] = {}
    skipped: list[str] = []
    remapped = 0
    lost = 0

    with get_conn() as conn:
        for table in ASSET_TABLES:
            rows = incoming.get(table)
            if rows is None:
                skipped.append(table)
                continue
            if not isinstance(rows, list):
                skipped.append(table)
                continue

            cols = _table_columns(conn, table)
            if replace:
                conn.execute(f"DELETE FROM {table}")

            written = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                use = {k: v for k, v in row.items() if k in cols}
                if not use:
                    continue

                if table == "watch_item":
                    # 目标 id 可能在换库/重灌后失效，按稳定标识重新定位
                    original = row.get("target_id")
                    resolved = _resolve_target(conn, row.get("target_type"), original, row.get("_ref"))
                    if resolved is None:
                        lost += 1
                        continue
                    if resolved != original:
                        remapped += 1
                    use["target_id"] = resolved

                names = ", ".join(use)
                marks = ", ".join("?" * len(use))
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})",
                    tuple(use.values()),
                )
                written += 1
            restored[table] = written

        # 兜底：两张单行配置表必须有且只有一行，否则接口会 500
        conn.execute("INSERT OR IGNORE INTO user_pref (id) VALUES (1)")
        conn.execute("INSERT OR IGNORE INTO alert_config (id) VALUES (1)")

        dropped = _drop_dangling(conn)

    logger.info("资产恢复完成：%s（重映射 %s，失联 %s）", restored, remapped, lost)
    return {
        "ok": True,
        "restored": restored,
        "skipped_tables": skipped,
        "remapped": remapped,
        "lost": lost,
        "dropped_dangling": dropped,
        "exported_at": payload.get("exported_at"),
        "note": "已恢复。若恢复了 admin_token，请重新用新口令登录管理台。",
    }


def _drop_dangling(conn) -> int:
    """清掉指向已不存在房源的观察记录。

    重灌演示数据后 listing 的 id 可能错位，留着这种记录点进去只会 404，
    所以宁可清掉并把数量如实报出来。
    """
    total = 0
    for target_type, table in (("listing", "listing"), ("auction", "auction"), ("community", "community")):
        cur = conn.execute(
            f"DELETE FROM watch_item WHERE target_type=? "
            f"AND target_id NOT IN (SELECT id FROM {table})",
            (target_type,),
        )
        total += cur.rowcount or 0
    return total


# --------------------------------------------------------------------------
# 整库快照（每日自动）
# --------------------------------------------------------------------------
def _db_bytes() -> int:
    try:
        return settings.DB_PATH.stat().st_size
    except OSError:
        return 0


def snapshot(tag: str = "") -> Path | None:
    """把当前库热备一份。

    用 SQLite 官方的 `VACUUM INTO`，对正在运行的库是安全的一致性快照
    （比直接 copy 文件可靠：后者可能拷到写了一半的 WAL 状态）。
    """
    if not settings.DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"rizhao-{stamp}{('-'+tag) if tag else ''}.db"
    target = BACKUP_DIR / name
    if target.exists():
        target.unlink()
    try:
        with get_conn() as conn:
            conn.execute("VACUUM INTO ?", (str(target),))
    except Exception as exc:                        # 备份失败不能拖垮主流程
        logger.warning("库快照失败：%s", exc)
        return None
    _prune()
    return target


def _prune() -> None:
    """只留最近 KEEP_SNAPSHOTS 份，避免把磁盘塞满。"""
    files = sorted(BACKUP_DIR.glob("rizhao-*.db"))
    for old in files[:-KEEP_SNAPSHOTS] if len(files) > KEEP_SNAPSHOTS else []:
        try:
            old.unlink()
        except OSError:
            pass


def ensure_daily_snapshot() -> Path | None:
    """启动时兜底：今天还没快照就补一份。

    调度器关闭（ENABLE_SCHEDULER=0）时也有备份，避免「只有开了定时任务才有备份」的盲区。
    """
    today = date.today().strftime("%Y%m%d")
    if BACKUP_DIR.exists() and any(BACKUP_DIR.glob(f"rizhao-{today}-*.db")):
        return None
    return snapshot(tag="boot")


def list_snapshots() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    items = []
    for p in sorted(BACKUP_DIR.glob("rizhao-*.db"), reverse=True)[:KEEP_SNAPSHOTS]:
        stat = p.stat()
        items.append({
            "name": p.name,
            "size_kb": round(stat.st_size / 1024, 1),
            "at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return items


def backup_status() -> dict:
    return {
        "snapshot_dir": str(BACKUP_DIR),
        "snapshot_count": len(list_snapshots()),
        "keep": KEEP_SNAPSHOTS,
        "snapshots": list_snapshots()[:6],
        "db_size_kb": round(_db_bytes() / 1024, 1),
        "asset_counts": {
            t: int((query_one(f"SELECT COUNT(*) AS n FROM {t}") or {}).get("n") or 0)
            for t in ASSET_TABLES
        },
    }
