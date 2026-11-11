"""验证码自动求解包：滑块 / 点选本地识别 + 拟人轨迹。

设计原则：
- 全程无人工参与：验证码出现时由程序自动识别并完成校验。
- 纯本地推理，不调用任何外部打码平台。
- 依赖可降级：优先 ddddocr（onnx 推理），未安装时退化到 OpenCV 几何识别。
"""
from .solver import solve_slider, solve_click_points, human_track

__all__ = ["solve_slider", "solve_click_points", "human_track"]
