"""定时调度：每日凌晨增量计算 + 观察周报。"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .services.pipeline import build_weekly_report, run_daily_pipeline

logger = logging.getLogger("rzprobe.scheduler")
_scheduler: BackgroundScheduler | None = None


def _daily_job() -> None:
    logger.info("开始每日增量计算…")
    # 先备份再动数据：万一这次计算有问题，还能回到昨天的库
    try:
        from .services import backup
        path = backup.snapshot(tag="daily")
        if path:
            logger.info("已生成每日快照：%s", path.name)
    except Exception:  # noqa: BLE001  备份失败不能拖垮主流程
        logger.warning("每日快照失败，继续增量计算", exc_info=True)
    try:
        summary = run_daily_pipeline(push=True)
        logger.info("每日增量计算完成: %s", {k: v for k, v in summary.items() if k != "errors"})
    except Exception:  # noqa: BLE001  定时任务不能因为一次异常就死掉
        logger.exception("每日增量计算失败")
    # 大盘日快照兜底：即使今天没跑抓取（比如停过一次），也把当天在售/存量落一份，
    # 保证趋势序列不断点。下架判定只在抓取日做（detect 内部已按来源守门）。
    try:
        from .services import market_pulse
        market_pulse.detect_delistings()
        market_pulse.record_market_daily()
    except Exception:  # noqa: BLE001
        logger.warning("每日大盘快照失败", exc_info=True)


def _refresh_job() -> None:
    """抓真实房源（贝壳 + 安居客 + 58 同城）→ 入库（记调价）→ 重算评分 → 生成提醒。

    这是「每天两次更新房价和房源」的执行体：
    抓取失败只记日志，重算仍然进行（用库里已有数据），不会让站点停在旧状态。
    """
    logger.info("开始刷新真实房源…")
    try:
        from .services import backup
        path = backup.snapshot(tag="refresh")
        if path:
            logger.info("刷新前快照：%s", path.name)
    except Exception:  # noqa: BLE001
        logger.warning("刷新前快照失败，继续", exc_info=True)

    fetched: dict = {}
    try:
        from .services.live_refresh import push_refresh_summary, refresh_all
        report = refresh_all()
        fetched = report.get("sources", {})
        for k, v in fetched.items():
            logger.info("刷新 %s：抓到 %s，新增 %s，更新 %s（调价 %s），跳过 %s",
                        k, v.get("fetched"), v.get("saved"), v.get("updated"),
                        v.get("price_changes"), v.get("skipped"))
        if report.get("errors"):
            logger.warning("刷新过程中的错误：%s", report["errors"])
    except Exception:  # noqa: BLE001
        logger.exception("刷新真实房源失败，继续用库里数据重算")
        report = None

    summary = {}
    try:
        summary = run_daily_pipeline(push=True)
        logger.info("刷新后重算完成: %s", {
            k: v for k, v in summary.items() if k in
            ("listing_total", "scored", "events", "red", "yellow", "pushed")
        })
    except Exception:  # noqa: BLE001
        logger.exception("刷新后重算失败")

    # 主动把本次结果推给用户，不用他来网站查看
    if report:
        try:
            from .services.live_refresh import push_refresh_summary
            push_refresh_summary(report, summary)
        except Exception:  # noqa: BLE001
            logger.exception("刷新摘要推送失败")


def _session_check_job() -> None:
    """每日一次会话自检：失效就主动推送恢复提醒（带入口），不再盲目重试。"""
    logger.info("开始会话自检…")
    try:
        from .services.live_refresh import push_session_alert, session_health
        health = session_health()
        bad = [h for h in health if h.get("has_cookie") and not h.get("ok")]
        logger.info("会话自检完成：%s 个来源失效", len(bad))
        if bad:
            push_session_alert(bad)
    except Exception:  # noqa: BLE001
        logger.exception("会话自检失败")


def _rent_job() -> None:
    """每周日 03:30 抓一次贝壳日照租房页 → 租售比。低频、免验证码、免会话。"""
    logger.info("开始每周租金刷新…")
    try:
        from .services.rent_pulse import refresh_rents
        out = refresh_rents(pages=3)
        s = out.get("summary") or {}
        logger.info("租金刷新完成：%s 条样本，%s 个小区，全城租售比中位 %s",
                    s.get("city", {}).get("sample_n"),
                    len(s.get("communities") or []),
                    s.get("city", {}).get("yield_median"))
    except Exception:  # noqa: BLE001
        logger.exception("每周租金刷新失败")


def _weekly_job() -> None:
    logger.info("开始生成观察周报…")
    try:
        report = build_weekly_report()
        logger.info("观察周报完成: 观察 %s 项，值得看 %s 项，建议剔除 %s 项",
                    report["total"], len(report["still_worth"]), len(report["drop_out"]))
    except Exception:  # noqa: BLE001
        logger.exception("观察周报生成失败")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.ENABLE_SCHEDULER:
        logger.info("调度器已关闭（ENABLE_SCHEDULER=0）")
        return None
    if _scheduler and _scheduler.running:
        return _scheduler

    hour, _, minute = settings.DAILY_RUN_AT.partition(":")
    _scheduler = BackgroundScheduler(timezone=settings.TZ)
    _scheduler.add_job(
        _daily_job, CronTrigger(hour=int(hour), minute=int(minute or 0)),
        id="daily_pipeline", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 每天两次刷新真实房源（默认 07:30 / 19:30），刷完立刻重算，站点上的价格与线索随之更新
    refresh_times: list[str] = []
    if settings.LIVE_REFRESH_ENABLED:
        for i, slot in enumerate([s.strip() for s in settings.REFRESH_AT.split(",") if s.strip()]):
            h, _, m = slot.partition(":")
            try:
                _scheduler.add_job(
                    _refresh_job, CronTrigger(hour=int(h), minute=int(m or 0)),
                    id=f"live_refresh_{i}", replace_existing=True,
                    max_instances=1, coalesce=True,
                )
                refresh_times.append(f"{int(h):02d}:{int(m or 0):02d}")
            except (ValueError, TypeError):
                logger.warning("忽略无法解析的刷新时间：%s", slot)
    # 每周一 08:30 生成观察周报
    _scheduler.add_job(
        _weekly_job, CronTrigger(day_of_week="mon", hour=8, minute=30),
        id="weekly_report", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 每日 09:10 会话自检（在早上第一次刷新之后，坏了当天就能收到恢复提醒）
    _scheduler.add_job(
        _session_check_job, CronTrigger(hour=9, minute=10),
        id="session_check", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 每周日 03:30 租金抓取（贝壳租房页公开、免验证码、免会话）
    _scheduler.add_job(
        _rent_job, CronTrigger(day_of_week="sun", hour=3, minute=30),
        id="rent_refresh", replace_existing=True, max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info("调度器已启动：每日 %s 增量计算，每天 %s 刷新真实房源，每日 09:10 会话自检，每周一 08:30 观察周报",
                settings.DAILY_RUN_AT, "、".join(refresh_times) or "（未启用）")
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def job_snapshot() -> list[dict]:
    """给管理台展示：每个定时任务的下次执行时间。"""
    if not _scheduler:
        return []
    out = []
    try:
        jobs = _scheduler.get_jobs()
    except Exception:  # noqa: BLE001
        return []
    for job in jobs:
        nxt = getattr(job, "next_run_time", None)
        out.append({
            "id": job.id,
            "name": {
                "daily_pipeline": "每日增量计算",
                "weekly_report": "观察周报",
                "session_check": "会话自检",
                "rent_refresh": "租金刷新",
            }.get(job.id, "刷新真实房源" if job.id.startswith("live_refresh") else job.id),
            "next_run": nxt.isoformat() if nxt else None,
        })
    return out


def scheduler_running() -> bool:
    return bool(_scheduler and _scheduler.running)
