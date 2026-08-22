"""Telethon helpers for auth, group lookup, and message fetch (last N days)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom.dialog import Dialog
from telethon.tl.types import User, Channel, Chat


def load_credentials() -> tuple[int, str, str]:
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session = os.getenv("TELEGRAM_SESSION", "housing_session").strip() or "housing_session"
    if not api_id or not api_hash:
        raise ValueError(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env "
            "(from https://my.telegram.org)."
        )
    return int(api_id), api_hash, session


def create_client(
    api_id: Optional[int] = None,
    api_hash: Optional[str] = None,
    session: Optional[str] = None,
) -> TelegramClient:
    if api_id is None or api_hash is None or session is None:
        loaded_id, loaded_hash, loaded_session = load_credentials()
        api_id = api_id or loaded_id
        api_hash = api_hash or loaded_hash
        session = session or loaded_session
    return TelegramClient(session, api_id, api_hash)


async def is_authorized(client: TelegramClient) -> bool:
    await client.connect()
    return await client.is_user_authorized()


async def send_login_code(client: TelegramClient, phone: str) -> str:
    await client.connect()
    result = await client.send_code_request(phone)
    return result.phone_code_hash


async def complete_login(
    client: TelegramClient,
    phone: str,
    code: str,
    phone_code_hash: str,
    password: Optional[str] = None,
) -> None:
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if not password:
            raise ValueError("Two-step verification is on — enter your cloud password.")
        await client.sign_in(password=password)


def _sender_info(sender: Any) -> dict[str, Any]:
    if sender is None:
        return {
            "sender_id": None,
            "sender_name": "Unknown",
            "sender_username": None,
        }
    if isinstance(sender, User):
        name_parts = [sender.first_name or "", sender.last_name or ""]
        name = " ".join(p for p in name_parts if p).strip() or "Unknown"
        return {
            "sender_id": sender.id,
            "sender_name": name,
            "sender_username": sender.username,
        }
    if isinstance(sender, (Channel, Chat)):
        return {
            "sender_id": sender.id,
            "sender_name": getattr(sender, "title", None) or "Unknown",
            "sender_username": getattr(sender, "username", None),
        }
    return {
        "sender_id": getattr(sender, "id", None),
        "sender_name": str(sender),
        "sender_username": None,
    }


async def list_group_dialogs(client: TelegramClient) -> list[dict[str, Any]]:
    await client.connect()
    groups: list[dict[str, Any]] = []
    async for dialog in client.iter_dialogs():
        d: Dialog = dialog
        if not (d.is_group or d.is_channel):
            continue
        entity = d.entity
        username = getattr(entity, "username", None)
        groups.append(
            {
                "id": d.id,
                "title": d.title,
                "username": username,
                "is_channel": d.is_channel,
            }
        )
    groups.sort(key=lambda g: (g["title"] or "").lower())
    return groups


async def fetch_messages(
    client: TelegramClient,
    group: str | int,
    days: int = 30,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Fetch messages from the last `days` days for a group username, link, or id."""
    await client.connect()
    entity = await client.get_entity(group)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict[str, Any]] = []

    async for msg in client.iter_messages(entity, limit=limit):
        if msg.date is None:
            continue
        msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
        if msg_date < cutoff:
            break

        text = msg.message or msg.text or ""
        if not text.strip():
            # Skip pure media/stickers with no caption for housing search
            continue

        sender = await msg.get_sender()
        info = _sender_info(sender)
        chat_title = getattr(entity, "title", None) or str(group)

        rows.append(
            {
                "date": msg_date.isoformat(),
                "message_id": msg.id,
                "group": chat_title,
                "group_id": getattr(entity, "id", None),
                "text": text,
                **info,
            }
        )

    return rows
