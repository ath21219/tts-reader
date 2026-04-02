"""音声再生・再生状態管理

miniaudio をバックエンドとして使用。
PlaybackDevice を使い回し、再生完了を threading.Event で正確に検知する。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from tts_reader.core.models import AudioSegment, PlaybackState, TextChunk


# ---------------------------------------------------------------------------
# miniaudio バックエンド
# ---------------------------------------------------------------------------

class _MiniaudioBackend:
    """miniaudio を使った再生バックエンド

    PlaybackDevice を使い回し、再生完了を Event で検知する。
    ジェネレータが全フレームを供給し終えた時点で Event をセットする。
    デバイスのバッファに残ったデータは device.stop() で即座に破棄する。
    """

    def __init__(self) -> None:
        self._device: Any | None = None
        self._done_event: threading.Event = threading.Event()
        self._done_event.set()  # 初期状態は「完了」

    def ensure_init(self) -> None:
        pass

    def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
        import miniaudio

        # デコード
        decoded = miniaudio.decode(audio_data, nchannels=1, sample_rate=44100)

        # ストリーム生成
        raw_stream = miniaudio.stream_raw_pcm_memory(
            decoded.samples,
            decoded.nchannels,
            miniaudio.width_from_format(decoded.sample_format),
        )

        self._done_event.clear()

        # ジェネレータ終了を検知するラッパー
        done_event = self._done_event

        def _monitored_stream() -> Any:
            required_frames = yield b""
            try:
                while True:
                    data = raw_stream.send(required_frames)
                    required_frames = yield data
            except StopIteration:
                pass
            finally:
                done_event.set()

        gen = _monitored_stream()
        next(gen)  # 初期化

        # デバイスを作成（または再作成）
        # miniaudio の PlaybackDevice は start/stop を繰り返せるが
        # 出力フォーマットが変わる可能性があるため毎回再作成する
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
        self._device.start(gen)

    def wait_until_done(self) -> None:
        """ジェネレータが全フレームを供給し終えるまで待機し、
        デバイスを即座に停止してバッファ内の残データを破棄する。"""
        self._done_event.wait(timeout=300.0)

        # ジェネレータ完了 = 全データ供給済み
        # デバイスを停止してバッファ残データを即破棄
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:
                pass

    def stop(self) -> None:
        """再生を即座に中断する"""
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:
                pass
        self._done_event.set()

    def quit(self) -> None:
        """デバイスを閉じてリソースを解放する"""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        self._done_event.set()


# ---------------------------------------------------------------------------
# AudioPlayer
# ---------------------------------------------------------------------------

class AudioPlayer:
    """非同期対応の音声プレイヤー

    1セグメントの再生が完了するまで play_segment は返らない。
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
        def _play_blocking() -> None:
            self._backend.play(audio_data)
            self._backend.wait_until_done()

        await asyncio.to_thread(_play_blocking)

    def _notify(self, chunk: TextChunk, state: PlaybackState) -> None:
        if self._on_state_change:
            self._on_state_change(chunk, state)
