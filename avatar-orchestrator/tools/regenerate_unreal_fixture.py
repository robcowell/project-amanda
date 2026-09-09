#!/usr/bin/env python3
"""Regenerate the C++ conformance fixture from the sample session.

The Unreal decoder is tested against the exact bytes this orchestrator emits,
not against a hand-typed copy of them -- so whenever the protocol or the sample
session changes, run this and commit the result.

    python3 tools/regenerate_unreal_fixture.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = (
    ROOT.parent / "unreal" / "AmandaBridge" / "Source" / "AmandaBridge"
    / "Private" / "Tests" / "AmandaSampleSession.inl"
)

HEADER = """// Generated -- do not edit.
//
// The sample session, emitted by the Python reference implementation, so the
// C++ decoder is tested against the exact bytes the orchestrator sends rather
// than against a hand-typed approximation of them.
//
// Regenerate from avatar-orchestrator/:
//     python3 tools/regenerate_unreal_fixture.py

static const TCHAR* AmandaSampleSession[] = {
"""


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    lines = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "emit_sample_session.py")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    body = "\n".join(f'\tTEXT("{escape(line)}"),' for line in lines)
    TARGET.write_text(f"{HEADER}{body}\n}};\n")
    print(f"wrote {len(lines)} messages to {TARGET.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
