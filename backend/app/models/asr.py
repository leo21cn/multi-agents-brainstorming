"""语音转写：AsrProvider 协议、DashScope 实时流式识别实现与离线演示实现。

Qwen-Audio-3.0-ASR-Flash-Streaming（`qwen-audio-3.0-asr-flash-streaming`）走
`/api-ws/v1/inference` 任务制双工协议：run-task → 二进制 PCM 帧 → finish-task；
增量结果以 `result-generated` 事件携带 `payload.output.sentence.text` 逐句下发。

`AsrStream` 在后台协程持续读事件，说话期间即经回调逐段吐出转写文本；`finish()`
发送 finish-task、收尾并返回定稿。批量转写（REST `/rooms/{id}/asr`）复用同一
会话：逐帧喂完即 finish。音频只在内存中处理，不落盘、不入日志。
"""

import asyncio
import json
import time
import uuid


class AsrError(Exception):
    """转写失败；消息即界面呈现的原因。"""


class AsrStream:
    """一次流式转写会话（异步）：open 后逐帧发送，增量经 on_text(text, partial) 回调。"""

    START_TIMEOUT = 15.0
    FINISH_TIMEOUT = 60.0

    def __init__(self, provider, on_text):
        self._provider = provider
        self._on_text = on_text
        self._ws = None
        self._task_id = None
        self._reader = None
        self._done_ev = asyncio.Event()
        self._error: str | None = None
        self._done_texts: list[str] = []
        self._partial = ""

    async def open(self) -> None:
        import websockets

        self._ws = await websockets.connect(
            self._provider.WS_URL,
            additional_headers={"Authorization": f"Bearer {self._provider.api_key}"},
            open_timeout=10,
        )
        self._task_id = uuid.uuid4().hex
        run_task = {
            "header": {"task_id": self._task_id, "action": "run-task", "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._provider.model,
                "parameters": {"format": "pcm", "sample_rate": 16000},
                # 该端点校验 payload.input 必填，且要求其位于 parameters 之后
                "input": {},
            },
        }
        await self._ws.send(json.dumps(run_task, ensure_ascii=False))
        deadline = time.monotonic() + self.START_TIMEOUT
        while time.monotonic() < deadline:
            msg = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=5))
            event = msg.get("header", {}).get("event")
            if event in ("task-started", "task-finished"):
                break
            if event == "task-failed":
                raise AsrError(
                    "转写任务启动失败："
                    + str(msg.get("header", {}).get("error_message") or "task-failed")
                )
        else:
            raise AsrError("转写任务未启动或超时")
        self._reader = asyncio.create_task(self._read_loop())

    async def _emit(self, text: str, partial: bool) -> None:
        """回调 on_text；回调可为同步或协程，协程必须 await 才能把增量发出去。"""
        import inspect

        result = self._on_text(text, partial)
        if inspect.isawaitable(result):
            await result

    async def _read_loop(self) -> None:
        """后台读事件：result-generated 逐条回调增量文本，任务终结置事件。"""
        try:
            while True:
                msg = json.loads(await self._ws.recv())
                event = msg.get("header", {}).get("event")
                if event == "result-generated":
                    text, end = self._sentence_of(msg)
                    if text:
                        if end:
                            self._done_texts.append(text)
                            self._partial = ""
                        else:
                            self._partial = text
                        current = "".join(self._done_texts) + self._partial
                        await self._emit(current, not end)
                elif event == "task-finished":
                    self._done_ev.set()
                    break
                elif event == "task-failed":
                    self._error = "转写失败：" + str(
                        msg.get("header", {}).get("error_message") or "task-failed"
                    )
                    self._done_ev.set()
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = f"语音转写失败：{exc}"
            self._done_ev.set()

    @staticmethod
    def _sentence_of(msg: dict) -> tuple[str, bool]:
        out = msg.get("payload", {}).get("output", {})
        sentence = out.get("sentence")
        if isinstance(sentence, dict) and isinstance(sentence.get("text"), str):
            end = sentence.get("sentence_end") is True or sentence.get("end_time") is not None
            return sentence["text"], end
        if isinstance(out.get("text"), str):
            return out["text"], False
        return "", False

    async def send_frame(self, data: bytes) -> None:
        if self._ws is None:
            raise AsrError("转写会话未启动")
        await self._ws.send(data)

    async def finish(self) -> str:
        """发送 finish-task，等收尾事件，返回定稿文本（无词时抛 AsrError）。"""
        finish_task = {
            "header": {"task_id": self._task_id, "action": "finish-task"},
            # 该端点对 finish-task 同样校验 payload.input 必填
            "payload": {"input": {}},
        }
        await self._ws.send(json.dumps(finish_task, ensure_ascii=False))
        try:
            await asyncio.wait_for(self._done_ev.wait(), timeout=self.FINISH_TIMEOUT)
        except asyncio.TimeoutError:
            self._error = "转写超时，请重试"
        if self._error is not None:
            raise AsrError(self._error)
        text = "".join(self._done_texts) + self._partial
        text = text.strip()
        if not text:
            raise AsrError("未识别到有效内容")
        return text

    async def abort(self) -> None:
        """连接断开或异常时收尾：取消读循环并关闭连接。"""
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


class AsrProvider:
    """语音转写提供方协议：批量转写 + 流式会话。"""

    def transcribe(self, pcm: bytes) -> str:  # pragma: no cover - 协议
        raise NotImplementedError

    def open_stream(self, on_text) -> AsrStream:  # pragma: no cover - 协议
        raise NotImplementedError


class DashScopeStreamingAsrProvider(AsrProvider):
    """DashScope Qwen-Audio-3.0-ASR-Flash-Streaming（模型名取系统设置）。"""

    WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    FRAME = 3200  # 100ms @ 16kHz 单声道 s16le

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def open_stream(self, on_text) -> AsrStream:
        return AsrStream(self, on_text)

    def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            raise AsrError("录音为空")
        return asyncio.run(self._batch(pcm))

    async def _batch(self, pcm: bytes) -> str:
        stream = self.open_stream(lambda text, partial: None)
        try:
            await stream.open()
            for i in range(0, len(pcm), self.FRAME):
                await stream.send_frame(pcm[i : i + self.FRAME])
            return await stream.finish()
        finally:
            await stream.abort()


class DemoAsrProvider(AsrProvider):
    """离线演示转写：无凭据时供链路验证，返回确定性占位文本。"""

    TEXT = "这是离线演示的语音转写结果（未配置可用的转写凭据）。"

    def open_stream(self, on_text):
        return _DemoAsrStream(on_text)

    def transcribe(self, pcm: bytes) -> str:
        return self.TEXT


class _DemoAsrStream(AsrStream):
    """演示流式会话：不动网络，finish 时一次性回调占位文本。"""

    def __init__(self, on_text):
        self._on_text = on_text

    async def open(self) -> None:
        return None

    async def send_frame(self, data: bytes) -> None:
        return None

    async def finish(self) -> str:
        await self._emit(DemoAsrProvider.TEXT, True)
        return DemoAsrProvider.TEXT

    async def abort(self) -> None:
        return None
