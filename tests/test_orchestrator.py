"""オーケストレータの結合テスト"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tts_reader.core.config import AppConfig
from tts_reader.core.models import ChunkType, PlaybackEvent, PlaybackState
from tts_reader.core.orchestrator import Orchestrator
from tts_reader.core.audio_player import AudioPlayer

from conftest import MockAudioBackend, MockTTSClient


@pytest.mark.asyncio
class TestOrchestrator:

    async def test_speak_simple_text(self, orchestrator: Orchestrator) -> None:
        events: list[PlaybackEvent] = []
        orchestrator.set_event_callback(lambda e: events.append(e))
        await orchestrator.start()
        try:
            await orchestrator.speak("これはテストです。")
        finally:
            await orchestrator.shutdown()
        playing_events = [
            e for e in events
            if e.state == PlaybackState.PLAYING and e.chunk.is_speakable
        ]
        assert len(playing_events) >= 1

    async def test_speak_multiple_sentences(self, orchestrator: Orchestrator) -> None:
        events: list[PlaybackEvent] = []
        orchestrator.set_event_callback(lambda e: events.append(e))
        await orchestrator.start()
        try:
            await orchestrator.speak("最初の文。次の文。最後の文。")
        finally:
            await orchestrator.shutdown()
        playing_text_events = [
            e for e in events
            if e.state == PlaybackState.PLAYING and e.chunk.is_speakable
        ]
        assert len(playing_text_events) == 3

    async def test_speak_with_heading(self, orchestrator: Orchestrator) -> None:
        events: list[PlaybackEvent] = []
        orchestrator.set_event_callback(lambda e: events.append(e))
        await orchestrator.start()
        try:
            await orchestrator.speak("# タイトル\n\n本文です。")
        finally:
            await orchestrator.shutdown()
        pause_events = [
            e for e in events
            if e.state == PlaybackState.PLAYING and e.chunk.is_pause
        ]
        assert len(pause_events) >= 2

    async def test_speak_markdown_skips_code_block(
        self, orchestrator: Orchestrator, mock_tts_client: MockTTSClient,
    ) -> None:
        text = "本文です。\n\n```\ncode\n```\n\n続きです。"
        await orchestrator.start()
        try:
            await orchestrator.speak(text)
        finally:
            await orchestrator.shutdown()
        requested_texts = [r.text for r in mock_tts_client.requests]
        assert all("code" not in t for t in requested_texts)

    async def test_tts_requests_use_config(
        self, orchestrator: Orchestrator, mock_tts_client: MockTTSClient,
    ) -> None:
        await orchestrator.start()
        try:
            await orchestrator.speak("テスト。")
        finally:
            await orchestrator.shutdown()
        assert len(mock_tts_client.requests) >= 1
        req = mock_tts_client.requests[0]
        assert req.caption == "test caption"
        assert req.seed == 42
        assert req.voice == "alloy"

    async def test_events_have_correct_order(
        self, orchestrator: Orchestrator,
    ) -> None:
        events: list[PlaybackEvent] = []
        orchestrator.set_event_callback(lambda e: events.append(e))
        await orchestrator.start()
        try:
            await orchestrator.speak("一文目。二文目。")
        finally:
            await orchestrator.shutdown()
        states = [e.state for e in events]
        for i in range(0, len(states) - 1, 2):
            assert states[i] == PlaybackState.PLAYING
            assert states[i + 1] == PlaybackState.IDLE

    async def test_stop_during_playback(self, orchestrator: Orchestrator) -> None:
        await orchestrator.start()
        try:
            task = asyncio.create_task(
                orchestrator.speak("長いテキスト。" * 20)
            )
            await asyncio.sleep(0.1)
            await orchestrator.stop()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        finally:
            await orchestrator.shutdown()

    async def test_speak_empty_text(self, orchestrator: Orchestrator) -> None:
        events: list[PlaybackEvent] = []
        orchestrator.set_event_callback(lambda e: events.append(e))
        await orchestrator.start()
        try:
            await orchestrator.speak("")
        finally:
            await orchestrator.shutdown()
        assert events == []

    async def test_speak_full_document(
        self, orchestrator: Orchestrator, sample_markdown: str,
    ) -> None:
        events: list[PlaybackEvent] = []
        orchestrator.set_event_callback(lambda e: events.append(e))
        await orchestrator.start()
        try:
            await orchestrator.speak(sample_markdown)
        finally:
            await orchestrator.shutdown()
        playing_events = [
            e for e in events if e.state == PlaybackState.PLAYING
        ]
        assert len(playing_events) > 0
        assert events[-1].state == PlaybackState.IDLE


@pytest.mark.asyncio
class TestOrchestratorBuffering:

    async def test_buffer_ahead_generates_requests_in_advance(
        self, app_config: AppConfig, mock_tts_client: MockTTSClient,
    ) -> None:
        app_config.playback.buffer_ahead = 2

        class SlowBackend:
            def __init__(self) -> None:
                self._counter = 0
                # [CHANGED] 本番コードとインターフェースを合わせる
                self._sample_format: Any = None
                self._nchannels: int = 0
                self._sample_rate: int = 0
            def ensure_init(self) -> None: pass
            def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
                self._counter = 5
            def wait_until_done(self) -> None:
                self._counter -= 1
                return self._counter > 0
            def stop(self) -> None: pass
            def quit(self) -> None: pass

        slow_player = AudioPlayer(backend=SlowBackend())
        orch = Orchestrator(
            config=app_config,
            tts_client=mock_tts_client,  # type: ignore[arg-type]
            audio_player=slow_player,
        )
        await orch.start()
        try:
            await orch.speak("一文目。二文目。三文目。四文目。")
        finally:
            await orch.shutdown()
        assert len(mock_tts_client.requests) == 4

    # [CHANGED] 新規: buffer_ahead=3 (新デフォルト) でも正常動作すること
    async def test_buffer_ahead_default_value(
        self, mock_tts_client: MockTTSClient,
    ) -> None:
        """AppConfig のデフォルト buffer_ahead=3 で正常に全チャンク再生されること"""
        config = AppConfig()  # デフォルト設定を使用
        config.tts.server_url = "http://localhost:9999"

        mock_backend = MockAudioBackend()
        player = AudioPlayer(backend=mock_backend)
        orch = Orchestrator(
            config=config,
            tts_client=mock_tts_client,  # type: ignore[arg-type]
            audio_player=player,
        )

        events: list[PlaybackEvent] = []
        orch.set_event_callback(lambda e: events.append(e))

        await orch.start()
        try:
            await orch.speak("一文目。二文目。三文目。四文目。五文目。")
        finally:
            await orch.shutdown()

        playing_text_events = [
            e for e in events
            if e.state == PlaybackState.PLAYING and e.chunk.is_speakable
        ]
        assert len(playing_text_events) == 5
        assert len(mock_tts_client.requests) == 5

    # [CHANGED] 新規: produce のリトライロジック検証
    async def test_produce_respects_running_flag_on_full_queue(
        self, app_config: AppConfig, mock_tts_client: MockTTSClient,
    ) -> None:
        """キューが満杯の状態で _running=False にすると
        producer が速やかに終了すること"""
        app_config.playback.buffer_ahead = 1

        import threading

        class BlockingBackend:
            """play() で長時間ブロックするバックエンド"""
            def __init__(self) -> None:
                self._done = threading.Event()
                self._sample_format: Any = None
                self._nchannels: int = 0
                self._sample_rate: int = 0
            def ensure_init(self) -> None: pass
            def play(self, audio_data: bytes, fmt: str = "mp3") -> None:
                self._done.clear()
            def wait_until_done(self) -> None:
                # 最大5秒ブロック（テストが stop で解放する想定）
                self._done.wait(timeout=5.0)
            def stop(self) -> None:
                self._done.set()
            def quit(self) -> None:
                self._done.set()

        player = AudioPlayer(backend=BlockingBackend())
        orch = Orchestrator(
            config=app_config,
            tts_client=mock_tts_client,  # type: ignore[arg-type]
            audio_player=player,
        )
        await orch.start()
        try:
            task = asyncio.create_task(
                orch.speak("一文目。二文目。三文目。四文目。五文目。")
            )
            # consumer が最初のチャンクの再生でブロックし、
            # producer がキュー満杯で待機するのを待つ
            await asyncio.sleep(0.3)

            # stop → _running=False → producer/consumer が終了するはず
            await orch.stop()
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pytest.fail("Orchestrator did not stop in time after _running=False")
        finally:
            await orch.shutdown()
