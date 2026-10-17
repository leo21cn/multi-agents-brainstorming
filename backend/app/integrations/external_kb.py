"""外部中心知识库（my-center-knowledge-lib）客户端封装。

组合检索的外部源：连接配置（URL/令牌/启用）取系统设置；只读调用语料库列表、
连通探测与语义检索，不做同步触发等浅管理（需求定案：实时访问）。

全部为同步 httpx：检索在既有工作线程内执行，同步调用即可，不引入事件循环。
错误按类型分类（unreachable/unauthorized/timeout/bad_response），供检索层显式降级。
"""

from __future__ import annotations

import httpx

CONNECT_TIMEOUT = 3.0
TOTAL_TIMEOUT = 8.0


class ExternalKbError(Exception):
    """外部调用失败；kind 为分类，供降级标注与界面文案映射。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # unreachable / unauthorized / timeout / bad_response / not_found


class ExternalKbClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=(CONNECT_TIMEOUT, TOTAL_TIMEOUT),
                **kwargs,
            )
        except httpx.ConnectError as exc:
            raise ExternalKbError("unreachable", f"无法连接外部知识库：{exc}") from exc
        except httpx.ConnectTimeout as exc:
            raise ExternalKbError("timeout", "连接外部知识库超时") from exc
        except httpx.ReadTimeout as exc:
            raise ExternalKbError("timeout", "外部知识库响应超时") from exc
        except httpx.HTTPError as exc:
            raise ExternalKbError("unreachable", f"无法连接外部知识库：{exc}") from exc
        if resp.status_code == 401:
            raise ExternalKbError("unauthorized", "外部知识库令牌无效")
        if resp.status_code == 404:
            raise ExternalKbError("not_found", "外部语料库不存在")
        if resp.status_code >= 400:
            try:
                payload = resp.json()
                message = (
                    (payload.get("error") or {}).get("message")
                    or payload.get("detail")
                    or f"HTTP {resp.status_code}"
                )
            except ValueError:
                message = f"HTTP {resp.status_code}"
            raise ExternalKbError("bad_response", f"外部知识库返回错误：{message}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ExternalKbError("bad_response", "外部知识库响应不是合法 JSON") from exc

    def healthz(self) -> bool:
        try:
            resp = httpx.get(
                f"{self.base_url}/healthz", timeout=(CONNECT_TIMEOUT, CONNECT_TIMEOUT)
            )
        except httpx.HTTPError as exc:
            raise ExternalKbError("unreachable", f"无法连接外部知识库：{exc}") from exc
        if resp.status_code != 200:
            raise ExternalKbError("bad_response", f"外部健康检查返回 HTTP {resp.status_code}")
        return True

    def list_corpora(self) -> list[dict]:
        payload = self._request("GET", "/api/v1/corpora")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ExternalKbError("bad_response", "外部语料库列表形态异常")
        return data

    def get_corpus(self, corpus_id: str) -> dict | None:
        try:
            payload = self._request("GET", f"/api/v1/corpora/{corpus_id}")
        except ExternalKbError as exc:
            if exc.kind == "not_found":
                return None
            raise
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def search(self, corpus_id: str, query: str, top_k: int) -> list[dict]:
        payload = self._request(
            "POST",
            "/api/v1/search",
            json={"corpus_id": corpus_id, "query": query, "top_k": top_k, "filters": {}},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise ExternalKbError("bad_response", "外部检索结果形态异常")
        return data
