"""テキスト解析・チャンク分割

入力テキスト（Markdown、HTML等）を解析し、
読み上げ対象の本文チャンクと演出用ポーズに分割する。

source_offset / source_length は常に原文（parse() に渡された text）上の
位置・長さを指す。これによりエディタ上のハイライトが正確に対応する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from tts_reader.core.config import PlaybackConfig
from tts_reader.core.models import ChunkType, TextChunk


# ---------------------------------------------------------------------------
# Parser Protocol（将来の拡張用）
# ---------------------------------------------------------------------------

class TextParserProtocol(Protocol):
    def parse(self, text: str) -> list[TextChunk]: ...


# ---------------------------------------------------------------------------
# 内部データ
# ---------------------------------------------------------------------------

@dataclass
class _RawBlock:
    """原文上のブロック情報"""
    text: str           # 原文のブロックテキスト（装飾込み）
    offset: int         # 原文上の開始位置
    is_heading: bool = False
    heading_level: int = 0
    heading_body: str = ""       # 見出しの # 以降のテキスト（原文）
    heading_body_offset: int = 0  # heading_body の原文上の開始位置


# ---------------------------------------------------------------------------
# Markdown / 汎用パーサ
# ---------------------------------------------------------------------------

class MarkdownTextParser:
    """Markdownテキストを解析してチャンクリストに変換する"""

    # 読み飛ばし対象のパターン
    _SKIP_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"^---\s*$", re.MULTILINE),
        re.compile(r"^```[\s\S]*?^```", re.MULTILINE),
        re.compile(r"<!--[\s\S]*?-->"),
        re.compile(r"^<(?:meta|link|script|style)[^>]*>.*$",
                   re.MULTILINE | re.IGNORECASE),
        re.compile(r"!\[([^\]]*)\]\([^)]+\)"),
    ]

    # インライン装飾の除去パターン
    _INLINE_STRIP: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
        (re.compile(r"\*(.+?)\*"), r"\1"),
        (re.compile(r"~~(.+?)~~"), r"\1"),
        (re.compile(r"`([^`]+)`"), r"\1"),
        (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    ]

    # 見出し
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    def __init__(self, playback_config: PlaybackConfig | None = None) -> None:
        self._pc = playback_config or PlaybackConfig()

    # ----- public -----------------------------------------------------------

    def parse(self, text: str) -> list[TextChunk]:
        """テキスト全体を解析してチャンクリストを返す"""
        # 1. スキップ対象の範囲を特定
        skip_ranges = self._find_skip_ranges(text)

        # 2. ブロック単位に分割（原文上の位置情報を保持）
        raw_blocks = self._split_into_blocks(text, skip_ranges)

        # 3. 各ブロックをチャンクに変換
        chunks: list[TextChunk] = []
        idx = 0
        for block in raw_blocks:
            block_chunks, idx = self._process_block(block, idx, len(chunks) > 0)
            chunks.extend(block_chunks)

        return chunks

    # ----- skip 範囲の検出 --------------------------------------------------

    def _find_skip_ranges(self, text: str) -> list[tuple[int, int]]:
        """スキップ対象の (start, end) 範囲リストを返す（マージ済み）"""
        ranges: list[tuple[int, int]] = []
        for pat in self._SKIP_PATTERNS:
            for m in pat.finditer(text):
                ranges.append((m.start(), m.end()))
        ranges.sort()

        # 重なりをマージ
        merged: list[tuple[int, int]] = []
        for s, e in ranges:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged

    def _is_in_skip_range(
        self, pos: int, length: int, skip_ranges: list[tuple[int, int]],
    ) -> bool:
        """指定範囲がスキップ範囲に完全に含まれるか判定"""
        end = pos + length
        for s, e in skip_ranges:
            if s <= pos and end <= e:
                return True
        return False

    # ----- ブロック分割 -----------------------------------------------------

    def _split_into_blocks(
        self, text: str, skip_ranges: list[tuple[int, int]],
    ) -> list[_RawBlock]:
        """原文を空行で分割し、スキップ範囲を除外したブロックリストを返す。

        各ブロックは原文上のオフセットを保持する。
        """
        blocks: list[_RawBlock] = []

        # 空行（\n\n 以上）で分割しつつ、原文上の位置を追跡
        for m in re.finditer(r"(?:^|\n\n+)([^\n](?:[^\n]|\n(?!\n))*)", text):
            block_text = m.group(1).strip()
            if not block_text:
                continue

            # 原文上のブロック開始位置（先頭の空白・改行を除いた実テキストの位置）
            block_start = m.start(1)

            # スキップ範囲に含まれるか
            if self._is_in_skip_range(block_start, len(block_text), skip_ranges):
                continue

            # スキップ範囲と部分的に重なる場合もスキップ
            # （ブロック内にスキップ対象が含まれる場合は残りを処理）
            clean_text = block_text
            for s, e in skip_ranges:
                rel_s = s - block_start
                rel_e = e - block_start
                if 0 <= rel_s < len(clean_text):
                    clean_text = clean_text[:rel_s] + clean_text[rel_e:]

            clean_text = clean_text.strip()
            if not clean_text:
                continue

            # 見出し判定
            hm = self._HEADING_RE.match(block_text)
            if hm:
                # 見出し本体の原文上の位置を計算
                heading_body = hm.group(2)
                heading_body_offset = block_start + hm.start(2)
                blocks.append(_RawBlock(
                    text=block_text,
                    offset=block_start,
                    is_heading=True,
                    heading_level=len(hm.group(1)),
                    heading_body=heading_body,
                    heading_body_offset=heading_body_offset,
                ))
            else:
                blocks.append(_RawBlock(
                    text=block_text,
                    offset=block_start,
                ))

        return blocks

    # ----- ブロック → チャンク変換 ------------------------------------------

    def _process_block(
        self,
        block: _RawBlock,
        start_idx: int,
        has_previous: bool,
    ) -> tuple[list[TextChunk], int]:
        """1ブロックをチャンクリストに変換"""
        chunks: list[TextChunk] = []
        idx = start_idx

        if block.is_heading:
            # 見出し前ポーズ
            chunks.append(TextChunk(
                index=idx, content="", chunk_type=ChunkType.PAUSE,
                pause_duration=self._pc.heading_pause,
            ))
            idx += 1

            # 見出しテキスト
            clean = self._strip_inline(block.heading_body)
            chunks.append(TextChunk(
                index=idx,
                content=clean,
                chunk_type=ChunkType.TEXT,
                source_offset=block.offset,
                source_length=len(block.text),
            ))
            idx += 1

            # 見出し後ポーズ
            chunks.append(TextChunk(
                index=idx, content="", chunk_type=ChunkType.PAUSE,
                pause_duration=self._pc.heading_pause,
            ))
            idx += 1
            return chunks, idx

        # 通常段落: 段落前ポーズ
        if has_previous:
            chunks.append(TextChunk(
                index=idx, content="", chunk_type=ChunkType.PAUSE,
                pause_duration=self._pc.paragraph_pause,
            ))
            idx += 1

        # 文単位に分割（原文上の位置を追跡）
        sentence_chunks = self._split_into_sentences(block)
        for content, src_offset, src_length in sentence_chunks:
            chunks.append(TextChunk(
                index=idx,
                content=content,
                chunk_type=ChunkType.TEXT,
                source_offset=src_offset,
                source_length=src_length,
            ))
            idx += 1

        return chunks, idx

    # ----- 文分割（原文位置追跡） -------------------------------------------

    def _split_into_sentences(
        self, block: _RawBlock,
    ) -> list[tuple[str, int, int]]:
        """ブロックを文単位に分割し、(読み上げテキスト, 原文offset, 原文length) を返す。

        原文テキスト上で句読点の位置を検出し、そこで区切る。
        各文について原文上の正確な開始位置と長さを記録する。
        """
        raw = block.text
        base_offset = block.offset

        # 原文上で文の境界を見つける
        # 句点・ピリオド・感嘆符・疑問符の直後で分割
        split_re = re.compile(r"(?<=[。．.!！?？])\s*")

        boundaries: list[int] = [0]
        for m in split_re.finditer(raw):
            boundaries.append(m.end())

        # 各文の範囲を確定
        result: list[tuple[str, int, int]] = []
        for i in range(len(boundaries)):
            start = boundaries[i]
            end = boundaries[i + 1] if i + 1 < len(boundaries) else len(raw)

            raw_sentence = raw[start:end].strip()
            if not raw_sentence:
                continue

            # 原文上の正確な位置: strip で先頭の空白を除いた分を補正
            leading_ws = len(raw[start:end]) - len(raw[start:end].lstrip())
            src_offset = base_offset + start + leading_ws
            src_length = len(raw_sentence)

            # 読み上げ用テキスト（インライン装飾を除去）
            clean = self._strip_inline(raw_sentence)
            if clean:
                result.append((clean, src_offset, src_length))

        return result

    # ----- ユーティリティ ---------------------------------------------------

    def _strip_inline(self, text: str) -> str:
        """インラインMarkdown装飾を除去する"""
        result = text
        for pat, repl in self._INLINE_STRIP:
            result = pat.sub(repl, result)
        return result.strip()
