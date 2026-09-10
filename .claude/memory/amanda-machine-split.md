---
name: amanda-machine-split
description: Project Amanda runs across two machines — a Linux dev laptop that cannot run Unreal, and a Windows box that hasn't been set up yet
metadata:
  type: project
---

Project Amanda (`~/code/Amanda`, github.com/robcowell/project-amanda) is split
across two machines and only one of them exists so far.

The dev laptop is a 2017 ultrabook: i7-8550U, Intel UHD 620 integrated graphics,
15 GB RAM. It **cannot run Unreal or MetaHuman** at any useful quality. It runs
the Python orchestrator, and has no `ANTHROPIC_API_KEY` — so `python -m
amanda.main` needs `--scripted` there, which uses canned replies through the
real segmenter, TTS and bridge.

Rob has a Windows PC with a discrete GPU that is the renderer target. As of
2026-09-09 nothing has been installed on it: Phase 0 (Unreal, a MetaHuman,
lighting, look-dev) has not started, and the C++ bridge plugin in
`unreal/AmandaBridge/` has been written but **never compiled**.

**Why:** everything on the Linux side is deliberately built to run headless and
keyless so progress isn't blocked on hardware that isn't set up.

**How to apply:** don't suggest running or testing Unreal work locally, and
don't assume Claude API calls will work from the laptop. See
[[amanda-livelink-unknown]] for the risk that gates the Windows work.
