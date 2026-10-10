import pathlib
import sys

# 保证从 backend/ 目录运行 pytest 时可导入 app 包
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.deps import get_config_service
from app.main import app
from app.persistence.db import get_engine, init_db
from app.services.config_service import ConfigService
from app.settings import Settings


@pytest.fixture(autouse=True)
def _temp_config_service(tmp_path):
    """每个测试用独立的临时 SQLite，并覆盖配置服务依赖。"""
    engine = get_engine(Settings("", str(tmp_path / "t.db"), str(tmp_path), 6000, 8000))
    init_db(engine)
    app.dependency_overrides[get_config_service] = lambda: ConfigService(engine)
    yield
    app.dependency_overrides.clear()
