"""共通フィクスチャ"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tts_reader.core.config import AppConfig, PlaybackConfig, TTSConfig
from tts_reader.core.models import AudioSegment, ChunkType, TextChunk, TTSRequest
from tts_reader.core.audio_player import AudioPlayer
from tts_reader.core.tts_client import TTSClient
from tts_reader.core.orchestrator import Orchestrator
from tts_reader.core.text_parser import MarkdownTextParser


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tts_config() -> TTSConfig:
    return TTSConfig(
        server_url="http://localhost:9999",
        voice="alloy",
        model="tts-1",
        response_format="mp3",
        caption="test caption",
        seed=42,
    )


@pytest.fixture
def playback_config() -> PlaybackConfig:
    return PlaybackConfig(
        buffer_ahead=1,
        default_pause=0.1,
        heading_pause=0.2,
        section_pause=0.3,
        paragraph_pause=0.1,
    )


@pytest.fixture
def app_config(tts_config: TTSConfig, playback_config: PlaybackConfig) -> AppConfig:
    return AppConfig(tts=tts_config, playback=playback_config)


# ---------------------------------------------------------------------------
# Parser fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parser(playback_config: PlaybackConfig) -> MarkdownTextParser:
    return MarkdownTextParser(playback_config)


# ---------------------------------------------------------------------------
# Mock TTS Client
# ---------------------------------------------------------------------------

class MockTTSClient:
    """TTSサーバの応答をシミュレートするモッククライアント"""

    def __init__(self, config: TTSConfig | None = None) -> None:
        self._config = config or TTSConfig()
        self.requests: list[TTSRequest] = []
        self._audio_data = b"\xff\xfb\x90\x00" * 100

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def build_request(
        self,
        text: str,
        *,
        caption: str | None = None,
        seed: int | None = None,
    ) -> TTSRequest:
        req = TTSRequest(
            text=text,
            caption=caption if caption is not None else self._config.caption,
            seed=seed if seed is not None else self._config.seed,
            voice=self._config.voice,
            model=self._config.model,
            response_format=self._config.response_format,
            speed=self._config.speed,
        )
        self.requests.append(req)
        return req

    async def synthesize_stream(
        self, request: TTSRequest, chunk_size: int = 4096,
    ) -> AsyncIterator[bytes]:
        mid = len(self._audio_data) // 2
        yield self._audio_data[:mid]
        await asyncio.sleep(0.01)
        yield self._audio_data[mid:]

    async def synthesize(self, request: TTSRequest) -> bytes:
        return self._audio_data


@pytest.fixture
def mock_tts_client(tts_config: TTSConfig) -> MockTTSClient:
    return MockTTSClient(tts_config)


# ---------------------------------------------------------------------------
# Mock Audio Backend / Player
# ---------------------------------------------------------------------------

class MockAudioBackend:
    """音声バックエンドのモック

    _MiniaudioBackend と同じインターフェースを持つ。
    play() → wait_until_done() の呼び出しパターンに対応する。

    [CHANGED] デバイス使い回し対応のフォーマットフィールドを追加。
    本番コードの _MiniaudioBackend と同じプロパティを持たせることで、
    テスト時にも同一のインターフェースを保証する。
    """

    def __init__(self) -> None:
        self.played: list[bytes] = []
        self._done_event = threading.Event()
        self._done_event.set()
        # [CHANGED] 本番コードとインターフェースを合わせる
        self._sample_format: Any = None
        self._nchannels: int = 0
        self._sample_rate: int = 0

    def ensure_init(self) -> None:
        pass

    def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
        self.played.append(audio_data)
        self._done_event.clear()
        # モックなので即座に「再生完了」とする
        self._done_event.set()

    def wait_until_done(self) -> None:
        self._done_event.wait(timeout=5.0)

    def stop(self) -> None:
        self._done_event.set()

    def quit(self) -> None:
        self._done_event.set()


@pytest.fixture
def mock_audio_backend() -> MockAudioBackend:
    return MockAudioBackend()


@pytest.fixture
def mock_audio_player(mock_audio_backend: MockAudioBackend) -> AudioPlayer:
    return AudioPlayer(backend=mock_audio_backend)


# ---------------------------------------------------------------------------
# Orchestrator with mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def orchestrator(
    app_config: AppConfig,
    parser: MarkdownTextParser,
    mock_tts_client: MockTTSClient,
    mock_audio_player: AudioPlayer,
) -> Orchestrator:
    return Orchestrator(
        config=app_config,
        parser=parser,
        tts_client=mock_tts_client,  # type: ignore[arg-type]
        audio_player=mock_audio_player,
    )


# ---------------------------------------------------------------------------
# Sample texts
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_markdown() -> str:
    text = (
        "# 第1章 はじめに\n"
        "\n"
        "これはテスト文です。複数の文を含みます。\n"
        "\n"
        "## 1.1 詳細\n"
        "\n"
        "詳細な説明がここにあります。\n"
        "\n"
        "```python\n"
        'print("hello")\n'
        "```\n"
        "\n"
        "<!-- コメント -->\n"
        "\n"
        "最後の段落です。\n"
    )
    return text


@pytest.fixture
def sample_plain_text() -> str:
    text = "これは単純なテキストです。二文目です。三文目です。"
    return text


@pytest.fixture
def sample_html_mixed() -> str:
    text = (
        '<meta charset="utf-8">\n'
        "<style>body { color: red; }</style>\n"
        "\n"
        "# タイトル\n"
        "\n"
        "本文テキストです。\n"
        "\n"
        "![画像](https://example.com/image.png)\n"
        "\n"
        "続きのテキスト。\n"
    )
    return text
