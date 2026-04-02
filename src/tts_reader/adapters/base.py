"""アダプタ基底クラス"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tts_reader.core.config import AppConfig
from tts_reader.core.models import PlaybackEvent
from tts_reader.core.orchestrator import Orchestrator


class BaseAdapter(ABC):
    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or AppConfig()
        self._orchestrator = Orchestrator(config=self._config)
        self._orchestrator.set_event_callback(self.on_playback_event)

    async def initialize(self) -> None:
        await self._orchestrator.start()

    async def dispose(self) -> None:
        await self._orchestrator.shutdown()

    async def read_text(self, text: str) -> None:
        await self._orchestrator.speak(text)

    async def stop_reading(self) -> None:
        await self._orchestrator.stop()

    @abstractmethod
    def on_playback_event(self, event: PlaybackEvent) -> None: ...

    @abstractmethod
    def get_text(self) -> str: ...
