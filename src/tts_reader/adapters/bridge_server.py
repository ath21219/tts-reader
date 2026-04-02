"""WebSocket ブリッジサーバ"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from tts_reader.core.config import AppConfig
from tts_reader.core.models import PlaybackEvent, PlaybackState, ChunkType
from tts_reader.core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class BridgeServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9120,
        config: AppConfig | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._config = config or AppConfig()
        self._server: Server | None = None
        self._clients: set[ServerConnection] = set()
        self._orchestrator: Orchestrator | None = None
        self._speak_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._event_dispatcher_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._orchestrator = Orchestrator(config=self._config)
        self._orchestrator.set_event_callback(self._on_playback_event)
        await self._orchestrator.start()
        self._event_dispatcher_task = asyncio.create_task(self._dispatch_events())
        self._server = await websockets.serve(
            self._handle_client,
            self._host,
            self._port,
        )
        logger.info("Bridge server listening on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._orchestrator:
            await self._orchestrator.shutdown()
        if self._event_dispatcher_task and not self._event_dispatcher_task.done():
            self._event_queue.put_nowait(None)  # 終了シグナル
            try:
                await asyncio.wait_for(self._event_dispatcher_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._event_dispatcher_task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Bridge server stopped.")

    async def serve_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def _dispatch_events(self) -> None:
        """キューからイベントを取り出してブロードキャストするワーカー。

        _on_playback_event は同期コールバックとして呼ばれるため、
        直接 await self._broadcast() できない。代わりにキューに投入し、
        この専用タスクが非同期にブロードキャストする。
        これにより call_soon_threadsafe + ensure_future の
        fire-and-forget 問題を解消し、送信順序も保証する。
        """
        while True:
            data = await self._event_queue.get()
            if data is None:
                break
            try:
                await self._broadcast(data)
            except Exception:
                logger.exception("Failed to broadcast event")

    async def _handle_client(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        remote = ws.remote_address
        logger.info("Client connected: %s", remote)
        try:
            async for raw in ws:
                await self._dispatch(ws, raw)
        except websockets.ConnectionClosed:
            logger.info("Client disconnected: %s", remote)
        finally:
            self._clients.discard(ws)

    async def _dispatch(self, ws: ServerConnection, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(ws, {"event": "error", "data": {"message": "Invalid JSON"}})
            return
        method = msg.get("method", "")
        params = msg.get("params", {})
        match method:
            case "speak":
                await self._handle_speak(ws, params)
            case "stop":
                await self._handle_stop(ws)
            case "configure":
                await self._handle_configure(ws, params)
            case _:
                await self._send(ws, {
                    "event": "error",
                    "data": {"message": f"Unknown method: {method}"},
                })

    async def _handle_speak(self, ws: ServerConnection, params: dict[str, Any]) -> None:
        text = params.get("text", "")
        if not text:
            await self._send(ws, {
                "event": "error",
                "data": {"message": "No text provided"},
            })
            return
        if self._speak_task and not self._speak_task.done():
            assert self._orchestrator is not None
            await self._orchestrator.stop()
            self._speak_task.cancel()
            try:
                await self._speak_task
            except asyncio.CancelledError:
                pass
        assert self._orchestrator is not None
        self._speak_task = asyncio.create_task(self._run_speak(text))

    async def _run_speak(self, text: str) -> None:
        assert self._orchestrator is not None
        try:
            await self._orchestrator.speak(text)
            await self._broadcast({"event": "done"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Speak failed")
            await self._broadcast({
                "event": "error",
                "data": {"message": str(e)},
            })

    async def _handle_stop(self, ws: ServerConnection) -> None:
        if self._orchestrator:
            await self._orchestrator.stop()
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
            try:
                await self._speak_task
            except asyncio.CancelledError:
                pass

    async def _handle_configure(
        self, ws: ServerConnection, params: dict[str, Any],
    ) -> None:
        tts = self._config.tts
        if "caption" in params:
            tts.caption = params["caption"]
        if "seed" in params:
            tts.seed = params["seed"]
        if "voice" in params:
            tts.voice = params["voice"]
        if "speed" in params:
            tts.speed = params["speed"]
        if "server_url" in params:
            tts.server_url = params["server_url"]
        playback = self._config.playback
        if "buffer_ahead" in params:
            playback.buffer_ahead = params["buffer_ahead"]
        logger.info("Configuration updated: %s", params)
        await self._send(ws, {
            "event": "configured",
            "data": params,
        })

    def _on_playback_event(self, event: PlaybackEvent) -> None:
        """同期コールバック — イベントキューに投入するだけ。

        旧実装では loop.call_soon_threadsafe + asyncio.ensure_future で
        fire-and-forget していたが、以下の問題があった:
        - イベントループが忙しい場合に送信が遅延しハイライトがずれる
        - 送信順序が保証されない
        キュー + 専用ディスパッチャタスクにより両方を解消する。
        """
        chunk = event.chunk
        data = {
            "event": "playback",
            "data": {
                "state": event.state.name.lower(),
                "chunk": {
                    "index": chunk.index,
                    "content": chunk.content,
                    "type": chunk.chunk_type.name.lower(),
                    "source_offset": chunk.source_offset,
                    "source_length": chunk.source_length,
                    "pause_duration": chunk.pause_duration,
                },
            },
        }
        try:
            self._event_queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping playback event for chunk %d", chunk.index)

    async def _broadcast(self, data: dict[str, Any]) -> None:
        if not self._clients:
            return
        payload = json.dumps(data, ensure_ascii=False)
        coros = [self._send(ws, data, _raw=payload) for ws in self._clients.copy()]
        await asyncio.gather(*coros, return_exceptions=True)

    async def _send(
        self,
        ws: ServerConnection,
        data: dict[str, Any],
        *,
        _raw: str | None = None,
    ) -> None:
        try:
            await ws.send(_raw or json.dumps(data, ensure_ascii=False))
        except websockets.ConnectionClosed:
            self._clients.discard(ws)
