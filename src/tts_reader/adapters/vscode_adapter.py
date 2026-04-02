"""VSCode アダプタ — ブリッジサーバ起動エントリポイント"""

from __future__ import annotations

import asyncio
import argparse
import logging
import signal

from tts_reader.core.config import AppConfig, TTSConfig, PlaybackConfig
from tts_reader.adapters.bridge_server import BridgeServer


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TTS Reader Bridge Server for VSCode")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9120)
    p.add_argument("--tts-url", default="http://localhost:8000")
    p.add_argument("--voice", default="alloy")
    p.add_argument("--model", default="tts-1")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    config = AppConfig(
        tts=TTSConfig(
            server_url=args.tts_url,
            voice=args.voice,
            model=args.model,
        ),
        playback=PlaybackConfig(),
    )
    server = BridgeServer(host=args.host, port=args.port, config=config)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    await server.start()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
