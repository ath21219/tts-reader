"""TTS Reader Core — プラットフォーム非依存のTTS読み上げエンジン"""

from tts_reader.core.config import AppConfig, PlaybackConfig, TTSConfig
from tts_reader.core.models import (
    AudioSegment,
    ChunkType,
    PlaybackEvent,
    PlaybackState,
    TextChunk,
    TTSRequest,
)
from tts_reader.core.orchestrator import Orchestrator
from tts_reader.core.text_parser import MarkdownTextParser
from tts_reader.core.tts_client import TTSClient
from tts_reader.core.audio_player import AudioPlayer

__all__ = [
    "AppConfig",
    "AudioPlayer",
    "AudioSegment",
    "ChunkType",
    "MarkdownTextParser",
    "Orchestrator",
    "PlaybackConfig",
    "PlaybackEvent",
    "PlaybackState",
    "TTSClient",
    "TTSConfig",
    "TTSRequest",
    "TextChunk",
]
