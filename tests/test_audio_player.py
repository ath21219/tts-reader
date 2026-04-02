"""音声再生のテスト"""

from __future__ import annotations

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

    async def test_state_callback_is_called(
        self, mock_audio_backend,
    ) -> None:
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
