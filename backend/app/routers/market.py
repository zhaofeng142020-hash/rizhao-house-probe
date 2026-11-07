"""市场大盘接口：全城库存/中位价/新增/下架趋势 + 去化近似。

数据全部来自真实采集（crawled）房源的每日快照；演示样本不计入。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..services.market_pulse import market_overview
from ..services.rent_pulse import rent_summary

router = APIRouter(prefix="/api/market", tags=["市场大盘"])


@router.get("/overview")
def overview(days: int = 180):
    """全城大盘概览。days 控制回看天数（默认 180）。"""
    return market_overview(max(30, min(days, 730)))


@router.get("/rents")
def rents():
    """真实租售比：每小区租金中位与年化租金回报率（最近一批租房样本口径）。"""
    return rent_summary()
