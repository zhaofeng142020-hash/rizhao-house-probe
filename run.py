"""统一启动入口（本地 / 容器 / 云托管通用）。

为什么需要它：
- 云平台通常只给一个端口，并通过 PORT 环境变量注入，同时要求监听 0.0.0.0；
- 直接 `uvicorn app.main:app` 需要先把 backend 放进 sys.path，不同调度器的
  工作目录不一致，容易 import 失败。这里把路径处理和端口处理收口到一处。

用法：
    python run.py              # 默认 8000
    PORT=3000 python run.py    # 由平台注入端口
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import uvicorn  # noqa: E402  （必须先补好 sys.path 再导入应用）

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
