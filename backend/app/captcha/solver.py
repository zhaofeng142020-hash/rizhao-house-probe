"""验证码求解核心：滑块缺口定位、点选目标定位、拟人拖动轨迹。

识别策略（按优先级）：
  1. ddddocr 深度模型（准确率高，推荐安装：pip install ddddocr）
  2. OpenCV 边缘几何识别（无深度模型时的兜底）
两者都不可用时抛 RuntimeError，由上层决定降级策略。

本模块只做图像推理，不做任何网络请求。
"""
from __future__ import annotations

import logging
import random

logger = logging.getLogger("rzprobe.captcha.solver")

_ocr_slide = None
_ocr_det = None
_loaded = False


def _ensure_models() -> bool:
    """懒加载 ddddocr 模型；未安装或加载失败返回 False（走 OpenCV 兜底）。"""
    global _ocr_slide, _ocr_det, _loaded
    if _loaded:
        return _ocr_slide is not None or _ocr_det is not None
    _loaded = True
    try:
        import ddddocr  # noqa: PLC0415
    except ImportError:
        logger.info("ddddocr 未安装，滑块/点选识别将使用 OpenCV 几何兜底")
        return False
    try:
        _ocr_slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    except Exception:  # noqa: BLE001  旧版本参数不同
        try:
            _ocr_slide = ddddocr.DdddOcr(show_ad=False)
        except Exception:  # noqa: BLE001
            _ocr_slide = None
    try:
        _ocr_det = ddddocr.DdddOcr(det=True, show_ad=False)
    except Exception:  # noqa: BLE001
        _ocr_det = None
    return _ocr_slide is not None or _ocr_det is not None


# --------------------------------------------------------------------------
# 滑块缺口定位
# --------------------------------------------------------------------------
def solve_slider(bg_bytes: bytes, slider_bytes: bytes | None = None) -> int:
    """返回滑块需要水平拖动的像素距离。

    bg_bytes     带缺口的背景图（必需）
    slider_bytes 滑块小图（有则用 ddddocr 双图匹配，最准）
    """
    if not bg_bytes:
        raise RuntimeError("缺少背景图")
    if _ensure_models() and _ocr_slide is not None:
        try:
            if slider_bytes:
                res = _ocr_slide.slide_match(slider_bytes, bg_bytes, simple_target=True)
                dist = int(res["target"][0])
            else:
                res = _ocr_slide.slide_comparison(bg_bytes, bg_bytes)
                dist = int(res["target"][0])
            if dist > 0:
                return dist
        except Exception as exc:  # noqa: BLE001  模型失败转 OpenCV
            logger.warning("ddddocr 滑块识别失败，转 OpenCV：%s", exc)
    return _solve_slider_cv(bg_bytes)


def _solve_slider_cv(bg_bytes: bytes) -> int:
    """OpenCV 兜底：边缘检测找矩形缺口左边界。"""
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("OpenCV 未安装，无法几何识别滑块缺口") from None
    arr = np.frombuffer(bg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("背景图解码失败")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 100, 200)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h_img = img.shape[0]
    candidates: list[tuple[int, int]] = []  # (x, score)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # 缺口典型尺寸：宽 40-220，高 20-90，且不是整图边框
        if 40 <= w <= 220 and 20 <= h <= 90 and h_img - y > h + 5:
            # 缺口内部梯度低（边缘包着的空腔），外框梯度高 → 用周长打分
            peri = cv2.arcLength(c, True)
            candidates.append((x, int(peri)))
    if not candidates:
        raise RuntimeError("未找到滑块缺口轮廓")
    # 取轮廓最规整（周长接近矩形 2*(w+h) 误差小）且位置合理的候选
    candidates.sort(key=lambda t: -t[1])
    return int(candidates[0][0])


# --------------------------------------------------------------------------
# 点选目标定位
# --------------------------------------------------------------------------
def solve_click_points(image_bytes: bytes, need: int = 3) -> list[tuple[int, int]]:
    """识别点选验证码中需要依次点击的目标中心坐标。

    need=期望点选个数；返回 [(x1,y1),(x2,y2),...]，按从左到右排序。
    """
    if not _ensure_models() or _ocr_det is None:
        raise RuntimeError("点选识别需要 ddddocr（pip install ddddocr）")
    try:
        boxes = _ocr_det.detection(image_bytes)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"点选目标检测失败：{exc}") from exc
    pts = []
    for box in boxes[:max(1, need)]:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        pts.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys))))
    pts.sort(key=lambda t: t[0])
    return pts


# --------------------------------------------------------------------------
# 拟人轨迹（风控行为检测的关键：不能匀速直线）
# --------------------------------------------------------------------------
def human_track(distance: int) -> list[tuple[int, int, int]]:
    """生成拟人拖动轨迹。

    返回 [(dx, dy, delay_ms), ...]：
      - 前段加速（二次缓动），末段减速
      - 轻微过冲 4-12px 后回拉
      - y 方向随机微抖 ±2px
      - 总时长 0.6-1.1 秒
    """
    if distance <= 0:
        return []
    overshoot = random.randint(4, 12)
    total = distance + overshoot
    steps = max(18, min(45, int(total / 6)))
    track: list[tuple[int, int, int]] = []
    moved = 0.0
    for i in range(1, steps + 1):
        # 前快后慢：ease-out quad
        progress = i / steps
        eased = 1 - (1 - progress) ** 2
        target = total * eased
        dx = int(round(target - moved))
        moved += dx
        dy = random.choice((-1, 0, 0, 1))
        delay = random.randint(8, 22)
        track.append((dx, dy, delay))
    # 过冲回拉
    back = overshoot + random.randint(0, 2)
    track.append((-back, 0, random.randint(60, 120)))
    track.append((random.randint(0, 2), random.randint(-1, 1), random.randint(40, 90)))
    return track
