from __future__ import annotations

import asyncio
from pathlib import Path

import pyrogram_compat  # noqa: F401
from pyrogram.errors import FloodWait, PeerFlood

from account_manager import AccountManager
from configs import Config
from crypto import encrypt_text
from utils import load_json, normalize_chat_target, save_json_atomic


async def save_session_string(config: Config, phone: str, session_string: str, password: str) -> None:
    out = {
        "phone": phone,
        "session": encrypt_text(session_string, password=password),
    }
    path = Path(config.sessions_dir) / f"{phone}.json"
    save_json_atomic(str(path), out)


def load_checkpoint(path: str) -> dict:
    return load_json(path, default={})


def save_checkpoint(path: str, payload: dict) -> None:
    save_json_atomic(path, payload)


async def with_available_client(
    manager: AccountManager,
    password: str,
    task_coro,
    max_switch: int = 50,
):
    tried: set[str] = set()
    switches = 0
    while switches < max_switch:
        available = [p for p in manager.next_available_phones() if p not in tried]
        if not available:
            raise RuntimeError("No available account ready right now")
        phone = available[0]
        client = await manager.build_client(phone=phone, password=password)
        await client.connect()
        try:
            return await task_coro(client, phone)
        except FloodWait as fw:
            if int(fw.value) >= 3600:
                manager.set_cooldown(phone=phone, seconds=int(fw.value))
                tried.add(phone)
                switches += 1
            else:
                await asyncio.sleep(int(fw.value) + 2)
        finally:
            try:
                await client.disconnect()
            except Exception:
                # Avoid masking success with a non-critical disconnect error.
                pass
    raise RuntimeError("Could not finish task after account switches")


async def execute_with_rotation(
    manager: AccountManager,
    password: str,
    operation,
    max_switch: int = 25,
) -> tuple[object, str]:
    switched: set[str] = set()

    for _ in range(max_switch):
        phone = manager.get_next_phone(exclude=switched)
        if not phone:
            wait_s = manager.seconds_until_next_available()
            raise RuntimeError(
                "No account available right now"
                if wait_s <= 0
                else f"No account available, retry in about {wait_s}s"
            )

        app = await manager.build_client(phone=phone, password=password)
        await app.connect()
        try:
            result = await operation(app, phone)
            return result, phone
        except FloodWait as fw:
            if int(fw.value) >= 3600:
                manager.set_cooldown(phone=phone, seconds=int(fw.value))
                switched.add(phone)
            else:
                await asyncio.sleep(int(fw.value) + 2)
        except PeerFlood:
            manager.set_cooldown(phone=phone, seconds=7200)
            switched.add(phone)
        finally:
            try:
                await app.disconnect()
            except Exception:
                # Avoid masking success with a non-critical disconnect error.
                pass

    raise RuntimeError("Unable to complete after rotating through available accounts")


async def resolve_target_chat(app, raw_target: str):
    target = normalize_chat_target(raw_target)
    if not target:
        raise RuntimeError("Target group kosong atau format link tidak valid")

    last_exc = None

    # For invite links like https://t.me/+xxxx, try join first, then resolve chat.
    if target.startswith("+"):
        try:
            await app.join_chat(f"https://t.me/{target}")
        except Exception as exc:
            last_exc = exc

    for candidate in [target, raw_target.strip()]:
        if not candidate:
            continue
        candidate_str = str(candidate).strip()
        if not candidate_str:
            continue
        numeric_candidate = None
        if candidate_str.lstrip("-").isdigit():
            numeric_candidate = str(int(candidate_str))
            candidate = int(candidate_str)
        else:
            candidate = candidate_str

        try:
            return await app.get_chat(candidate)
        except Exception as exc:
            last_exc = exc

            # Fallback ke dialog scan: cocokkan ID numerik atau username.
            # Berguna saat akun sudah join grup tapi internal username cache
            # Pyrogram belum populated, sehingga get_chat(username) lempar error.
            try:
                want_username = (
                    None
                    if numeric_candidate is not None
                    else candidate_str.lstrip("@").lower()
                )
                async for dialog in app.get_dialogs():
                    chat = getattr(dialog, "chat", None)
                    if not chat:
                        continue
                    if numeric_candidate is not None and str(chat.id) == numeric_candidate:
                        return chat
                    if want_username:
                        chat_username = (getattr(chat, "username", None) or "").lower()
                        if chat_username and chat_username == want_username:
                            return chat
            except Exception as exc2:
                last_exc = exc2

    detail = ""
    if last_exc is not None:
        detail = f" (root cause: {type(last_exc).__name__}: {last_exc})"
    raise RuntimeError(
        "Group/link tidak valid. Gunakan @username, https://t.me/username, "
        f"atau invite link https://t.me/+xxxx{detail}"
    ) from last_exc
