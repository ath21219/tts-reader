"""設定管理"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TTSConfig:
    """TTS関連の設定"""
    server_url: str = "http://localhost:7820"
    endpoint: str = "/v1/audio/speech"
    voice: str = "alloy"
    model: str = "tts-1"
    response_format: str = "mp3"
    speed: float = 1.0
    caption: str = ""
    seed: Optional[int] = None
    timeout: float = 30.0


@dataclass
class PlaybackConfig:
    """再生関連の設定"""
    buffer_ahead: int = 1           # 先行バッファリングするチャンク数
    default_pause: float = 0.3      # 文間のデフォルトポーズ（秒）
    heading_pause: float = 0.5      # 見出し前後のポーズ（秒）
    section_pause: float = 0.5      # セクション区切りのポーズ（秒）
    paragraph_pause: float = 0.3    # 段落間のポーズ（秒）


@dataclass
class AppConfig:
    """アプリケーション全体の設定"""
    tts: TTSConfig = field(default_factory=TTSConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
