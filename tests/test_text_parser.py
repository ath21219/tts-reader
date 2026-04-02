"""テキスト解析のテスト"""

from tts_reader.core.config import PlaybackConfig
from tts_reader.core.models import ChunkType
from tts_reader.core.text_parser import MarkdownTextParser


class TestMarkdownTextParser:

    def setup_method(self) -> None:
        self.config = PlaybackConfig(
            heading_pause=0.5,
            paragraph_pause=0.3,
            section_pause=0.8,
        )
        self.parser = MarkdownTextParser(self.config)

    # ----- 基本的な分割 ---------------------------------------------------

    def test_plain_single_sentence(self) -> None:
        chunks = self.parser.parse("これはテストです。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 1
        assert text_chunks[0].content == "これはテストです。"

    def test_plain_multiple_sentences(self) -> None:
        chunks = self.parser.parse("最初の文。次の文。最後の文。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 3
        assert text_chunks[0].content == "最初の文。"
        assert text_chunks[1].content == "次の文。"
        assert text_chunks[2].content == "最後の文。"

    def test_english_sentences(self) -> None:
        chunks = self.parser.parse("First sentence. Second sentence. Third.")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 3

    def test_mixed_punctuation(self) -> None:
        chunks = self.parser.parse("本当ですか？はい！そうです。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 3
        assert text_chunks[0].content == "本当ですか？"
        assert text_chunks[1].content == "はい！"
        assert text_chunks[2].content == "そうです。"

    # ----- 見出し ---------------------------------------------------------

    def test_heading_creates_pause_before_and_after(self) -> None:
        chunks = self.parser.parse("# タイトル")
        assert len(chunks) == 3
        assert chunks[0].chunk_type == ChunkType.PAUSE
        assert chunks[0].pause_duration == self.config.heading_pause
        assert chunks[1].chunk_type == ChunkType.TEXT
        assert chunks[1].content == "タイトル"
        assert chunks[2].chunk_type == ChunkType.PAUSE

    def test_h2_heading(self) -> None:
        chunks = self.parser.parse("## セクション")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 1
        assert text_chunks[0].content == "セクション"

    def test_heading_followed_by_paragraph(self) -> None:
        text = "# タイトル\n\nこれは本文です。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 2
        assert text_chunks[0].content == "タイトル"
        assert text_chunks[1].content == "これは本文です。"

    # ----- 段落間ポーズ ---------------------------------------------------

    def test_paragraph_pause_between_blocks(self) -> None:
        text = "最初の段落。\n\n次の段落。"
        chunks = self.parser.parse(text)
        pause_chunks = [c for c in chunks if c.chunk_type == ChunkType.PAUSE]
        assert len(pause_chunks) >= 1
        assert any(c.pause_duration == self.config.paragraph_pause for c in pause_chunks)

    def test_no_pause_at_start_of_first_block(self) -> None:
        text = "最初の段落です。"
        chunks = self.parser.parse(text)
        assert chunks[0].chunk_type == ChunkType.TEXT

    # ----- Markdownインライン装飾の除去 -----------------------------------

    def test_strip_bold(self) -> None:
        chunks = self.parser.parse("これは**太字**です。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].content == "これは太字です。"

    def test_strip_italic(self) -> None:
        chunks = self.parser.parse("これは*イタリック*です。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].content == "これはイタリックです。"

    def test_strip_strikethrough(self) -> None:
        chunks = self.parser.parse("これは~~取り消し~~です。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].content == "これは取り消しです。"

    def test_strip_inline_code(self) -> None:
        chunks = self.parser.parse("関数`print()`を使います。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].content == "関数print()を使います。"

    def test_strip_link_keep_text(self) -> None:
        chunks = self.parser.parse("詳細は[こちら](https://example.com)をご覧ください。")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].content == "詳細はこちらをご覧ください。"

    def test_strip_multiple_inline(self) -> None:
        text = "**太字**と*イタリック*と`コード`を含む文。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].content == "太字とイタリックとコードを含む文。"

    # ----- スキップ対象 ---------------------------------------------------

    def test_skip_code_block(self) -> None:
        text = "本文です。\n\n```python\nprint('hello')\n```\n\n続きです。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        contents = [c.content for c in text_chunks]
        assert "本文です。" in contents
        assert "続きです。" in contents
        assert all("print" not in c for c in contents)

    def test_skip_html_comment(self) -> None:
        text = "本文です。\n\n<!-- コメント -->\n\n続きです。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        contents = [c.content for c in text_chunks]
        assert all("コメント" not in c for c in contents)

    def test_skip_horizontal_rule(self) -> None:
        text = "前の段落。\n\n---\n\n後の段落。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        contents = [c.content for c in text_chunks]
        assert "前の段落。" in contents
        assert "後の段落。" in contents
        assert all("---" not in c for c in contents)

    def test_skip_image(self) -> None:
        text = "テキスト。\n\n![alt](image.png)\n\n続き。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        contents = [c.content for c in text_chunks]
        assert all("image.png" not in c for c in contents)

    def test_skip_meta_tags(self) -> None:
        text = '<meta charset="utf-8">\n\n本文です。'
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        contents = [c.content for c in text_chunks]
        assert "本文です。" in contents
        assert all("meta" not in c for c in contents)

    # ----- ソースオフセット -----------------------------------------------

    def test_source_offset_simple(self) -> None:
        text = "最初の文。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks[0].source_offset >= 0
        assert text_chunks[0].source_length > 0

    def test_source_offset_after_heading(self) -> None:
        text = "# 見出し\n\n本文テキスト。"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert len(text_chunks) == 2
        assert text_chunks[1].source_offset > text_chunks[0].source_offset

    # ----- 空入力・エッジケース -------------------------------------------

    def test_empty_input(self) -> None:
        chunks = self.parser.parse("")
        assert chunks == []

    def test_whitespace_only(self) -> None:
        chunks = self.parser.parse("   \n\n   \n")
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks == []

    def test_only_code_block(self) -> None:
        text = "```\ncode only\n```"
        chunks = self.parser.parse(text)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert text_chunks == []

    # ----- 複合ドキュメント -----------------------------------------------

    def test_full_document(self, sample_markdown: str) -> None:
        chunks = self.parser.parse(sample_markdown)
        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        pause_chunks = [c for c in chunks if c.chunk_type == ChunkType.PAUSE]
        assert len(text_chunks) > 0
        assert len(pause_chunks) > 0
        all_contents = " ".join(c.content for c in text_chunks)
        assert "print" not in all_contents
        assert "コメント" not in all_contents
        contents = [c.content for c in text_chunks]
        assert "第1章 はじめに" in contents
        assert "1.1 詳細" in contents

    def test_indices_are_sequential(self, sample_markdown: str) -> None:
        chunks = self.parser.parse(sample_markdown)
        for i, chunk in enumerate(chunks):
            assert chunk.index == i
