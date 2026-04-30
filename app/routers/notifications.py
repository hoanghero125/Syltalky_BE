import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.deps import get_current_user
from app.core.security import decode_token
from app.database import get_db, AsyncSessionLocal
from app.models.notification import Notification
from app.models.user import User

router = APIRouter(prefix="/notifications", tags=["notifications"])

# In-memory registry: user_id → set of WebSockets
_user_connections: dict[str, set[WebSocket]] = defaultdict(set)


async def push_notification(user_id: str, notification: dict):
    """Push a notification to all connected WebSockets for a user."""
    dead = set()
    for ws in list(_user_connections.get(user_id, set())):
        try:
            await ws.send_json(notification)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _user_connections[user_id].discard(ws)


@router.get("")
async def list_notifications(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    notifs = result.scalars().all()
    return [
        {
            "id": str(n.id),
            "type": n.type,
            "payload": n.payload,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifs
    ]


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    notif = result.scalar_one_or_none()
    if notif:
        notif.is_read = True
        await db.commit()
    return {"ok": True}


@router.websocket("/ws")
async def notifications_ws(
    websocket: WebSocket,
    token: str = Query(...),
):
    payload = decode_token(token)
    user_id_str = payload.get("sub")
    if not user_id_str or payload.get("type") != "access":
        await websocket.close(code=4001)
        return

    await websocket.accept()
    _user_connections[user_id_str].add(websocket)

    # Send unread count on connect
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Notification).where(
                Notification.user_id == uuid.UUID(user_id_str),
                Notification.is_read.is_(False),
            )
        )
        unread = result.scalars().all()
        if unread:
            await websocket.send_json({
                "type": "unread_count",
                "count": len(unread),
                "notifications": [
                    {
                        "id": str(n.id),
                        "type": n.type,
                        "payload": n.payload,
                        "is_read": n.is_read,
                        "created_at": n.created_at.isoformat() if n.created_at else None,
                    }
                    for n in unread
                ],
            })

    try:
        while True:
            await websocket.receive_text()  # keep alive (ping)
    except WebSocketDisconnect:
        pass
    finally:
        _user_connections[user_id_str].discard(websocket)
