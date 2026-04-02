"""データモデル定義"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class ChunkType(Enum):
    """チャンクの種別"""
    TEXT = auto()       # 読み上げ対象の本文
    PAUSE = auto()      # 無音（場面転換の間など）
    SKIP = auto()       # 読み飛ばし（メタタグ等）


@dataclass
class TextChunk:
    """解析済みテキストチャンク"""
    index: int
    content: str
    chunk_type: ChunkType
    pause_duration: float = 0.0   # PAUSE時の秒数
    source_offset: int = 0        # 原文中の開始位置（ハイライト用）
    source_length: int = 0        # 原文中の長さ（ハイライト用）

    @property
    def is_speakable(self) -> bool:
        return self.chunk_type == ChunkType.TEXT

    @property
    def is_pause(self) -> bool:
        return self.chunk_type == ChunkType.PAUSE


@dataclass
class TTSRequest:
    """TTSサーバへのリクエスト"""
    text: str
    caption: str = ""
    seed: Optional[int] = None
    voice: str = "alloy"
    model: str = "tts-1"
    response_format: str = "mp3"
    speed: float = 1.0


class PlaybackState(Enum):
    """再生状態"""
    IDLE = auto()
    PLAYING = auto()
    PAUSED = auto()
    STOPPED = auto()


@dataclass
class PlaybackEvent:
    """再生イベント（Orchestrator → Adapter への通知用）"""
    chunk: TextChunk
    state: PlaybackState


@dataclass
class AudioSegment:
    """再生キューに投入される音声セグメント"""
    chunk: TextChunk
    audio_data: bytes = b""       # 完全な音声バイナリ（ストリーミング受信後に確定）
    is_ready: bool = False        # 音声データの受信完了フラグ

    # ストリーミング受信中のバッファ
    _buffer: bytearray = field(default_factory=bytearray, repr=False)

    def append(self, data: bytes) -> None:
        self._buffer.extend(data)

    def finalize(self) -> None:
        self.audio_data = bytes(self._buffer)
        self._buffer.clear()
        self.is_ready = True
