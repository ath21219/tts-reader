"""音声再生・再生状態管理

miniaudio をバックエンドとして使用。
再生時間を音声データから算出し、完了を確実に待機する。
"""

from __future__ import annotations

import asyncio
import threading
import time
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
        self._lock = threading.Lock()
        self._duration: float = 0.0
        self._play_start: float = 0.0

    def ensure_init(self) -> None:
        pass

    def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
        import miniaudio

        # デコード（フォーマット自動検出）
        decoded = miniaudio.decode(audio_data, nchannels=1, sample_rate=44100)

        # 再生時間を算出
        if decoded.sample_rate > 0:
            self._duration = decoded.num_frames / decoded.sample_rate
        else:
            self._duration = 0.0

        # ストリーム生成
        stream = miniaudio.stream_raw_pcm_memory(
            decoded.samples,
            decoded.nchannels,
            miniaudio.width_from_format(decoded.sample_format),
        )

        with self._lock:
            # 前のデバイスがあれば閉じる
            if self._device is not None:
                try:
                    self._device.close()
                except Exception:
                    pass

            self._device = miniaudio.PlaybackDevice(
                output_format=decoded.sample_format,
                nchannels=decoded.nchannels,
                sample_rate=decoded.sample_rate,
            )
            self._play_start = time.monotonic()
            self._device.start(stream)

    def get_remaining_time(self) -> float:
        """再生残り時間を秒で返す。再生中でなければ 0。"""
        if self._duration <= 0:
            return 0.0
        elapsed = time.monotonic() - self._play_start
        remaining = self._duration - elapsed
        return max(0.0, remaining)

    def wait_until_done(self) -> None:
        """再生完了まで待機する。"""
        remaining = self.get_remaining_time()
        if remaining > 0:
            # 少しだけ余裕を持たせる（デバイスバッファの排出時間）
            time.sleep(remaining + 0.05)

        # デバイスを閉じて確実に停止
        with self._lock:
            if self._device is not None:
                try:
                    self._device.close()
                except Exception:
                    pass
                self._device = None

    def stop(self) -> None:
        with self._lock:
            self._duration = 0.0
            if self._device is not None:
                try:
                    self._device.close()
                except Exception:
                    pass
                self._device = None

    def quit(self) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# AudioPlayer
# ---------------------------------------------------------------------------

class AudioPlayer:
    """非同期対応の音声プレイヤー

    1セグメントの再生が完了するまで play_segment は返らない。
    これにより Orchestrator の consumer が次のセグメントに進む
    タイミングが正確に制御される。
    """

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
        """1つの音声セグメントを再生し、完了まで待機する。

        PLAYING → 再生/ポーズ完了待機 → IDLE の順序を厳密に保証する。
        """
        async with self._lock:
            self._state = PlaybackState.PLAYING
            self._notify(segment.chunk, PlaybackState.PLAYING)

            if segment.chunk.is_pause:
                await asyncio.sleep(segment.chunk.pause_duration)
            elif segment.audio_data:
                await self._play_audio_and_wait(segment.audio_data)

            self._state = PlaybackState.IDLE
            self._notify(segment.chunk, PlaybackState.IDLE)

    async def stop(self) -> None:
        self._state = PlaybackState.STOPPED
        await asyncio.to_thread(self._backend.stop)

    async def shutdown(self) -> None:
        await self.stop()
        await asyncio.to_thread(self._backend.quit)

    async def _play_audio_and_wait(self, audio_data: bytes) -> None:
        """バックエンドで再生を開始し、完了まで確実に待機する。"""
        # play() と wait_until_done() を同一スレッドで順に実行する。
        # これにより play 開始直後の時刻計測が正確になる。
        def _play_blocking() -> None:
            self._backend.play(audio_data)
            self._backend.wait_until_done()

        await asyncio.to_thread(_play_blocking)

    def _notify(self, chunk: TextChunk, state: PlaybackState) -> None:
        if self._on_state_change:
            self._on_state_change(chunk, state)
