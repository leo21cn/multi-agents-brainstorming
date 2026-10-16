import asyncio
import json

import pytest

from app.models.asr import AsrError, DashScopeStreamingAsrProvider, DemoAsrProvider


def test_demo_provider_deterministic():
    p = DemoAsrProvider()
    assert p.transcribe(b"\x00\x00") == DemoAsrProvider.TEXT


def test_empty_pcm_rejected():
    with pytest.raises(AsrError):
        DashScopeStreamingAsrProvider("k", "qwen-audio-3.0-asr-flash-streaming").transcribe(b"")


async def _serve(handler):
    import websockets

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def test_batch_transcribe_via_task_protocol():
    """批量转写按任务制协议走一遍：run-task → 二进制帧 → finish-task → 定稿文本。"""
    received = {}

    async def handler(ws):
        run = json.loads(await ws.recv())
        assert run["header"]["action"] == "run-task"
        received["model"] = run["payload"]["model"]
        received["params"] = run["payload"]["parameters"]
        received["input"] = run["payload"]["input"]
        task_id = run["header"]["task_id"]
        await ws.send(
            json.dumps({"header": {"event": "task-started", "task_id": task_id}, "payload": {}})
        )
        frames = []
        while True:
            frame = await ws.recv()
            if isinstance(frame, str):
                fin = json.loads(frame)
                assert fin["header"]["action"] == "finish-task"
                assert fin["payload"]["input"] == {}
                break
            frames.append(frame)
        received["audio"] = b"".join(frames)
        await ws.send(
            json.dumps(
                {
                    "header": {"event": "result-generated", "task_id": task_id},
                    "payload": {"output": {"sentence": {"text": "第一句，", "end_time": None}}},
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "header": {"event": "result-generated", "task_id": task_id},
                    "payload": {"output": {"sentence": {"text": "第一句，第二句。", "end_time": 4800}}},
                }
            )
        )
        await ws.send(
            json.dumps({"header": {"event": "task-finished", "task_id": task_id}, "payload": {}})
        )

    async def main():
        server, port = await _serve(handler)
        provider = DashScopeStreamingAsrProvider("key-1", "qwen-audio-3.0-asr-flash-streaming")
        provider.WS_URL = f"ws://127.0.0.1:{port}/api-ws/v1/inference"
        pcm = b"\x01\x02" * 4000
        text = await provider._batch(pcm)
        assert text == "第一句，第二句。"
        assert received["audio"] == pcm
        assert received["model"] == "qwen-audio-3.0-asr-flash-streaming"
        assert received["params"] == {"format": "pcm", "sample_rate": 16000}
        assert received["input"] == {}
        server.close()
        await server.wait_closed()

    asyncio.run(main())


def test_stream_callbacks_incremental():
    """流式会话：说话期间 result-generated 逐条回调增量文本，finish 返回定稿。"""
    callbacks = []

    async def handler(ws):
        run = json.loads(await ws.recv())
        task_id = run["header"]["task_id"]
        await ws.send(
            json.dumps({"header": {"event": "task-started", "task_id": task_id}, "payload": {}})
        )
        await ws.send(
            json.dumps(
                {
                    "header": {"event": "result-generated", "task_id": task_id},
                    "payload": {"output": {"sentence": {"text": "今天", "end_time": None}}},
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "header": {"event": "result-generated", "task_id": task_id},
                    "payload": {"output": {"sentence": {"text": "今天天气不错。", "end_time": 4800}}},
                }
            )
        )
        await ws.recv()  # 忽略二进制帧
        fin = json.loads(await ws.recv())
        assert fin["header"]["action"] == "finish-task"
        await ws.send(
            json.dumps({"header": {"event": "task-finished", "task_id": task_id}, "payload": {}})
        )

    async def main():
        server, port = await _serve(handler)
        provider = DashScopeStreamingAsrProvider("key-1", "qwen-audio-3.0-asr-flash-streaming")
        provider.WS_URL = f"ws://127.0.0.1:{port}/api-ws/v1/inference"
        stream = provider.open_stream(lambda text, partial: callbacks.append((text, partial)))
        await stream.open()
        await stream.send_frame(b"\x01\x02" * 1600)
        final = await stream.finish()
        assert final == "今天天气不错。"
        assert callbacks == [("今天", True), ("今天天气不错。", False)]
        server.close()
        await server.wait_closed()

    asyncio.run(main())


def test_stream_task_failed_raises():
    async def handler(ws):
        run = json.loads(await ws.recv())
        task_id = run["header"]["task_id"]
        await ws.send(
            json.dumps(
                {
                    "header": {"event": "task-failed", "task_id": task_id, "error_message": "bad model"},
                    "payload": {},
                }
            )
        )

    async def main():
        server, port = await _serve(handler)
        provider = DashScopeStreamingAsrProvider("key-1", "qwen-audio-3.0-asr-flash-streaming")
        provider.WS_URL = f"ws://127.0.0.1:{port}/api-ws/v1/inference"
        stream = provider.open_stream(lambda text, partial: None)
        with pytest.raises(AsrError):
            await stream.open()
        await stream.abort()
        server.close()
        await server.wait_closed()

    asyncio.run(main())
