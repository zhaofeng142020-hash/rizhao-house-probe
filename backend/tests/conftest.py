"""pytest 公共夹具：用临时库跑，绝不碰 data/rizhao.db。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让 `from app.xxx import yyy` 可用（tests 目录在 backend 下）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

# 仅供 pytest 使用的固定假口令，不是任何真实凭据
TEST_ADMIN_TOKEN = "pytest-only-fake-token"


@pytest.fixture(scope="session", autouse=True)
def temp_db(tmp_path_factory):
    """把数据库切到临时目录，并灌一份小规模演示数据。"""
    db_path = tmp_path_factory.mktemp("rzprobe") / "test.db"
    config.settings.DB_PATH = db_path
    # 固定测试口令，避免自动生成导致每次跑测试口令都变
    config.settings.ADMIN_TOKEN = TEST_ADMIN_TOKEN
    # db.py 里的 settings 是同一个对象，改一处即可生效
    from app.db import init_db
    from app.services.pipeline import run_daily_pipeline
    from app.services.seed import SEED_VERSION, seed_all

    init_db(reset=True)
    seed_all(clear=True)
    # 标好样本版本，避免 TestClient 触发生命周期时又重灌一遍（会让 id 变化、断言失效）
    from app.services.auth import set_setting
    set_setting("seed_version", SEED_VERSION)
    run_daily_pipeline(push=False)
    yield db_path


@pytest.fixture()
def client(temp_db):
    """已登录的客户端：默认带管理员口令，用于测业务接口。"""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        c.headers.update({"X-Admin-Token": TEST_ADMIN_TOKEN})
        yield c


@pytest.fixture()
def anon_client(temp_db):
    """未登录的客户端：用于验证写操作被拦住、读操作仍然开放。"""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c
