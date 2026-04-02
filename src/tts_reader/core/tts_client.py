"""TTSサーバ通信（ストリーミング）"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from tts_reader.core.config import TTSConfig
from tts_reader.core.models import TTSRequest


class TTSClient:
    def __init__(self, config: TTSConfig | None = None) -> None:
        self._config = config or TTSConfig()
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._config.timeout),
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def build_request(
        self,
        text: str,
        *,
        caption: str | None = None,
        seed: int | None = None,
    ) -> TTSRequest:
        return TTSRequest(
            text=text,
            caption=caption if caption is not None else self._config.caption,
            seed=seed if seed is not None else self._config.seed,
            voice=self._config.voice,
            model=self._config.model,
            response_format=self._config.response_format,
            speed=self._config.speed,
        )

    async def synthesize_stream(
        self,
        request: TTSRequest,
        chunk_size: int = 4096,
    ) -> AsyncIterator[bytes]:
        if self._session is None:
            await self.start()
        assert self._session is not None
        url = f"{self._config.server_url}{self._config.endpoint}"
        payload = self._build_payload(request)
        async with self._session.post(url, json=payload) as resp:
            resp.raise_for_status()
            async for data in resp.content.iter_chunked(chunk_size):
                yield data

    async def synthesize(self, request: TTSRequest) -> bytes:
        parts: list[bytes] = []
        async for chunk in self.synthesize_stream(request):
            parts.append(chunk)
        return b"".join(parts)

    def _build_payload(self, request: TTSRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "input": request.text,
            "voice": request.voice,
            "response_format": request.response_format,
            "speed": request.speed,
        }
        if request.caption:
            payload["caption"] = request.caption
        if request.seed is not None:
            payload["seed"] = request.seed
        return payload
