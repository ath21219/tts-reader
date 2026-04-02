"""全体統括・パイプライン制御

テキスト解析 → 先行TTS生成 → バッファリング → 再生 の
パイプラインを管理する。

バッファリング戦略:
  - Producer は チャンク順に TTS 生成タスクを起動する。
  - 各タスクの結果は index 付きの dict に格納される。
  - 投入専用コルーチンが index 順にキューへ投入する。
  - Consumer はキューから取り出して再生する。
  これにより、先行生成の並行性を保ちつつ順序を保証する。
"""

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

        # 再生キュー（順序保証済みのセグメントが入る）
        self._play_queue: asyncio.Queue[AudioSegment | None] = asyncio.Queue()
        self._chunks: list[TextChunk] = []

        # 状態
        self._running = False
        self._current_chunk_index: int = -1

        # 外部通知
        self._on_event: Callable[[PlaybackEvent], Any] | None = None

        # 再生コールバック接続
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

        # 前回のキューをクリア
        self._play_queue = asyncio.Queue()

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
        # キューを空にする
        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # 終端マーカーを投入して consumer を終了させる
        try:
            self._play_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    # ----- pipeline stages --------------------------------------------------

    async def _produce(self) -> None:
        """チャンクを順に処理し、音声を先行生成してバッファに投入する。

        buffer_ahead の数だけ並行してTTS生成を行うが、
        キューへの投入は常にチャンク順を保証する。
        """
        buffer_ahead = self._config.playback.buffer_ahead
        total = len(self._chunks)

        # 各チャンクの生成結果を格納する dict と完了イベント
        results: dict[int, AudioSegment] = {}
        events: dict[int, asyncio.Event] = {
            i: asyncio.Event() for i in range(total)
        }

        # 同時生成数を制限するセマフォ
        sem = asyncio.Semaphore(buffer_ahead)

        async def _generate(idx: int, chunk: TextChunk) -> None:
            """1チャンクの音声を生成し、results に格納して Event をセットする"""
            async with sem:
                if not self._running:
                    events[idx].set()
                    return
                segment = await self._synthesize_chunk(chunk)
                results[idx] = segment
                events[idx].set()

        # 全チャンクの生成タスクを起動
        tasks: list[asyncio.Task[None]] = []
        for i, chunk in enumerate(self._chunks):
            if not self._running:
                # 残りのイベントもセットして待機を解除
                for j in range(i, total):
                    events[j].set()
                break
            task = asyncio.create_task(_generate(i, chunk))
            tasks.append(task)

        # チャンク順にキューへ投入する
        for i in range(total):
            if not self._running:
                break
            await events[i].wait()
            if i in results:
                await self._play_queue.put(results.pop(i))

        # 全タスクの完了を待つ（例外を拾うため）
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # 終端マーカー
        await self._play_queue.put(None)

    async def _consume(self) -> None:
        """バッファから音声セグメントを取り出して順に再生する"""
        while self._running:
            segment: AudioSegment | None = await self._play_queue.get()
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

        # TTSリクエスト
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
