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
        self._buffer: asyncio.Queue[AudioSegment] = asyncio.Queue()
        self._chunks: list[TextChunk] = []
        self._running = False
        self._current_chunk_index: int = -1
        self._on_event: Callable[[PlaybackEvent], Any] | None = None
        self._player.set_state_callback(self._on_playback_state)

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
        self._running = True
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
        self._running = False
        await self._player.stop()
        while not self._buffer.empty():
            try:
                self._buffer.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _produce(self) -> None:
        sem = asyncio.Semaphore(self._config.playback.buffer_ahead)

        async def _generate_one(chunk: TextChunk) -> None:
            async with sem:
                if not self._running:
                    return
                segment = await self._synthesize_chunk(chunk)
                await self._buffer.put(segment)

        tasks: list[asyncio.Task[None]] = []
        for chunk in self._chunks:
            if not self._running:
                break
            task = asyncio.create_task(_generate_one(chunk))
            tasks.append(task)
        for task in tasks:
            await task
        await self._buffer.put(None)  # type: ignore[arg-type]

    async def _consume(self) -> None:
        while self._running:
            segment: AudioSegment | None = await self._buffer.get()
            if segment is None:
                break
            if not self._running:
                break
            self._current_chunk_index = segment.chunk.index
            await self._player.play_segment(segment)

    async def _synthesize_chunk(self, chunk: TextChunk) -> AudioSegment:
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

    def _on_playback_state(
        self, chunk: TextChunk, state: PlaybackState,
    ) -> None:
        if self._on_event:
            self._on_event(PlaybackEvent(chunk=chunk, state=state))
