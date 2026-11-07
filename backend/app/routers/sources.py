"""来源目录 / 真实房源导入 / 采集开关。

GET /api/sources 是公开读接口：任何人都能看清「多少是真的」。
写操作（导入、开关、触发采集）走 admin_guard，必须管理口令。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import sources as source_svc

router = APIRouter(prefix="/api/sources", tags=["数据来源"])
admin_router = APIRouter(prefix="/api/admin/sources", tags=["数据来源管理"])


@router.get("")
def source_overview():
    # 云端容器若被回收，内存里的定时任务会丢；有人打开网站就顺手补一轮。
    # 只读、非阻塞，5 分钟节流一次。
    from ..services.freshness import maybe_catchup

    maybe_catchup()
    return source_svc.overview()


@router.get("/template")
def import_template():
    """给前端「填入示例」用的字段说明，避免用户猜格式。"""
    return {
        "format": "JSON 数组",
        "required": ["community_name", "area", "total_wan 或 total_price", "source_url"],
        "fields": [
            {"key": "community_name", "desc": "小区名，库里没有会自动新建", "example": "某某花园"},
            {"key": "district", "desc": "板块；新建小区时建议填", "example": "东港区"},
            {"key": "layout", "desc": "户型", "example": "3室2厅"},
            {"key": "area", "desc": "建筑面积 ㎡", "example": 128.5},
            {"key": "total_wan", "desc": "挂牌总价，单位万元（推荐）", "example": 112},
            {"key": "source_url", "desc": "原始挂牌页完整链接，必填，用于回溯核验", "example": "https://..."},
            {"key": "source_ref", "desc": "平台房源编号，可选", "example": "32188475"},
            {"key": "list_days", "desc": "已挂牌天数，可选", "example": 45},
            {"key": "floor_text", "desc": "楼层，可选", "example": "8/18层"},
            {"key": "defect_tags", "desc": "硬伤标签数组，可选", "example": ["顶楼"]},
        ],
        "sample": [
            {
                "community_name": "示例小区", "district": "东港区", "layout": "3室2厅",
                "area": 128.5, "total_wan": 112,
                "source_url": "https://example.com/house/123456",
                "source_ref": "123456", "list_days": 45, "floor_text": "8/18层",
                "defect_tags": [],
            }
        ],
        "rules": [
            "每条必须带原始页面链接，否则拒绝导入 —— 没有链接就无法核验",
            "同小区 + 同户型 + 同面积 + 同总价的重复房源会自动跳过",
            "导入的是挂牌价，不是成交价；系统不会把它算成真实成交",
            "单批最多 500 条；导入后需到管理台点一次「重算」才会参与评分",
        ],
    }


class ImportIn(BaseModel):
    rows: list[dict]
    source_key: str = "manual"
    source_name: str | None = None
    source_kind: str = "manual"  # manual=人工/表格整理；crawled=采集脚本产出


@admin_router.post("/import")
def import_listings(payload: ImportIn):
    try:
        return source_svc.import_listings(
            payload.rows, payload.source_key, payload.source_name, payload.source_kind
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class EnableIn(BaseModel):
    enabled: bool


@admin_router.put("/{key}/enable")
def toggle_source(key: str, payload: EnableIn):
    try:
        return source_svc.set_source_enabled(key, payload.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@admin_router.post("/crawl")
def crawl_now(pages: int = 1):
    """手动触发一次真实采集。只在 CRAWL_ENABLED=1 且有勾选通道时才联网。"""
    return source_svc.collect_enabled_sources(pages=max(1, min(pages, 5)))


@admin_router.get("/batches")
def import_batches():
    from ..db import query_all

    return {"items": query_all(
        "SELECT * FROM source_import_batch ORDER BY id DESC LIMIT 30"
    )}
