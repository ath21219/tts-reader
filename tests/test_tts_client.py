"""TTSクライアントのテスト"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tts_reader.core.config import TTSConfig
from tts_reader.core.models import TTSRequest
from tts_reader.core.tts_client import TTSClient


# ---------------------------------------------------------------------------
# モックTTSサーバ
# ---------------------------------------------------------------------------

# アプリ起動後に app[key] へ代入すると DeprecationWarning が出るため、
# 起動前に設置したミュータブルオブジェクトの中身を更新する方式にする。

class RequestStore:
    """最後のリクエストを保持する入れ物"""

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None


def create_mock_tts_app(store: RequestStore) -> web.Application:
    app = web.Application()

    async def handle_speech(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        if "input" not in body or not body["input"]:
            return web.json_response(
                {"error": "input is required"}, status=400,
            )
        # app の状態ではなく外部オブジェクトに記録
        store.last = body

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "audio/mpeg"},
        )
        await response.prepare(request)
        dummy_data = b"\xff\xfb\x90\x00" * 50
        chunk_size = len(dummy_data) // 3
        for i in range(0, len(dummy_data), chunk_size):
            await response.write(dummy_data[i : i + chunk_size])
            await asyncio.sleep(0.01)
        await response.write_eof()
        return response

    app.router.add_post("/v1/audio/speech", handle_speech)
    return app


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

@pytest.fixture
def request_store() -> RequestStore:
    return RequestStore()


@pytest.fixture
async def mock_server(aiohttp_server: Any, request_store: RequestStore) -> TestServer:
    app = create_mock_tts_app(request_store)
    server = await aiohttp_server(app)
    return server


@pytest.fixture
def client_config(mock_server: TestServer) -> TTSConfig:
    host = mock_server.host
    port = mock_server.port
    return TTSConfig(
        server_url=f"http://{host}:{port}",
        endpoint="/v1/audio/speech",
        voice="nova",
        model="tts-1",
        response_format="mp3",
        caption="test",
        seed=42,
    )


class TestTTSClientBuildRequest:

    def test_defaults_from_config(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = client.build_request("hello")
        assert req.text == "hello"
        assert req.caption == "test caption"
        assert req.seed == 42
        assert req.voice == "alloy"

    def test_override_caption_and_seed(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = client.build_request("hello", caption="override", seed=999)
        assert req.caption == "override"
        assert req.seed == 999

    def test_empty_caption_override(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = client.build_request("hello", caption="")
        assert req.caption == ""


class TestTTSClientPayload:

    def test_payload_contains_required_fields(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = TTSRequest(
            text="hello", voice="nova", model="tts-1",
            response_format="mp3", speed=1.0,
        )
        payload = client._build_payload(req)
        assert payload["input"] == "hello"
        assert payload["voice"] == "nova"
        assert payload["model"] == "tts-1"
        assert payload["response_format"] == "mp3"
        assert payload["speed"] == 1.0

    def test_payload_includes_caption_when_set(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = TTSRequest(text="hello", caption="speak softly")
        payload = client._build_payload(req)
        assert payload["caption"] == "speak softly"

    def test_payload_excludes_caption_when_empty(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = TTSRequest(text="hello", caption="")
        payload = client._build_payload(req)
        assert "caption" not in payload

    def test_payload_includes_seed_when_set(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = TTSRequest(text="hello", seed=123)
        payload = client._build_payload(req)
        assert payload["seed"] == 123

    def test_payload_excludes_seed_when_none(self, tts_config: TTSConfig) -> None:
        client = TTSClient(tts_config)
        req = TTSRequest(text="hello", seed=None)
        payload = client._build_payload(req)
        assert "seed" not in payload


@pytest.mark.asyncio
class TestTTSClientSynthesize:

    async def test_synthesize_returns_audio_data(
        self, mock_server: TestServer, client_config: TTSConfig,
    ) -> None:
        client = TTSClient(client_config)
        await client.start()
        try:
            req = client.build_request("テストテキスト")
            data = await client.synthesize(req)
            assert len(data) > 0
            assert isinstance(data, bytes)
        finally:
            await client.close()

    async def test_synthesize_stream_yields_chunks(
        self, mock_server: TestServer, client_config: TTSConfig,
    ) -> None:
        client = TTSClient(client_config)
        await client.start()
        try:
            req = client.build_request("ストリーミングテスト")
            chunks: list[bytes] = []
            async for chunk in client.synthesize_stream(req):
                chunks.append(chunk)
            assert len(chunks) > 1
            assert all(len(c) > 0 for c in chunks)
        finally:
            await client.close()

    async def test_server_receives_correct_payload(
        self, mock_server: TestServer, client_config: TTSConfig,
        request_store: RequestStore,
    ) -> None:
        client = TTSClient(client_config)
        await client.start()
        try:
            req = client.build_request("ペイロード検証", caption="custom", seed=99)
            await client.synthesize(req)
            assert request_store.last is not None
            assert request_store.last["input"] == "ペイロード検証"
            assert request_store.last["caption"] == "custom"
            assert request_store.last["seed"] == 99
            assert request_store.last["voice"] == "nova"
        finally:
            await client.close()

    async def test_auto_start_on_synthesize(
        self, mock_server: TestServer, client_config: TTSConfig,
    ) -> None:
        client = TTSClient(client_config)
        try:
            req = client.build_request("自動起動テスト")
            data = await client.synthesize(req)
            assert len(data) > 0
        finally:
            await client.close()
