"""音声再生・再生状態管理

miniaudio をバックエンドとして使用。
PlaybackDevice を使い回し、再生完了を threading.Event で正確に検知する。
"""

from __future__ import annotations

import asyncio
import time
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

    """

    def __init__(self) -> None:
        self._device: Any | None = None
        self._done_event: threading.Event = threading.Event()
        self._done_event.set()  # 初期状態は「完了」
        self._sample_format: Any = None
        self._nchannels: int = 0
        self._sample_rate: int = 0

    def ensure_init(self) -> None:
        pass

    def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
        import miniaudio

        # デコード
        decoded = miniaudio.decode(audio_data, nchannels=1, sample_rate=44100)

        # PCM バイト列とフレーム情報を事前計算
        pcm_bytes = bytes(decoded.samples)
        bytes_per_frame = decoded.nchannels * miniaudio.width_from_format(
            decoded.sample_format
        )
        total_frames = len(pcm_bytes) // bytes_per_frame if bytes_per_frame > 0 else 0

        self._done_event.clear()

        # StopIteration を使わず、オフセットで終了判定する。
        # 全データ供給後は無音 (ゼロ埋め) を返し続け、
        # device.stop() が呼ばれるまでジェネレータは生存する。
        done_event = self._done_event
        offset = 0  # 供給済みフレーム数

        def _stream_generator() -> Any:
            nonlocal offset
            required_frames = yield b""
            while True:
                if offset >= total_frames:
                    # 全データ供給完了 → 完了シグナル
                    done_event.set()
                    # 無音フレームを返し続ける（device.stop() まで）
                    while True:
                        silence = b"\x00" * (required_frames * bytes_per_frame)
                        required_frames = yield silence
                else:
                    frames_to_send = min(required_frames, total_frames - offset)
                    start_byte = offset * bytes_per_frame
                    end_byte = (offset + frames_to_send) * bytes_per_frame
                    data = pcm_bytes[start_byte:end_byte]
                    # 要求フレーム数に満たない場合は無音でパディング
                    if frames_to_send < required_frames:
                        padding = (required_frames - frames_to_send) * bytes_per_frame
                        data = data + b"\x00" * padding
                    offset += frames_to_send
                    required_frames = yield data

        gen = _stream_generator()
        next(gen)  # 初期化

        # 出力フォーマットが前回と一致する場合は close せず再利用する。
        need_new_device = (
            self._device is None
            or self._sample_format != decoded.sample_format
            or self._nchannels != decoded.nchannels
            or self._sample_rate != decoded.sample_rate
        )

        if need_new_device:
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
            self._sample_format = decoded.sample_format
            self._nchannels = decoded.nchannels
            self._sample_rate = decoded.sample_rate

        self._device.start(gen)

    def wait_until_done(self) -> None:
        """全フレーム供給完了を待機し、デバイスバッファの残りを
        排出するためのわずかな猶予を設けてからデバイスを停止する。"""
        self._done_event.wait(timeout=300.0)

        # デバイスバッファに残った最後のデータを再生させるための猶予
        # 一般的なオーディオバッファは 20〜100ms 程度
        time.sleep(0.15)

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

    コールバックの戻り値がコルーチンの場合は await する。
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
        """1セグメントを再生し、完了まで待機する。
        """
        async with self._lock:
            self._state = PlaybackState.PLAYING
            await self._notify_async(segment.chunk, PlaybackState.PLAYING)

            if segment.chunk.is_pause:
                await asyncio.sleep(segment.chunk.pause_duration)
            elif segment.audio_data:
                await self._play_audio_and_wait(segment.audio_data)

            self._state = PlaybackState.IDLE
            await self._notify_async(segment.chunk, PlaybackState.IDLE)

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

    async def _notify_async(self, chunk: TextChunk, state: PlaybackState) -> None:
        """コールバックを呼び出す。

        コールバックがコルーチンを返す場合は await する。
        これにより bridge_server 側で直接 await self._broadcast() が可能になる。
        """
        if self._on_state_change:
            result = self._on_state_change(chunk, state)
            if asyncio.iscoroutine(result):
                await result
