#!/usr/bin/env python3
"""Replay the sample session over the real bridge, with real timing.

Lets the renderer be built and tuned against a live protocol v1 connection
before Claude, TTS or a microphone exist. Point Unreal at ws://127.0.0.1:8765.

    python3 tools/serve_sample_session.py            # wait for a renderer, replay once
    python3 tools/serve_sample_session.py --speed 4  # four times faster
    python3 tools/serve_sample_session.py --loop     # replay until interrupted

Disconnect the renderer mid-replay and reconnect it to exercise resync.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from amanda.avatar.websocket import DEFAULT_HOST, DEFAULT_PORT, AvatarBridge  # noqa: E402
from emit_sample_session import SESSION  # noqa: E402

log = logging.getLogger("serve_sample_session")


async def replay(bridge: AvatarBridge, speed: float) -> None:
    elapsed = 0.0
    for offset, payload in SESSION:
        await asyncio.sleep(max(0.0, (offset - elapsed) / speed))
        elapsed = offset
        bridge.send(payload)
        log.info("%7.3fs  %s", offset, payload.event.value)


async def main_async(args: argparse.Namespace) -> int:
    async with AvatarBridge(args.host, args.port) as bridge:
        log.info("waiting for a renderer on ws://%s:%d ...", bridge.host, bridge.port)
        if not await bridge.wait_for_client(timeout=args.wait):
            log.error("no renderer connected within %ss", args.wait)
            return 1
        log.info("renderer connected; replaying %d events at %.1fx", len(SESSION), args.speed)

        while True:
            await replay(bridge, args.speed)
            if not args.loop:
                break
            log.info("--- looping ---")
            await asyncio.sleep(1.0)

        log.info(
            "done: %d sent, %d dropped with no renderer attached",
            bridge.stats.messages_sent,
            bridge.stats.messages_dropped,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--speed", type=float, default=1.0, help="playback rate multiplier")
    parser.add_argument("--loop", action="store_true", help="replay until interrupted")
    parser.add_argument("--wait", type=float, default=120.0, help="seconds to wait for a renderer")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
