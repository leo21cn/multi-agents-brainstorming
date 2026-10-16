import threading
import time
from types import SimpleNamespace

from app import deps


def _reset():
    deps._vs = None
    deps._engine = None


def test_concurrent_first_calls_build_one_vector_store(monkeypatch, tmp_path):
    """冷启动时房间页并发拉三份配置：单例的懒建必须串行。

    两个线程同时进 Chroma 的 PersistentClient，其共享系统注册表会以 KeyError 冒出，
    表现为三个只读接口一起 500。
    """
    _reset()
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    built = []
    real = deps.default_vector_store

    def spy(data_dir):
        built.append(data_dir)
        return real(data_dir)

    monkeypatch.setattr(deps, "default_vector_store", spy)

    errors = []

    def call():
        try:
            deps.get_vector_store_singleton()
        except Exception as exc:  # noqa: BLE001 - 用例要看到任何异常
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _reset()

    assert not errors
    assert len(built) == 1


def test_concurrent_first_calls_build_one_engine(monkeypatch):
    """同一把锁也要挡住引擎与建表：并发建两次会让 init_db 在同一库上交错。"""
    _reset()
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(data_dir="unused"))
    built = []

    def fake_engine(settings):
        built.append("engine")
        time.sleep(0.02)  # 撑开竞态窗口：不加锁时八个线程会各建一次
        return object()

    monkeypatch.setattr(deps, "get_engine", fake_engine)
    monkeypatch.setattr(deps, "init_db", lambda e: built.append("init"))

    def call():
        deps.get_engine_singleton()

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _reset()

    assert built.count("engine") == 1
    assert built.count("init") == 1
