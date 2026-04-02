"""CLIアダプタ — 開発・テスト用"""

from __future__ import annotations

import asyncio
import sys

from tts_reader.core.config import AppConfig
from tts_reader.core.models import PlaybackEvent, PlaybackState
from tts_reader.adapters.base import BaseAdapter


class CLIAdapter(BaseAdapter):
    """ターミナルから直接テストできるCLIアダプタ"""

    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__(config)
        self._text: str = ""

    def on_playback_event(self, event: PlaybackEvent) -> None:
        chunk = event.chunk
        state = event.state
        if state == PlaybackState.PLAYING:
            if chunk.is_pause:
                print(f"  [PAUSE {chunk.pause_duration:.1f}s]")
            elif chunk.is_speakable:
                print(f"  ▶ [{chunk.index:03d}] {chunk.content}")

    def get_text(self) -> str:
        return self._text

    async def run(self, text: str) -> None:
        self._text = text
        print("=" * 60)
        print("TTS Reader — CLI Mode")
        print("=" * 60)
        await self.initialize()
        try:
            await self.read_text(text)
        finally:
            await self.dispose()
        print("=" * 60)
        print("Done.")


_SAMPLE_TEXT = (
    "# 第1章 はじめに\n"
    "\n"
    "これはTTS読み上げシステムの**テスト文**です。\n"
    "複数の文を含む段落があります。正しく分割されるでしょうか？\n"
    "\n"
    "## 1.1 技術概要\n"
    "\n"
    "このシステムは、Markdownテキストを解析して読み上げます。\n"
    "`コード`やリンク [例](https://example.com) も適切に処理します。\n"
    "\n"
    "---\n"
    "\n"
    "<!-- このコメントは読み上げられません -->\n"
    "\n"
    "```python\n"
    "# このコードブロックも読み上げられません\n"
    'print("hello")\n'
    "```\n"
    "\n"
    "## 1.2 まとめ\n"
    "\n"
    "以上がシステムの概要です。ご質問があればお知らせください！\n"
)


async def _async_main() -> None:
    if len(sys.argv) > 1:
        path = sys.argv[1]
        with open(path, encoding="utf-8") as f:
            text = f.read()
    else:
        text = _SAMPLE_TEXT

    config = AppConfig()
    adapter = CLIAdapter(config)
    await adapter.run(text)


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
