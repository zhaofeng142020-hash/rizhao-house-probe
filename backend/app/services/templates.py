"""推送文案模板（红 / 黄 / 白 三级）。

硬性要求：
1. 标题固定「【日照房价探针·捡漏雷达】」
2. 小区+户型+面积+总价+单价
3. 比同小区低多少、降了几次、挂了多久
4. 可能原因（急售/法拍/硬伤）
5. 人工核验 3 件事
6. 结尾免责声明：挂牌价≠成交价
"""
from __future__ import annotations

from datetime import date
from typing import Any

GRADE_META = {
    "red": {"icon": "🔴", "label": "重点留意", "hint": "建议 48 小时内实地或电话核实"},
    "yellow": {"icon": "🟡", "label": "建议观察", "hint": "进观察清单，先电话验证再跑一趟"},
    "white": {"icon": "⚪", "label": "仅记录", "hint": "硬伤/风险偏高，不主动提醒"},
}

DISCLAIMER = "挂牌价≠成交价，捡漏分仅作观察参考，最终以实地尽调为准。"


def _wan(value: Any) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v / 10000:.1f}万" if v >= 10000 else f"{v:,.0f}元"


def _pct(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _num(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def build_listing_messages(item: dict, score: dict) -> dict:
    """二手房：返回 {title, text, markdown} 三种形态，供不同通道使用。"""
    grade = score.get("grade", "white")
    meta = GRADE_META.get(grade, GRADE_META["white"])

    community = item.get("community_name") or "未知小区"
    layout = item.get("layout") or f"{item.get('rooms', '')}室"
    area = item.get("area") or 0
    total = item.get("total_price") or 0
    unit = item.get("unit_price") or 0
    district = item.get("district") or ""

    deviation = score.get("deviation_pct") or 0
    changes = item.get("price_changes") or 0
    consec = item.get("consecutive_drops") or 0
    list_days = item.get("list_days") or 0
    median_unit = score.get("median_unit") or 0

    conf_label = {"high": "高", "mid": "中", "low": "低"}.get(score.get("confidence"), "中")
    risk_label = {"low": "低", "mid": "中", "high": "高"}.get(score.get("risk_level"), "中")

    reasons: list[str] = []
    if consec >= 2:
        reasons.append(f"连续降价{consec}次（疑似急售/置换）")
    elif changes:
        reasons.append(f"累计调价{changes}次")
    if list_days > 180:
        reasons.append(f"滞销{list_days}天（卖方心态松动）")
    tags = item.get("defect_tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.strip("[]").replace('"', "").split(",") if t]
    if tags:
        reasons.append("硬伤：" + "、".join(tags))
    if not reasons:
        reasons.append("暂无明显降价动因，警惕假低价")

    line2 = f"{district}·{community} | {layout} | {float(area):.0f}㎡"
    line3 = f"总价 {_wan(total)} / 单价 {_num(unit)} 元/㎡"
    line4 = (
        f"比同小区同户型中位({_num(median_unit)}元/㎡)低 {_pct(deviation)}"
        f" | 调价{changes}次 | 挂牌{list_days}天"
    )
    line5 = "可能原因：" + "；".join(reasons)

    text = "\n".join([
        f"【日照房价探针·捡漏雷达】{meta['icon']}{meta['label']}",
        f"捡漏分 {score.get('score')} ｜ 置信度{conf_label} ｜ 风险{risk_label}",
        line2,
        line3,
        line4,
        line5,
        f"为什么便宜：{score.get('why_cheap', '—')}",
        f"可能是坑：{score.get('why_trap', '—')}",
        "需人工核验3件事：",
        "1. 实地看房查渗漏/采光/噪音",
        "2. 问中介近3月同户型真实成交价",
        "3. 查学区占用（法拍另查占用/腾退/税费）",
        DISCLAIMER,
    ])

    markdown = "\n".join([
        f"### {meta['icon']} 日照房价探针·捡漏雷达 · {meta['label']}",
        f"> 捡漏分 **{score.get('score')}** ｜ 置信度{conf_label} ｜ 风险{risk_label} ｜ {date.today().isoformat()}",
        f"**{line2}**",
        f"{line3}",
        f"<font color=\"warning\">{line4}</font>",
        f"{line5}",
        f"**为什么便宜**：{score.get('why_cheap', '—')}",
        f"**可能是坑**：{score.get('why_trap', '—')}",
        "**需人工核验 3 件事**",
        "1. 实地看房查渗漏/采光/噪音",
        "2. 问中介近 3 月同户型真实成交价",
        "3. 查学区占用（法拍另查占用/腾退/税费）",
        f"<font color=\"comment\">{DISCLAIMER}</font>",
    ])

    title = f"【{meta['icon']}{meta['label']}】{community} {layout} {float(area):.0f}㎡ {_wan(total)}"
    return {"title": title, "text": text, "markdown": markdown, "grade": grade, "hint": meta["hint"]}


def build_auction_messages(item: dict, score: dict) -> dict:
    """法拍：文案额外强调到手价与腾退风险。"""
    grade = score.get("grade", "white")
    meta = GRADE_META.get(grade, GRADE_META["white"])

    community = item.get("community_name") or item.get("title") or "法拍房源"
    area = item.get("area") or 0
    start = item.get("start_price") or 0
    landing = score.get("landing_price") or item.get("landing_price") or 0
    appraisal = item.get("appraisal_price") or 0
    tags = item.get("risk_tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.strip("[]").replace('"', "").split(",") if t]

    text = "\n".join([
        f"【日照房价探针·捡漏雷达】{meta['icon']}{meta['label']}（法拍）",
        f"捡漏分 {score.get('score')} ｜ 置信度{'中' if score.get('confidence') == 'mid' else '低'} "
        f"｜ 风险{'低' if score.get('risk_level') == 'low' else '中/高'}",
        f"{item.get('district', '')}·{community} | {float(area):.0f}㎡ | {item.get('stage', '一拍')}",
        f"评估价 {_wan(appraisal)} / 起拍价 {_wan(start)} / 到手价预估 {_wan(landing)}",
        f"起拍折扣 {_pct(score.get('start_discount'))} | 净折扣 {_pct(score.get('net_discount'))}"
        f"（到手单价 {_num(score.get('landing_unit'))} 元/㎡）",
        "风险标签：" + ("、".join(tags) if tags else "未标注（仍需核验）"),
        "需人工核验3件事：",
        "1. 实地看房查渗漏/采光/噪音",
        "2. 问中介近3月同户型真实成交价",
        "3. 查占用/腾退/税费（法拍重点：是否有人居住、租约是否有效、税费谁承担）",
        DISCLAIMER,
    ])

    markdown = "\n".join([
        f"### {meta['icon']} 日照房价探针·法拍机会 · {meta['label']}",
        f"> 捡漏分 **{score.get('score')}** ｜ {item.get('stage', '一拍')} ｜ {date.today().isoformat()}",
        f"**{item.get('district', '')}·{community}｜{float(area):.0f}㎡**",
        f"评估价 `{_wan(appraisal)}` → 起拍价 `{_wan(start)}` → 到手价预估 `{_wan(landing)}`",
        f"起拍折扣 **{_pct(score.get('start_discount'))}** ｜ 净折扣 **{_pct(score.get('net_discount'))}**"
        f"（到手单价 {_num(score.get('landing_unit'))} 元/㎡）",
        f"风险标签：<font color=\"warning\">{'、'.join(tags) if tags else '未标注'}</font>",
        "**需人工核验 3 件事**",
        "1. 实地看房查渗漏/采光/噪音",
        "2. 问中介近 3 月同户型真实成交价",
        "3. 查占用/腾退/税费（谁在住、租约是否有效、税费谁担）",
        f"<font color=\"comment\">{DISCLAIMER}</font>",
    ])

    title = f"【{meta['icon']}{meta['label']}·法拍】{community} {float(area):.0f}㎡ 到手{_wan(landing)}"
    return {"title": title, "text": text, "markdown": markdown, "grade": grade, "hint": meta["hint"]}


def build_digest(items: list[dict]) -> dict:
    """黄级每日汇总（不逐条轰炸）。"""
    if not items:
        return {"title": "【日照房价探针】今日无新增观察项", "text": "今日无新增黄级线索。", "markdown": "今日无新增黄级线索。"}
    lines = [f"【日照房价探针·捡漏雷达】🟡 每日观察汇总（{len(items)} 条）"]
    md_lines = [f"### 🟡 每日观察汇总 · {date.today().isoformat()}（{len(items)} 条）"]
    for idx, it in enumerate(items[:12], 1):
        s = it["score"]
        name = f"{it['item'].get('district', '')}·{it['item'].get('community_name', '')} " \
               f"{it['item'].get('layout', '')} {float(it['item'].get('area') or 0):.0f}㎡ " \
               f"{_wan(it['item'].get('total_price') or s.get('landing_price'))}"
        lines.append(f"{idx}. {name} ｜ {s.get('score')}分 ｜ 低{_pct(s.get('deviation_pct') or 0)}")
        md_lines.append(f"{idx}. **{name}** ｜ `{s.get('score')}分` ｜ 低{_pct(s.get('deviation_pct') or 0)}")
    lines.append(DISCLAIMER)
    md_lines.append(f"<font color=\"comment\">{DISCLAIMER}</font>")
    return {"title": f"【🟡每日汇总】{len(items)} 条待观察线索",
            "text": "\n".join(lines), "markdown": "\n".join(md_lines)}
