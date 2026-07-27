"""
§8 — real-time in-app channel. Feeds the card member interface's live
timeline while the app is open, off the same event stream as push/SMS/
email — one process_notification function, one extra branch.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("notification.realtime")

active_connections: dict[str, WebSocket] = {}


async def register_connection(websocket: WebSocket, member_id: str) -> None:
    await websocket.accept()
    active_connections[member_id] = websocket
    try:
        while True:
            await websocket.receive_text()  # keep-alive
    except WebSocketDisconnect:
        active_connections.pop(member_id, None)


async def push_live_update(member_id: str, event: dict) -> None:
    ws = active_connections.get(member_id)
    if ws is None:
        return
    try:
        await ws.send_json(event)
    except Exception as e:
        logger.info(f"push_live_update failed for member={member_id}, dropping connection: {e}")
        active_connections.pop(member_id, None)
