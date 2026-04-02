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

    server._server = await websockets.serve(
        server._handle_client,
        server._host,
        0,
    )
    port = server._server.sockets[0].getsockname()[1]
    server._port = port

    yield server

    await server.stop()


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
            # 接続確認: ping が成功すれば接続中
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
                async for raw in ws:
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
            await ws.send(json.dumps({
                "method": "speak",
                "params": {"text": "長いテキスト。" * 10},
            }))
            await asyncio.sleep(0.1)
            await ws.send(json.dumps({"method": "stop"}))
            # 接続確認: stopの後もpingが通ること
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
