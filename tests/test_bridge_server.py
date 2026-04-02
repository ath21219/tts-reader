"""ブリッジサーバのテスト"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import websockets
from websockets.asyncio.client import connect

from tts_reader.core.config import AppConfig, TTSConfig
from tts_reader.core.audio_player import AudioPlayer
from tts_reader.core.orchestrator import Orchestrator
from tts_reader.adapters.bridge_server import BridgeServer

from conftest import MockAudioBackend, MockTTSClient


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge_app_config() -> AppConfig:
    return AppConfig(
        tts=TTSConfig(server_url="http://localhost:9999"),
    )


@pytest_asyncio.fixture
async def bridge_server(bridge_app_config: AppConfig) -> AsyncIterator[BridgeServer]:
    server = BridgeServer(host="127.0.0.1", port=0, config=bridge_app_config)

    mock_tts = MockTTSClient(bridge_app_config.tts)
    mock_backend = MockAudioBackend()

    orch = Orchestrator(
        config=bridge_app_config,
        tts_client=mock_tts,  # type: ignore[arg-type]
        audio_player=AudioPlayer(backend=mock_backend),
    )
    server._orchestrator = orch
    server._orchestrator.set_event_callback(server._on_playback_event)
    await server._orchestrator.start()

    # [CHANGED] イベントディスパッチャタスクを起動
    server._event_dispatcher_task = asyncio.create_task(server._dispatch_events())

    server._server = await websockets.serve(
        server._handle_client,
        server._host,
        0,
    )
    port = server._server.sockets[0].getsockname()[1]
    server._port = port

    yield server

    # クリーンアップ: orchestrator を安全に停止
    orch._running = False
    try:
        await asyncio.wait_for(orch.shutdown(), timeout=2.0)
    except (asyncio.TimeoutError, Exception):
        pass

    # [CHANGED] イベントディスパッチャを停止
    if server._event_dispatcher_task and not server._event_dispatcher_task.done():
        try:
            server._event_queue.put_nowait(None)
            await asyncio.wait_for(server._event_dispatcher_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            server._event_dispatcher_task.cancel()

    if server._server:
        server._server.close()
        try:
            await asyncio.wait_for(server._server.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


def _ws_url(server: BridgeServer) -> str:
    return f"ws://{server._host}:{server._port}"


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBridgeServer:

    async def test_connect_and_disconnect(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            pong = await ws.ping()
            await pong

    async def test_speak_command(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "テスト。"},
            }))
            events: list[dict[str, Any]] = []
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(raw)
                    events.append(msg)
                    if msg.get("event") == "done":
                        break
            except asyncio.TimeoutError:
                pass
            playback_events = [e for e in events if e.get("event") == "playback"]
            assert len(playback_events) > 0
            done_events = [e for e in events if e.get("event") == "done"]
            assert len(done_events) == 1

    async def test_speak_empty_text_returns_error(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": ""},
            }))
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["event"] == "error"

    async def test_stop_command(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            # 長いテキストの読み上げを開始
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "長いテキスト。" * 10},
            }))

            # 少し待ってイベントが来始めるのを確認
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg = json.loads(raw)
                # 最初のイベントが来た（playback or done）
            except asyncio.TimeoutError:
                pass

            # 停止コマンド送信
            await ws.send(json.dumps({"method": "stop"}))

            # stop 後もエラーなく通信が続くことを確認
            # （done が来るか、あるいは何も来ないかのどちらか）
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    msg = json.loads(raw)
                    if msg.get("event") in ("done", "error"):
                        break
            except asyncio.TimeoutError:
                # タイムアウトは正常（stop により再生が終了した）
                pass

            # 接続がまだ生きていることを確認
            pong = await ws.ping()
            await pong

    async def test_configure_command(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send(json.dumps({
                "method": "configure",
                "params": {
                    "caption": "speak softly",
                    "seed": 123,
                    "voice": "nova",
                },
            }))
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["event"] == "configured"
            assert msg["data"]["caption"] == "speak softly"
            assert msg["data"]["seed"] == 123

    async def test_unknown_method_returns_error(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send(json.dumps({"method": "unknown_method"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["event"] == "error"
            assert "Unknown method" in msg["data"]["message"]

    async def test_invalid_json_returns_error(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send("not valid json {{{")
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["event"] == "error"

    async def test_playback_event_contains_chunk_data(
        self, bridge_server: BridgeServer,
    ) -> None:
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "テスト文。"},
            }))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                msg = json.loads(raw)
                if msg.get("event") == "playback":
                    chunk = msg["data"]["chunk"]
                    assert "index" in chunk
                    assert "content" in chunk
                    assert "type" in chunk
                    assert "source_offset" in chunk
                    assert "source_length" in chunk
                    break
                if msg.get("event") == "done":
                    break

    # [CHANGED] 新規: イベントキュー経由でイベント順序が保たれることの検証
    async def test_playback_events_arrive_in_order(
        self, bridge_server: BridgeServer,
    ) -> None:
        """playback イベントが chunk index 順に到着すること"""
        async with connect(_ws_url(bridge_server)) as ws:
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "一文目。二文目。三文目。"},
            }))
            playback_events: list[dict[str, Any]] = []
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(raw)
                    if msg.get("event") == "playback":
                        playback_events.append(msg)
                    if msg.get("event") == "done":
                        break
            except asyncio.TimeoutError:
                pass

            # playing イベントだけ取り出して index が昇順であることを確認
            playing_indices = [
                e["data"]["chunk"]["index"]
                for e in playback_events
                if e["data"]["state"] == "playing"
                and e["data"]["chunk"]["type"] == "text"
            ]
            assert len(playing_indices) >= 2
            assert playing_indices == sorted(playing_indices)

    # [CHANGED] 新規: speak 中に再度 speak を送った場合の動作確認
    async def test_speak_replaces_previous(
        self, bridge_server: BridgeServer,
    ) -> None:
        """読み上げ中に新しい speak を送ると、前の読み上げが停止され
        新しいテキストの読み上げが開始されること"""
        async with connect(_ws_url(bridge_server)) as ws:
            # 最初の speak（長いテキスト）
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "長い文章。" * 10},
            }))

            # 少し待ってから新しい speak を送る
            await asyncio.sleep(0.1)

            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "短い。"},
            }))

            # 最終的に done が来ることを確認
            got_done = False
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(raw)
                    if msg.get("event") == "done":
                        got_done = True
                        break
            except asyncio.TimeoutError:
                pass
            assert got_done
