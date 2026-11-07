"""生成「可上传的精简部署包」。

为什么需要：本地开发目录里有 .venv（约 200MB）和 frontend/node_modules（约 170MB），
这些既不该上传也不该进制品。云托管只需要「后端源码 + 已构建好的前端产物 + 依赖清单」。

用法：
    python deploy_pack.py                 # 默认输出到姊妹目录 rizhao-house-probe-deploy
    python deploy_pack.py D:/tmp/rzpkg    # 自定义输出目录

生成后直接发布该目录即可；改了源码重新跑一次本脚本就能同步。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = BASE_DIR.parent / "rizhao-house-probe-deploy"

# 不进入部署包的东西（体积大 / 与运行无关）
IGNORE_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv",
    "node_modules", ".git", "dist", "tests",
}
IGNORE_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm"}
# 根目录需要带上的零散文件
ROOT_FILES = ["requirements.txt", "run.py", "README.md", ".env.example", ".env"]

# 必须存在的产物，缺了就直接报错（否则线上会是一个只有 API 的空站）
REQUIRED = [
    "backend/app/main.py",
    "backend/app/schema.sql",
    "backend/app/webui/index.html",
]


def build(out_dir: Path) -> int:
    out_dir = out_dir.resolve()
    if out_dir == BASE_DIR:
        print("[错误] 输出目录不能是项目目录本身")
        return 2
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # 1) 后端源码（含已构建的前端产物 backend/app/webui）
    shutil.copytree(
        BASE_DIR / "backend",
        out_dir / "backend",
        ignore=shutil.ignore_patterns(*IGNORE_DIRS, *IGNORE_SUFFIX),
    )

    # 2) 根目录零散文件
    for name in ROOT_FILES:
        src = BASE_DIR / name
        if src.exists():
            shutil.copy2(src, out_dir / name)

    # 3) 产物自检
    missing = [p for p in REQUIRED if not (out_dir / p).exists()]
    if missing:
        print("[错误] 部署包缺少以下文件，请先在 frontend 目录执行 npm run build：")
        for p in missing:
            print("       -", p)
        return 1

    total = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    files = sum(1 for f in out_dir.rglob("*") if f.is_file())
    print(f"[完成] 部署包已生成：{out_dir}")
    print(f"       文件数 {files} 个，合计 {total / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    raise SystemExit(build(target))
