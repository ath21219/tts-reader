"""音声再生のテスト"""

from __future__ import annotations

import asyncio

import pytest

from tts_reader.core.models import AudioSegment, ChunkType, PlaybackState, TextChunk
from tts_reader.core.audio_player import AudioPlayer


@pytest.mark.asyncio
class TestAudioPlayer:

    async def test_play_text_segment(
        self, mock_audio_backend,
    ) -> None:
        player = AudioPlayer(backend=mock_audio_backend)
        chunk = TextChunk(index=0, content="テスト", chunk_type=ChunkType.TEXT)
        segment = AudioSegment(chunk=chunk, audio_data=b"\x00" * 100, is_ready=True)
        await player.play_segment(segment)
        assert len(mock_audio_backend.played) == 1
        assert mock_audio_backend.played[0] == b"\x00" * 100
        assert player.state == PlaybackState.IDLE

    async def test_play_pause_segment(
        self, mock_audio_backend,
    ) -> None:
        player = AudioPlayer(backend=mock_audio_backend)
        chunk = TextChunk(
            index=0, content="", chunk_type=ChunkType.PAUSE, pause_duration=0.05,
        )
        segment = AudioSegment(chunk=chunk, is_ready=True)
        await player.play_segment(segment)
        assert len(mock_audio_backend.played) == 0
        assert player.state == PlaybackState.IDLE

    async def test_play_empty_audio_data(
        self, mock_audio_backend,
    ) -> None:
        player = AudioPlayer(backend=mock_audio_backend)
        chunk = TextChunk(index=0, content="空データ", chunk_type=ChunkType.TEXT)
        segment = AudioSegment(chunk=chunk, audio_data=b"", is_ready=True)
        await player.play_segment(segment)
        assert len(mock_audio_backend.played) == 0

    # [CHANGED] 同期コールバックのテスト
    async def test_state_callback_sync_is_called(
        self, mock_audio_backend,
    ) -> None:
        """同期コールバックが正しく呼ばれ、PLAYING → IDLE の順序であること"""
        player = AudioPlayer(backend=mock_audio_backend)
        events: list[tuple[int, PlaybackState]] = []

        def callback(chunk: TextChunk, state: PlaybackState) -> None:
            events.append((chunk.index, state))

        player.set_state_callback(callback)
        chunk = TextChunk(index=5, content="test", chunk_type=ChunkType.TEXT)
        segment = AudioSegment(chunk=chunk, audio_data=b"\x00" * 10, is_ready=True)
        await player.play_segment(segment)
        assert (5, PlaybackState.PLAYING) in events
        assert (5, PlaybackState.IDLE) in events

    # [CHANGED] 新規: 非同期コールバック (coroutine) のテスト
    async def test_state_callback_async_is_awaited(
        self, mock_audio_backend,
    ) -> None:
        """コールバックが coroutine を返す場合、正しく await されること"""
        player = AudioPlayer(backend=mock_audio_backend)
        events: list[tuple[int, PlaybackState]] = []

        async def async_callback(chunk: TextChunk, state: PlaybackState) -> None:
            await asyncio.sleep(0)  # 非同期処理のシミュレート
            events.append((chunk.index, state))

        # _notify_async は戻り値が coroutine かどうかで分岐する
        # async def は呼び出し時に coroutine を返すので対応できるはず
        player.set_state_callback(async_callback)  # type: ignore[arg-type]
        chunk = TextChunk(index=7, content="async test", chunk_type=ChunkType.TEXT)
        segment = AudioSegment(chunk=chunk, audio_data=b"\x00" * 10, is_ready=True)
        await player.play_segment(segment)
        assert (7, PlaybackState.PLAYING) in events
        assert (7, PlaybackState.IDLE) in events
        # 順序の確認: PLAYING が先
        playing_idx = events.index((7, PlaybackState.PLAYING))
        idle_idx = events.index((7, PlaybackState.IDLE))
        assert playing_idx < idle_idx

    async def test_state_callback_for_pause(
        self, mock_audio_backend,
    ) -> None:
        player = AudioPlayer(backend=mock_audio_backend)
        events: list[PlaybackState] = []

        def callback(chunk: TextChunk, state: PlaybackState) -> None:
            events.append(state)

        player.set_state_callback(callback)
        chunk = TextChunk(
            index=0, content="", chunk_type=ChunkType.PAUSE, pause_duration=0.01,
        )
        segment = AudioSegment(chunk=chunk, is_ready=True)
        await player.play_segment(segment)
        assert PlaybackState.PLAYING in events
        assert PlaybackState.IDLE in events

    async def test_sequential_playback(
        self, mock_audio_backend,
    ) -> None:
        player = AudioPlayer(backend=mock_audio_backend)
        for i in range(3):
            chunk = TextChunk(index=i, content=f"文{i}", chunk_type=ChunkType.TEXT)
            segment = AudioSegment(
                chunk=chunk, audio_data=f"audio{i}".encode(), is_ready=True,
            )
            await player.play_segment(segment)
        assert len(mock_audio_backend.played) == 3

    async def test_stop(
        self, mock_audio_backend,
    ) -> None:
        player = AudioPlayer(backend=mock_audio_backend)
        await player.stop()
        assert player.state == PlaybackState.STOPPED

    # [CHANGED] 新規: shutdown のテスト
    async def test_shutdown(
        self, mock_audio_backend,
    ) -> None:
        """shutdown 後に state が STOPPED であること"""
        player = AudioPlayer(backend=mock_audio_backend)
        await player.shutdown()
        assert player.state == PlaybackState.STOPPED

    # [CHANGED] 新規: 複数セグメントでコールバック順序が正しいことの確認
    async def test_multiple_segments_callback_order(
        self, mock_audio_backend,
    ) -> None:
        """複数セグメントを連続再生した場合、各セグメントごとに
        PLAYING → IDLE の順序が保たれること"""
        player = AudioPlayer(backend=mock_audio_backend)
        events: list[tuple[int, PlaybackState]] = []

        def callback(chunk: TextChunk, state: PlaybackState) -> None:
            events.append((chunk.index, state))

        player.set_state_callback(callback)

        for i in range(3):
            chunk = TextChunk(index=i, content=f"文{i}", chunk_type=ChunkType.TEXT)
            segment = AudioSegment(
                chunk=chunk, audio_data=f"audio{i}".encode(), is_ready=True,
            )
            await player.play_segment(segment)

        # 各セグメントで PLAYING → IDLE のペアが順に並ぶ
        assert len(events) == 6
        for i in range(3):
            assert events[i * 2] == (i, PlaybackState.PLAYING)
            assert events[i * 2 + 1] == (i, PlaybackState.IDLE)
