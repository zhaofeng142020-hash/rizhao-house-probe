"""SQLite 访问层：连接管理 + 通用查询封装。

刻意保持轻量（标准库 sqlite3），后期切 PostgreSQL 只需替换本文件 + schema 方言。
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import settings

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

# 旧库升级：`CREATE TABLE IF NOT EXISTS` 只会建新表，不会给「已存在的旧表」补列。
# 版本迭代中新增的字段在这里登记，启动时自动补上（幂等，可重复执行）。
COLUMN_PATCHES: dict[str, dict[str, str]] = {
    "user_pref": {
        "down_ratio": "REAL NOT NULL DEFAULT 0.30",      # 首付比例
        "loan_years": "INTEGER NOT NULL DEFAULT 30",     # 贷款年限
        "loan_rate": "REAL NOT NULL DEFAULT 0.031",      # 年利率
        "is_first_home": "INTEGER NOT NULL DEFAULT 1",   # 是否首套（影响契税档位）
    },
    # 来源可核验性：demo=演示样本 / crawled=自动采集 / manual=人工录入。
    # source 字段保留为「来源标识」，但演示数据绝不能伪装成真实平台挂牌，
    # 因此单列 source_kind 标明数据性质，source_url 为空时前端不许渲染成可点链接。
    # 这三张表结构一致，便于详情页用同一张「来源卡片」展示。
    "listing": {
        "source_kind": "TEXT NOT NULL DEFAULT 'demo'",
        "source_name": "TEXT",
        "source_ref": "TEXT",
        "collected_at": "TEXT",
    },
    "auction": {
        "source_kind": "TEXT NOT NULL DEFAULT 'demo'",
        "source_name": "TEXT",
        "source_ref": "TEXT",
        "collected_at": "TEXT",
    },
    "new_home": {
        "source_kind": "TEXT NOT NULL DEFAULT 'demo'",
        "source_name": "TEXT",
        "source_ref": "TEXT",
        "collected_at": "TEXT",
    },
    "community": {
        "alias": "TEXT",                                 # 别名/俗称，搜索用（逗号分隔）
    },
}


def _connect() -> sqlite3.Connection:
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """上下文管理器：自动提交 / 回滚 / 关闭。"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """给已存在的旧表补齐新增字段，返回本次实际补上的 "表.字段"。"""
    added: list[str] = []
    for table, columns in COLUMN_PATCHES.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue                                    # 新库由 schema.sql 直接建好
        current = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name in current:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.append(f"{table}.{name}")
    return added


def init_db(reset: bool = False) -> None:
    """建表。reset=True 时删除旧库（仅 demo/测试用）。

    对已存在的库是增量升级：新表由 `IF NOT EXISTS` 建，新字段由 `_ensure_columns` 补，
    不会清空数据，重启即可完成升级。
    """
    if reset and settings.DB_PATH.exists():
        settings.DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(settings.DB_PATH) + suffix)
            if p.exists():
                p.unlink()
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)
        added = _ensure_columns(conn)
        if added:
            logger.info("数据库增量升级，补齐字段：%s", ", ".join(added))
        # 单行配置初始化
        conn.execute(
            "INSERT OR IGNORE INTO alert_config (id) VALUES (1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_pref (id) VALUES (1)"
        )


# --------------------------------------------------------------------------
# 通用查询助手
# --------------------------------------------------------------------------
def query_all(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: Sequence[Any] = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """执行写操作，返回 lastrowid。"""
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or 0


def execute_many(sql: str, seq: Iterable[Sequence[Any]]) -> int:
    with get_conn() as conn:
        cur = conn.executemany(sql, seq)
        return cur.rowcount or 0


def scalar(sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row or row[0] is None:
        return default
    return row[0]
