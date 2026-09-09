"""Local WebSocket server carrying protocol v1 to the renderer.

Bound to loopback by default (build plan 25). The orchestrator is the server
and the renderer is the client, so Unreal can be restarted freely mid-session
without taking the conversation down with it.

Backlog (epic 4): start server, accept the Unreal client, send state and speech
lifecycle events, reconnect behaviour. On reconnect the bridge must send
`avatar.reset` before anything else so the renderer never inherits a mood from
a dead connection.

TODO: implement. The protocol it will speak is already defined and tested in
`amanda.avatar.protocol`.
"""
