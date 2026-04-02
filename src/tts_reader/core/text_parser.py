"""テキスト解析・チャンク分割"""

from __future__ import annotations

import re
from typing import Protocol

from tts_reader.core.config import PlaybackConfig
from tts_reader.core.models import ChunkType, TextChunk


# 以下、クラス本体は変更なし（先の実装と同一）
class TextParserProtocol(Protocol):
    def parse(self, text: str) -> list[TextChunk]: ...


class MarkdownTextParser:
    _SKIP_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"^---\s*$", re.MULTILINE),
        re.compile(r"^```[\s\S]*?^```", re.MULTILINE),
        re.compile(r"<!--[\s\S]*?-->"),
        re.compile(r"^<(?:meta|link|script|style)[^>]*>.*$",
                   re.MULTILINE | re.IGNORECASE),
        re.compile(r"!\[([^\]]*)\]\([^)]+\)"),
    ]

    _INLINE_STRIP: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
        (re.compile(r"\*(.+?)\*"), r"\1"),
        (re.compile(r"~~(.+?)~~"), r"\1"),
        (re.compile(r"`([^`]+)`"), r"\1"),
        (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    ]

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    _LIST_ITEM_RE = re.compile(r"^[\s]*[-*+]\s+(.+)$", re.MULTILINE)
    _ORDERED_LIST_RE = re.compile(r"^[\s]*\d+\.\s+(.+)$", re.MULTILINE)

    def __init__(self, playback_config: PlaybackConfig | None = None) -> None:
        self._pc = playback_config or PlaybackConfig()

    def parse(self, text: str) -> list[TextChunk]:
        cleaned, offset_map = self._remove_skip_blocks(text)
        raw_blocks = self._split_blocks(cleaned)
        chunks: list[TextChunk] = []
        idx = 0
        for block_text, block_offset in raw_blocks:
            block_chunks, idx = self._process_block(
                block_text, block_offset, offset_map, idx,
            )
            chunks.extend(block_chunks)
        return chunks

    def _remove_skip_blocks(
        self, text: str,
    ) -> tuple[str, list[tuple[int, int, int]]]:
        removed: list[tuple[int, int]] = []
        for pat in self._SKIP_PATTERNS:
            for m in pat.finditer(text):
                removed.append((m.start(), m.end()))
        removed.sort()
        merged: list[tuple[int, int]] = []
        for s, e in removed:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        parts: list[str] = []
        offset_map: list[tuple[int, int, int]] = []
        prev = 0
        cleaned_pos = 0
        for s, e in merged:
            segment = text[prev:s]
            parts.append(segment)
            offset_map.append((cleaned_pos, prev, len(segment)))
            cleaned_pos += len(segment)
            prev = e
        tail = text[prev:]
        parts.append(tail)
        offset_map.append((cleaned_pos, prev, len(tail)))
        return "".join(parts), offset_map

    def _resolve_original_offset(
        self,
        cleaned_offset: int,
        offset_map: list[tuple[int, int, int]],
    ) -> int:
        for cleaned_start, orig_start, length in reversed(offset_map):
            if cleaned_offset >= cleaned_start:
                delta = cleaned_offset - cleaned_start
                return orig_start + min(delta, length)
        return cleaned_offset

    def _split_blocks(self, text: str) -> list[tuple[str, int]]:
        blocks: list[tuple[str, int]] = []
        pos = 0
        for raw_block in re.split(r"\n{2,}", text):
            stripped = raw_block.strip()
            if stripped:
                actual = text.find(raw_block, pos)
                blocks.append((stripped, actual if actual >= 0 else pos))
            pos += len(raw_block) + 2
        return blocks

    def _process_block(
        self,
        block_text: str,
        block_offset: int,
        offset_map: list[tuple[int, int, int]],
        start_idx: int,
    ) -> tuple[list[TextChunk], int]:
        chunks: list[TextChunk] = []
        idx = start_idx

        hm = self._HEADING_RE.match(block_text)
        if hm:
            chunks.append(TextChunk(
                index=idx, content="", chunk_type=ChunkType.PAUSE,
                pause_duration=self._pc.heading_pause,
            ))
            idx += 1
            clean = self._strip_inline(hm.group(2))
            orig_off = self._resolve_original_offset(block_offset, offset_map)
            chunks.append(TextChunk(
                index=idx, content=clean, chunk_type=ChunkType.TEXT,
                source_offset=orig_off, source_length=len(block_text),
            ))
            idx += 1
            chunks.append(TextChunk(
                index=idx, content="", chunk_type=ChunkType.PAUSE,
                pause_duration=self._pc.heading_pause,
            ))
            idx += 1
            return chunks, idx

        sentences = self._split_sentences(self._strip_inline(block_text))
        if start_idx > 0:
            chunks.append(TextChunk(
                index=idx, content="", chunk_type=ChunkType.PAUSE,
                pause_duration=self._pc.paragraph_pause,
            ))
            idx += 1
        running_offset = block_offset
        for sentence in sentences:
            orig_off = self._resolve_original_offset(running_offset, offset_map)
            chunks.append(TextChunk(
                index=idx, content=sentence, chunk_type=ChunkType.TEXT,
                source_offset=orig_off, source_length=len(sentence),
            ))
            idx += 1
            running_offset += len(sentence)
        return chunks, idx

    def _strip_inline(self, text: str) -> str:
        result = text
        for pat, repl in self._INLINE_STRIP:
            result = pat.sub(repl, result)
        return result.strip()

    def _split_sentences(self, text: str) -> list[str]:
        parts = re.split(r"(?<=[。．.!！?？\n])\s*", text)
        return [p.strip() for p in parts if p.strip()]
