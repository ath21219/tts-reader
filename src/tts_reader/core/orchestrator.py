"""全体統括・パイプライン制御"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from tts_reader.core.audio_player import AudioPlayer
from tts_reader.core.config import AppConfig
from tts_reader.core.models import (
    AudioSegment,
    ChunkType,
    PlaybackEvent,
    PlaybackState,
    TextChunk,
)
from tts_reader.core.text_parser import MarkdownTextParser, TextParserProtocol
from tts_reader.core.tts_client import TTSClient

logger = logging.getLogger(__name__)


class Orchestrator:
    """読み上げパイプラインの全体統括"""

    def __init__(
        self,
        config: AppConfig | None = None,
        parser: TextParserProtocol | None = None,
        tts_client: TTSClient | None = None,
        audio_player: AudioPlayer | None = None,
    ) -> None:
        self._config = config or AppConfig()
        self._parser = parser or MarkdownTextParser(self._config.playback)
        self._tts = tts_client or TTSClient(self._config.tts)
        self._player = audio_player or AudioPlayer()

        self._play_queue: asyncio.Queue[AudioSegment | None] = asyncio.Queue()
        self._chunks: list[TextChunk] = []
        self._running = False
        self._current_chunk_index: int = -1
        self._on_event: Callable[[PlaybackEvent], Any] | None = None
        self._speak_task: asyncio.Task[None] | None = None
        self._player.set_state_callback(self._on_playback_state)

    # ----- public -----------------------------------------------------------

    def set_event_callback(
        self, callback: Callable[[PlaybackEvent], Any],
    ) -> None:
        self._on_event = callback

    async def start(self) -> None:
        await self._tts.start()

    async def shutdown(self) -> None:
        self._running = False
        await self._player.shutdown()
        await self._tts.close()

    async def speak(self, text: str) -> None:
        """テキスト全体を読み上げる（メインエントリポイント）"""
        self._running = True

        buffer_ahead = max(1, self._config.playback.buffer_ahead)
        self._play_queue = asyncio.Queue(maxsize=buffer_ahead)

        self._chunks = self._parser.parse(text)
        if not self._chunks:
            logger.warning("No speakable chunks found.")
            return

        logger.info("Parsed %d chunks.", len(self._chunks))

        producer = asyncio.create_task(self._produce())
        consumer = asyncio.create_task(self._consume())

        try:
            await asyncio.gather(producer, consumer)
        except asyncio.CancelledError:
            logger.info("Speak cancelled.")
        finally:
            self._running = False

    async def stop(self) -> None:
        """再生を停止する。

        _running を False にしてから、Producer / Consumer が
        ブロックしている可能性のあるキューを強制的に解放する。
        """
        self._running = False
        await self._player.stop()

        # キューを空にする（Producer の put() がブロック中なら解放される）
        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Consumer を終了させるために None を投入する。
        # キューが満杯の場合はまず1つ取り出してスペースを作る。
        for _ in range(2):
            try:
                self._play_queue.put_nowait(None)
                break
            except asyncio.QueueFull:
                try:
                    self._play_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    # ----- pipeline stages --------------------------------------------------

    async def _produce(self) -> None:
        """チャンクを順に1つずつ生成し、キューに投入する。"""
        for chunk in self._chunks:
            if not self._running:
                break
            segment = await self._synthesize_chunk(chunk)
            if not self._running:
                break
            try:
                await asyncio.wait_for(
                    self._play_queue.put(segment),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                if not self._running:
                    break
                # running 中のタイムアウトはリトライ
                await self._play_queue.put(segment)

        # 終端マーカー
        if self._running:
            await self._play_queue.put(None)

    async def _consume(self) -> None:
        """バッファから音声セグメントを取り出して順に再生する"""
        while self._running:
            try:
                segment: AudioSegment | None = await asyncio.wait_for(
                    self._play_queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                if not self._running:
                    break
                continue
            if segment is None:
                break
            if not self._running:
                break
            self._current_chunk_index = segment.chunk.index
            await self._player.play_segment(segment)

    async def _synthesize_chunk(self, chunk: TextChunk) -> AudioSegment:
        """1チャンクに対応する AudioSegment を生成する"""
        segment = AudioSegment(chunk=chunk)

        if chunk.chunk_type == ChunkType.PAUSE:
            segment.is_ready = True
            return segment

        if chunk.chunk_type == ChunkType.SKIP:
            segment.is_ready = True
            return segment

        request = self._tts.build_request(text=chunk.content)
        try:
            async for data in self._tts.synthesize_stream(request):
                segment.append(data)
            segment.finalize()
        except Exception:
            logger.exception("TTS synthesis failed for chunk %d", chunk.index)
            segment.finalize()

        return segment

    # ----- callbacks --------------------------------------------------------

    def _on_playback_state(
        self, chunk: TextChunk, state: PlaybackState,
    ) -> None:
        if self._on_event:
            self._on_event(PlaybackEvent(chunk=chunk, state=state))
