"""音声再生・再生状態管理"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from typing import Any

from tts_reader.core.models import AudioSegment, PlaybackState, TextChunk


class _PygameBackend:
    def __init__(self) -> None:
        self._initialized = False

    def ensure_init(self) -> None:
        if not self._initialized:
            import pygame
            pygame.mixer.init()
            self._initialized = True

    def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
        import pygame
        self.ensure_init()
        sound = pygame.mixer.Sound(io.BytesIO(audio_data))
        sound.play()
        self._current_sound = sound

    def is_busy(self) -> bool:
        import pygame
        return pygame.mixer.get_busy()

    def stop(self) -> None:
        import pygame
        pygame.mixer.stop()

    def quit(self) -> None:
        import pygame
        if self._initialized:
            pygame.mixer.quit()
            self._initialized = False


class AudioPlayer:
    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend or _PygameBackend()
        self._state = PlaybackState.IDLE
        self._lock = asyncio.Lock()
        self._on_state_change: Callable[[TextChunk, PlaybackState], Any] | None = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    def set_state_callback(
        self,
        callback: Callable[[TextChunk, PlaybackState], Any],
    ) -> None:
        self._on_state_change = callback

    async def play_segment(self, segment: AudioSegment) -> None:
        async with self._lock:
            self._state = PlaybackState.PLAYING
            self._notify(segment.chunk, PlaybackState.PLAYING)
            if segment.chunk.is_pause:
                await asyncio.sleep(segment.chunk.pause_duration)
            elif segment.audio_data:
                await self._play_audio(segment.audio_data)
            self._state = PlaybackState.IDLE
            self._notify(segment.chunk, PlaybackState.IDLE)

    async def stop(self) -> None:
        self._state = PlaybackState.STOPPED
        await asyncio.to_thread(self._backend.stop)

    async def shutdown(self) -> None:
        await self.stop()
        await asyncio.to_thread(self._backend.quit)

    async def _play_audio(self, audio_data: bytes) -> None:
        await asyncio.to_thread(self._backend.play, audio_data)
        while await asyncio.to_thread(self._backend.is_busy):
            await asyncio.sleep(0.05)

    def _notify(self, chunk: TextChunk, state: PlaybackState) -> None:
        if self._on_state_change:
            self._on_state_change(chunk, state)
