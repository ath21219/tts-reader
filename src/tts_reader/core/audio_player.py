"""音声再生・再生状態管理

miniaudio をバックエンドとして使用。
pygame の代替として、Python 3.14+ / Windows でも
ビルド不要で動作する。
"""

from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Callable
from typing import Any

from tts_reader.core.models import AudioSegment, PlaybackState, TextChunk


# ---------------------------------------------------------------------------
# miniaudio バックエンド
# ---------------------------------------------------------------------------

class _MiniaudioBackend:
    """miniaudio を使った再生バックエンド"""

    def __init__(self) -> None:
        self._device: Any | None = None
        self._playing = False
        self._lock = threading.Lock()

    def ensure_init(self) -> None:
        pass  # miniaudio は PlaybackDevice 生成時に初期化される

    def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
        import miniaudio

        # 音声データをデコード
        decoded = miniaudio.decode(audio_data, nchannels=1, sample_rate=44100)

        # PCMデータからストリームを作成
        stream = miniaudio.stream_raw_pcm_memory(
            decoded.samples,
            decoded.nchannels,
            miniaudio.width_from_format(decoded.sample_format),
        )

        # 再生完了を検知するためのラッパージェネレータ
        def monitored_stream() -> Any:
            required_frames = yield b""
            try:
                while True:
                    required_frames = yield stream.send(required_frames)
            except StopIteration:
                pass
            finally:
                with self._lock:
                    self._playing = False

        gen = monitored_stream()
        next(gen)  # ジェネレータ初期化

        with self._lock:
            # 前のデバイスがあれば閉じる
            if self._device is not None:
                try:
                    self._device.close()
                except Exception:
                    pass

            self._playing = True
            self._device = miniaudio.PlaybackDevice(
                output_format=decoded.sample_format,
                nchannels=decoded.nchannels,
                sample_rate=decoded.sample_rate,
            )
            self._device.start(gen)

    def is_busy(self) -> bool:
        with self._lock:
            return self._playing

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            if self._device is not None:
                try:
                    self._device.close()
                except Exception:
                    pass
                self._device = None

    def quit(self) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# AudioPlayer（バックエンド差し替え可能）
# ---------------------------------------------------------------------------

class AudioPlayer:
    """非同期対応の音声プレイヤー"""

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend or _MiniaudioBackend()
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
