#!/usr/bin/env python3
"""A renderer that prints instead of drawing.

Verifies the bridge end to end without Unreal, and shows exactly what the
renderer will receive -- including the resync burst on connect.

    python3 tools/mock_renderer.py
    python3 tools/mock_renderer.py --count 12   # exit after 12 events
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from websockets.asyncio.client import connect  # noqa: E402

from amanda.avatar import protocol as p  # noqa: E402
from amanda.avatar.websocket import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402


def describe(envelope: p.Envelope) -> str:
    if not envelope.known:
        return f"{envelope.event}  (unknown event -- ignoring, as a renderer should)"
    payload = envelope.parse()
    fields = {
        key: value
        for key, value in payload.to_dict().items()
        if key not in {"transition_ms", "hold_ms"}
    }
    detail = "  ".join(f"{key}={value}" for key, value in fields.items())
    return f"{envelope.event:<28}{detail}"


async def main_async(args: argparse.Namespace) -> int:
    uri = f"ws://{args.host}:{args.port}"
    print(f"connecting to {uri}")
    async with connect(uri) as connection:
        print("connected\n")
        started = time.monotonic()
        seen = 0
        async for message in connection:
            try:
                envelope = p.decode(message)
            except p.ProtocolError as exc:
                print(f"  !! unreadable message: {exc}")
                continue
            print(f"{time.monotonic() - started:7.3f}s  {describe(envelope)}")
            seen += 1
            if args.count and seen >= args.count:
                break
        print(f"\n{seen} events received")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--count", type=int, default=0, help="exit after N events (0 = forever)")
    args = parser.parse_args()

    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
