#!/usr/bin/env python3
"""Presence previsualiser -- a renderer that draws diagrams instead of a face.

Connects to the avatar bridge as an ordinary protocol v1 client, runs the
presence schedulers exactly as Unreal eventually will, and streams the resulting
face state to a browser alongside the diagnostics that matter.

It cannot tell you whether the character looks alive. It can tell you whether
the blink intervals have fallen into a rhythm, whether the gaze sequence repeats
more than chance explains, and whether performance transitions settle or
oscillate -- which is most of what tuning this layer actually consists of.

    python3 tools/previz.py                 # then open http://127.0.0.1:8766
    python3 tools/previz.py --audit 30      # no browser: 30 minutes, print a report

Drive it with the sample session in another terminal:

    python3 tools/serve_sample_session.py --loop --speed 2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import json
import logging
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent / "src"))

from websockets.asyncio.client import connect  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

from amanda.avatar import protocol as p  # noqa: E402
from amanda.presence import PresenceEngine  # noqa: E402
from amanda.presence.analysis import (  # noqa: E402
    dwell_fractions,
    interval_report,
    intervals_between,
    longest_repeated_run,
)

log = logging.getLogger("previz")

DIAGNOSTIC_INTERVAL = 0.5
EVENT_LOG_LENGTH = 14


class Previz:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.engine = PresenceEngine(seed=args.seed)
        self.started = time.monotonic()
        self.viewers: set = set()
        self.bridge_state = "waiting"
        self.events_seen = 0
        self.event_log: list[dict[str, object]] = []
        self.gaze_samples: list[tuple[float, str]] = []
        self.preset_changes = 0
        self._last_preset = ""
        self._diagnostics: dict[str, object] = {}
        self._diagnostics_at = 0.0

    @property
    def now(self) -> float:
        return time.monotonic() - self.started

    # ----------------------------------------------------------------- #
    # Bridge client
    # ----------------------------------------------------------------- #

    async def follow_bridge(self) -> None:
        """Stay attached to the bridge, reconnecting for as long as we run.

        The orchestrator is the server, so it can come and go independently --
        the previz should survive restarting it.
        """
        url = f"ws://{self.args.bridge_host}:{self.args.bridge_port}"
        while True:
            try:
                async with connect(url) as connection:
                    self.bridge_state = "connected"
                    log.info("attached to bridge at %s", url)
                    async for message in connection:
                        self._on_message(message)
            except (OSError, ConnectionClosed) as exc:
                if self.bridge_state != "waiting":
                    log.info("bridge gone (%s); retrying", type(exc).__name__)
            self.bridge_state = "waiting"
            await asyncio.sleep(1.0)

    def _on_message(self, message: str | bytes) -> None:
        try:
            envelope = p.decode(message)
        except p.ProtocolError as exc:
            log.warning("undecodable message: %s", exc)
            return

        if not envelope.known:
            # A renderer should skip what it does not implement, not fail.
            self._log_event(envelope.event, "ignored")
            return

        payload = envelope.parse()
        self.events_seen += 1
        self.engine.handle(payload, self.now)
        self._log_event(envelope.event, _summarise(payload))

    def _log_event(self, event: str, detail: str) -> None:
        self.event_log.append({"t": round(self.now, 2), "event": event, "detail": detail})
        del self.event_log[:-EVENT_LOG_LENGTH]

    # ----------------------------------------------------------------- #
    # Frames
    # ----------------------------------------------------------------- #

    async def pump(self) -> None:
        interval = 1.0 / self.args.fps
        while True:
            now = self.now
            state = self.engine.tick(now)
            self._sample(now, state)
            if self.viewers:
                frame = json.dumps(
                    {
                        "face": state.to_dict(),
                        "link": {
                            "bridge": self.bridge_state,
                            "events": self.events_seen,
                            "uptime": round(now, 1),
                        },
                        "log": self.event_log,
                        "diagnostics": self._diagnostics,
                    }
                )
                for viewer in list(self.viewers):
                    with contextlib.suppress(Exception):
                        await viewer.send(frame)
            await asyncio.sleep(interval)

    def _sample(self, now: float, state) -> None:
        self.gaze_samples.append((now, state.gaze_target))
        del self.gaze_samples[:-60000]
        if state.preset != self._last_preset:
            if self._last_preset:
                self.preset_changes += 1
            self._last_preset = state.preset
        if now - self._diagnostics_at >= DIAGNOSTIC_INTERVAL:
            self._diagnostics_at = now
            self._diagnostics = self.report()

    def report(self) -> dict[str, object]:
        blink = interval_report(self.engine.blink_times)
        history = [target.value for target in self.engine.gaze.history]
        repetition = longest_repeated_run(history)
        elapsed = max(1e-6, self.now)
        return {
            "blink": {
                "count": blink.count,
                "per_minute": round(blink.count / (elapsed / 60.0), 1),
                "mean": round(blink.mean, 2),
                "cv": round(blink.cv, 2),
                "rhythm": round(blink.rhythm, 2),
                "verdict": blink.verdict,
                # The intervals themselves, so the viewer can show the shape
                # rather than a summary. Even bars are the tell.
                "intervals": [
                    round(gap, 2) for gap in intervals_between(self.engine.blink_times)[-48:]
                ],
            },
            "gaze": {
                "shifts": repetition.span,
                "repeat": repetition.length,
                "baseline": round(repetition.baseline, 1),
                "excess": round(repetition.excess, 1),
                "pattern": list(repetition.pattern),
                "verdict": repetition.verdict,
                "dwell": {k: round(v, 3) for k, v in dwell_fractions(self.gaze_samples).items()},
            },
            "performance": {
                "changes": self.preset_changes,
                "refused": self.engine.smoother.refused,
            },
        }

    # ----------------------------------------------------------------- #
    # Viewer server
    # ----------------------------------------------------------------- #

    async def serve_viewers(self, connection) -> None:
        self.viewers.add(connection)
        log.info("viewer attached (%d total)", len(self.viewers))
        try:
            await connection.wait_closed()
        finally:
            self.viewers.discard(connection)

    async def run(self) -> int:
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(TOOLS))
        httpd = ThreadingHTTPServer((self.args.host, self.args.http_port), handler)
        httpd.log_message = lambda *a, **k: None  # type: ignore[method-assign]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        async with serve(self.serve_viewers, self.args.host, self.args.ws_port):
            log.info(
                "previz ready -- open http://%s:%d/previz.html",
                self.args.host,
                self.args.http_port,
            )
            await asyncio.gather(self.follow_bridge(), self.pump())
        return 0


def _summarise(payload: p.Payload) -> str:
    fields = payload.to_dict()
    if not fields:
        return ""
    return " ".join(f"{key}={value}" for key, value in list(fields.items())[:3])


def audit(args: argparse.Namespace) -> int:
    """Run the schedulers offline and print the diagnostics.

    No browser, no bridge, no waiting: thirty minutes of behaviour takes about a
    second. This is the mode to use when tuning a constant, because you can put
    it in a loop.
    """
    engine = PresenceEngine(seed=args.seed)
    seconds = args.audit * 60.0
    step = 1.0 / args.fps
    now = 0.0
    samples: list[tuple[float, str]] = []
    while now < seconds:
        state = engine.tick(now)
        samples.append((now, state.gaze_target))
        now += step

    blink = interval_report(engine.blink_times)
    repetition = longest_repeated_run([t.value for t in engine.gaze.history])
    dwell = dwell_fractions(samples)

    print(f"\n{args.audit:g} minutes, seed {args.seed}\n")
    print("  blink")
    print(f"    {blink.count} blinks, {blink.count / args.audit:.1f}/min, mean {blink.mean:.2f}s")
    print(f"    cv {blink.cv:.2f}  rhythm {blink.rhythm:.2f}  -> {blink.verdict}")
    print("\n  gaze")
    print(f"    {repetition.span} shifts, longest repeat {repetition.length}")
    print(
        f"    chance baseline {repetition.baseline:.1f}, "
        f"excess {repetition.excess:+.1f} -> {repetition.verdict}"
    )
    if repetition.pattern:
        print(f"    pattern: {' > '.join(repetition.pattern)}")
    print("\n  dwell")
    for target, fraction in sorted(dwell.items(), key=lambda item: -item[1]):
        bar = "#" * round(fraction * 40)
        print(f"    {target:<18} {fraction:5.1%} {bar}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-host", default="127.0.0.1")
    parser.add_argument("--bridge-port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8766)
    parser.add_argument("--ws-port", type=int, default=8767)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=None, help="fix the RNG for a repeatable run")
    parser.add_argument(
        "--audit", type=float, metavar="MINUTES",
        help="run offline for this many minutes and print a report instead of serving",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.audit:
        return audit(args)

    previz = Previz(args)
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(previz.run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
