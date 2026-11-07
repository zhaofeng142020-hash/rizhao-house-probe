# ============================================================================
# 日照房价探针 · 捡漏雷达 —— 单镜像部署（前端构建 + 后端服务）
# ============================================================================

# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：运行时 ----------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# 前端构建产物：vite 的 outDir 指向 ../backend/app/webui，所以在构建阶段落在 /backend/app/webui
COPY --from=frontend /backend/app/webui ./backend/app/webui

# SQLite 数据目录（用卷挂载持久化）
VOLUME ["/app/data"]
EXPOSE 8000

WORKDIR /app/backend
# 首次启动会自动建表 + 灌演示数据 + 跑一次增量计算
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
