"""データモデルのテスト"""

from tts_reader.core.models import (
    AudioSegment,
    ChunkType,
    PlaybackState,
    TextChunk,
    TTSRequest,
)


class TestTextChunk:
    def test_is_speakable_text(self) -> None:
        chunk = TextChunk(index=0, content="hello", chunk_type=ChunkType.TEXT)
        assert chunk.is_speakable is True
        assert chunk.is_pause is False

    def test_is_pause(self) -> None:
        chunk = TextChunk(
            index=0, content="", chunk_type=ChunkType.PAUSE, pause_duration=0.5,
        )
        assert chunk.is_speakable is False
        assert chunk.is_pause is True

    def test_skip_is_not_speakable(self) -> None:
        chunk = TextChunk(index=0, content="```code```", chunk_type=ChunkType.SKIP)
        assert chunk.is_speakable is False
        assert chunk.is_pause is False

    def test_source_position(self) -> None:
        chunk = TextChunk(
            index=5, content="text", chunk_type=ChunkType.TEXT,
            source_offset=100, source_length=4,
        )
        assert chunk.source_offset == 100
        assert chunk.source_length == 4


class TestAudioSegment:
    def test_append_and_finalize(self) -> None:
        chunk = TextChunk(index=0, content="test", chunk_type=ChunkType.TEXT)
        segment = AudioSegment(chunk=chunk)
        assert segment.is_ready is False
        assert segment.audio_data == b""
        segment.append(b"\x01\x02\x03")
        segment.append(b"\x04\x05")
        segment.finalize()
        assert segment.is_ready is True
        assert segment.audio_data == b"\x01\x02\x03\x04\x05"

    def test_finalize_empty(self) -> None:
        chunk = TextChunk(index=0, content="", chunk_type=ChunkType.PAUSE)
        segment = AudioSegment(chunk=chunk)
        segment.finalize()
        assert segment.is_ready is True
        assert segment.audio_data == b""


class TestTTSRequest:
    def test_default_values(self) -> None:
        req = TTSRequest(text="hello")
        assert req.text == "hello"
        assert req.caption == ""
        assert req.seed is None
        assert req.voice == "alloy"
        assert req.model == "tts-1"
        assert req.response_format == "mp3"
        assert req.speed == 1.0

    def test_custom_values(self) -> None:
        req = TTSRequest(
            text="test",
            caption="speak softly",
            seed=123,
            voice="nova",
            speed=0.8,
        )
        assert req.caption == "speak softly"
        assert req.seed == 123
        assert req.voice == "nova"
        assert req.speed == 0.8