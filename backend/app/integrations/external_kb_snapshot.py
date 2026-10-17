"""外部知识库连接层：随服务启动的后台任务维护管理元数据快照。

口径（AGENTS 不变量 21 增补）：
- 快照只缓存**管理元数据**（语料库清单、存在/归档状态、健康状态、刷新时间），
  供管理界面与挂接失效判定即时读取，不再逐条实时调外部；
- 发言组合检索仍**实时**调外部 `/search`，不走快照（实时性不变）；
- 未配置/未启用时置 disabled、零外部调用；外部失败保留旧快照并显式记错误状态
  （管理界面显式降级标注，不静默）。

后台循环经 lifespan 启动（进程内全局一份），线程安全：循环在事件循环内跑，
同步 httpx 经 asyncio.to_thread 执行；PATCH 保存连接配置后经线程安全 kick 触发即时刷新。
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from app.integrations.external_kb import ExternalKbError

REFRESH_INTERVAL_SECONDS = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExternalKbSnapshot:
    def __init__(self):
        self._lock = threading.Lock()
        self.corpora: list[dict] = []
        self.state: str = "disabled"  # disabled / ok / error:<kind>
        self.refreshed_at: datetime | None = None
        self.error: str | None = None
        self._known: set[str] = set()  # 历次刷新见过的语料库标识（区分「已消失」与「从未见过」）

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "corpora": [dict(c) for c in self.corpora],
                "state": self.state,
                "refreshed_at": self.refreshed_at.isoformat() if self.refreshed_at else None,
                "error": self.error,
            }

    def get_corpus_status(self, corpus_id: str) -> str | None:
        """语料库快照状态：active / archived / missing（曾在历次快照中出现、现已不在）。

        从未见过的语料库返回 None（此时挂接失效判定按未失效、创建校验回落实时查询）。
        """
        with self._lock:
            for c in self.corpora:
                if c.get("corpus_id") == corpus_id:
                    return "archived" if c.get("archived") else "active"
            if corpus_id in self._known:
                return "missing"
        return None

    def set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self.state = state
            self.error = error

    def update(self, corpora: list[dict]) -> None:
        with self._lock:
            self.corpora = [dict(c) for c in corpora]
            self._known.update(c.get("corpus_id", "") for c in self.corpora)
            self.state = "ok"
            self.refreshed_at = _now()
            self.error = None


snapshot_singleton = ExternalKbSnapshot()


async def refresh_snapshot() -> None:
    """读当前连接配置刷新快照；未启用置 disabled；外部失败保留旧快照并显式记错误。"""
    from app.deps import get_external_kb_client

    client = get_external_kb_client()
    if client is None:
        snapshot_singleton.set_state("disabled")
        return
    try:
        corpora = await asyncio.to_thread(client.list_corpora)
    except ExternalKbError as exc:
        snapshot_singleton.set_state(f"error:{exc.kind}", str(exc))
        return
    except Exception as exc:  # 兜底：任何意外都不终止循环
        snapshot_singleton.set_state("error:error", str(exc))
        return
    snapshot_singleton.update(corpora)


_loop = None
_snapshot_task = None


async def _snapshot_loop() -> None:
    while True:
        try:
            await refresh_snapshot()
        except Exception:
            pass  # 循环永不因单次刷新异常而终止
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


def ensure_snapshot_loop() -> None:
    """启动全局唯一后台循环（进程内一份；测试环境不跑 lifespan 则不启动）。"""
    global _loop, _snapshot_task
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
        return
    if _snapshot_task is None or _snapshot_task.done():
        _snapshot_task = _loop.create_task(_snapshot_loop())


def stop_snapshot_loop() -> None:
    global _snapshot_task
    if _snapshot_task is not None and not _snapshot_task.done():
        _snapshot_task.cancel()
    _snapshot_task = None


def kick_snapshot_refresh() -> None:
    """保存连接配置后触发一次即时后台刷新（线程安全，不等结果；无循环时不动作）。"""
    global _loop
    if _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(refresh_snapshot)
