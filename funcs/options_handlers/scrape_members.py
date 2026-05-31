import asyncio
import getpass

import pyrogram_compat  # noqa: F401
from pyrogram.enums import ChatMembersFilter, MessageEntityType
from rich.prompt import Confirm, Prompt

from account_manager import AccountManager
from configs import Config
from funcs.helpers import execute_with_rotation, load_checkpoint, resolve_target_chat, save_checkpoint
from funcs.ui import error, info, success, warn
from utils import append_members_dedup, infer_gender, normalize_menu_choice, per_group_members_path


async def handle_scrape(config: Config, manager: AccountManager) -> None:
    info("01 - Scrape Non-Hidden Members")
    info("02 - Scrape Hidden Members (from messages)")
    info("03 - Scrape Visible + Hidden (jalankan keduanya)")
    info("00 - Back")
    choice = normalize_menu_choice(Prompt.ask("Choose", default="00"))

    if choice not in {"01", "02", "03"}:
        return

    password = getpass.getpass("Encryption password: ")
    target = Prompt.ask("Group username/link").strip()

    if choice == "01":
        await _scrape_visible(config, manager, password, target)
    elif choice == "02":
        await _scrape_hidden(config, manager, password, target)
    else:
        info("Mode Visible + Hidden — menjalankan keduanya berurutan...")
        await _scrape_visible(config, manager, password, target)
        await _scrape_hidden(config, manager, password, target)


async def _scrape_visible(config: Config, manager: AccountManager, password: str, target: str) -> None:
    rows = []
    chat_info: dict = {"title": "", "id": ""}

    async def _op(app, _phone: str):
        chat = await resolve_target_chat(app, target)
        chat_info["title"] = chat.title or ""
        chat_info["id"] = str(chat.id)
        async for member in app.get_chat_members(chat.id, filter=ChatMembersFilter.SEARCH):
            user = member.user
            if not user or user.is_bot:
                continue
            rows.append(_row_from_user(user, chat.title or "", str(chat.id)))
        return True

    try:
        _, phone = await execute_with_rotation(manager, password, _op)
        before, after = append_members_dedup(config.members_csv, rows)
        per_group = _write_per_group(config, chat_info, rows)
        success(f"Scrape selesai via {phone}. Unique before={before}, after={after}")
        if per_group:
            success(f"Per-grup CSV: {per_group}")
    except Exception as exc:
        error(f"Scrape gagal: {exc}")


async def _scrape_hidden(config: Config, manager: AccountManager, password: str, target: str) -> None:
    checkpoint = load_checkpoint(config.checkpoint_file)
    start_from = 0
    users: dict[str, dict] = {}
    chat_info: dict = {"title": "", "id": ""}

    if checkpoint.get("target") == target and checkpoint.get("last_message_id"):
        if Confirm.ask("Checkpoint ditemukan. Lanjutkan?", default=True):
            start_from = int(checkpoint.get("last_message_id", 0))
            users = checkpoint.get("users", {})

    try:
        async def _op(app, _phone: str):
            chat = await resolve_target_chat(app, target)
            chat_info["title"] = chat.title or ""
            chat_info["id"] = str(chat.id)
            counter = 0
            async for msg in app.get_chat_history(chat.id):
                if start_from and msg.id >= start_from:
                    continue

                extracted = await _extract_users_from_msg(app, msg, chat.title or "", str(chat.id))
                if extracted:
                    for row in extracted:
                        users[row["ID"]] = row
                    counter += len(extracted)
                    if counter % 50 == 0:
                        save_checkpoint(
                            config.checkpoint_file,
                            {"target": target, "last_message_id": msg.id, "users": users},
                        )
                        info(f"Checkpoint saved at message {msg.id}, users={len(users)}")

                if counter and counter % 300 == 0:
                    await asyncio.sleep(0.5)

            return True

        _, phone = await execute_with_rotation(manager, password, _op)

        rows_list = list(users.values())
        before, after = append_members_dedup(config.members_csv, rows_list)
        per_group = _write_per_group(config, chat_info, rows_list)
        save_checkpoint(config.checkpoint_file, {})
        success(f"Hidden scrape selesai via {phone}. Unique before={before}, after={after}")
        if per_group:
            success(f"Per-grup CSV: {per_group}")
    except KeyboardInterrupt:
        warn("Interrupted. Checkpoint tersimpan.")
    except Exception as exc:
        error(f"Hidden scrape gagal: {exc}")


def _write_per_group(config: Config, chat_info: dict, rows: list[dict]) -> str | None:
    if not rows:
        return None
    title = (chat_info or {}).get("title") or ""
    if not title.strip():
        title = f"group_{(chat_info or {}).get('id') or 'untitled'}"
    try:
        path = per_group_members_path(config.members_csv, title)
        append_members_dedup(str(path), rows)
        return str(path)
    except Exception as exc:
        warn(f"Gagal tulis per-grup CSV: {type(exc).__name__}: {exc}")
        return None


def _row_from_user(user, group_name: str, group_id: str) -> dict:
    full_name = (user.first_name or "") + (f" {user.last_name}" if user.last_name else "")
    return {
        "Name": full_name,
        "ID": str(user.id),
        "Username": user.username or "",
        "Access Hash": str(getattr(user, "access_hash", "")),
        "Gender": infer_gender(full_name),
        "Group Name": group_name,
        "Group ID": group_id,
    }


async def _extract_users_from_msg(app, msg, group_name: str, group_id: str) -> list[dict]:
    rows: dict[str, dict] = {}

    if msg.from_user and not msg.from_user.is_bot:
        rows[str(msg.from_user.id)] = _row_from_user(msg.from_user, group_name, group_id)

    if getattr(msg, "forward_from", None) and not msg.forward_from.is_bot:
        rows[str(msg.forward_from.id)] = _row_from_user(msg.forward_from, group_name, group_id)

    entities = msg.entities or []
    text = msg.text or msg.caption or ""
    for ent in entities:
        if ent.type == MessageEntityType.TEXT_MENTION and getattr(ent, "user", None):
            u = ent.user
            if not u.is_bot:
                rows[str(u.id)] = _row_from_user(u, group_name, group_id)
        elif ent.type == MessageEntityType.MENTION:
            mention = text[ent.offset : ent.offset + ent.length].strip()
            username = mention.lstrip("@")
            if not username:
                continue
            try:
                u = await app.get_users(username)
                if u and not u.is_bot:
                    rows[str(u.id)] = _row_from_user(u, group_name, group_id)
            except Exception:
                continue

    return list(rows.values())
