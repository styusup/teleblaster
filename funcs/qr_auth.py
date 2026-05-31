import asyncio
import base64
import time
from pathlib import Path
from typing import Callable

import qrcode
from pyrogram import handlers, raw
from pyrogram.errors import FloodWait
from pyrogram.session import Auth, Session


def _print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    print("\nScan QR ini dari Telegram mobile: Settings > Devices > Link Desktop Device\n")
    for row in matrix:
        line = "".join("██" if cell else "  " for cell in row)
        print(line)
    print(f"\nURL: {url}\n")


def _save_qr_png(url: str, out_path: str = "qr_login.png") -> None:
    img = qrcode.make(url)
    img.save(out_path)
    print(f"QR image saved: {Path(out_path).resolve()}")


async def show_qr_and_wait_login(
    app,
    api_id: int,
    api_hash: str,
    timeout_seconds: int = 120,
    out_path: str = "qr_login.png",
    on_qr_file: Callable[[str, str], None] | None = None,
    on_event: Callable[[str], None] | None = None,
) -> bool:
    """Show a QR code and wait for the user to accept it on a primary device.

    Implementation notes (per https://core.telegram.org/api/qr-login):

    - The secondary device must run on the user's nearest DC, otherwise the
      first ``auth.exportLoginToken`` after the QR is accepted returns
      ``LoginTokenMigrateTo`` and we'd have to migrate. We therefore call
      ``help.GetNearestDc`` first and switch DCs up-front.
    - Acceptance is signalled by the server via an ``UpdateLoginToken`` update,
      not by the next ``auth.exportLoginToken`` poll. We must therefore listen
      for that update with a raw handler. To receive updates at all, the
      Pyrogram dispatcher has to be running, and ``Client.connect()`` does NOT
      start it (only ``Client.start()`` / ``Client.initialize()`` do). We start
      it here manually for the duration of the QR flow.
    - Once the update arrives, we re-export the token and the response is
      ``LoginTokenSuccess`` (or ``LoginTokenMigrateTo`` if the DC switch has
      not happened yet, in which case we follow the migrate dance with
      ``auth.importLoginToken``).
    """

    success_event = asyncio.Event()
    success_authorization: list = []  # mutable holder so the handler can write to it
    last_shown_token: bytes | None = None

    def _emit(msg: str) -> None:
        if on_event is not None:
            try:
                on_event(msg)
            except Exception:
                pass

    def _token_to_url(token_bytes: bytes) -> str:
        token_b64 = base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")
        return f"tg://login?token={token_b64}"

    async def _switch_dc(dc_id: int) -> None:
        await app.session.stop()
        await app.storage.dc_id(dc_id)
        await app.storage.auth_key(
            await Auth(app, dc_id, await app.storage.test_mode()).create()
        )
        app.session = Session(
            app,
            dc_id,
            await app.storage.auth_key(),
            await app.storage.test_mode(),
        )
        await app.session.start()

    async def _apply_authorization(auth_obj, note: str) -> bool:
        user = getattr(auth_obj, "user", None)
        if user is None:
            return False
        await app.storage.user_id(user.id)
        await app.storage.is_bot(bool(getattr(user, "bot", False)))
        await app.storage.save()
        success_authorization.append(auth_obj)
        success_event.set()
        _emit(f"QR: authorization tersimpan ({note})")
        return True

    async def _handle_token_response(token_obj, note: str) -> bool:
        if isinstance(token_obj, raw.types.auth.LoginTokenSuccess):
            return await _apply_authorization(token_obj.authorization, note)
        if isinstance(token_obj, raw.types.auth.LoginTokenMigrateTo):
            _emit(f"QR: migrate to DC {token_obj.dc_id}")
            await _switch_dc(token_obj.dc_id)
            try:
                imported = await app.invoke(
                    raw.functions.auth.ImportLoginToken(token=token_obj.token)
                )
            except Exception as exc:
                _emit(f"QR: import after migrate gagal: {type(exc).__name__}: {exc}")
                return False
            return await _handle_token_response(imported, "import setelah migrate")
        return False

    async def _export_token():
        return await app.invoke(
            raw.functions.auth.ExportLoginToken(
                api_id=api_id,
                api_hash=api_hash,
                except_ids=[],
            )
        )

    async def _refresh_qr() -> raw.base.auth.LoginToken | None:
        nonlocal last_shown_token
        try:
            r = await _export_token()
        except FloodWait as exc:
            _emit(f"QR: flood wait {exc.value}s")
            await asyncio.sleep(min(int(exc.value), 30))
            return None
        except Exception as exc:
            _emit(f"QR: export error {type(exc).__name__}: {exc}")
            return None

        # Treat the response uniformly: it can already be Success/MigrateTo if the
        # server has decided to finalize on the next call.
        finished = await _handle_token_response(r, "export")
        if finished:
            return r

        if not isinstance(r, raw.types.auth.LoginToken):
            _emit(f"QR: response tidak dikenali ({type(r).__name__})")
            return None

        if r.token != last_shown_token:
            url = _token_to_url(r.token)
            _save_qr_png(url, out_path=out_path)
            if on_qr_file is not None:
                try:
                    on_qr_file(str(Path(out_path).resolve()), url)
                except Exception:
                    pass
            _print_qr(url)
            last_shown_token = r.token
        _emit("QR: token siap, menunggu scan")
        return r

    async def _on_update(client, update, _users, _chats):
        if not isinstance(update, raw.types.UpdateLoginToken):
            return
        _emit("QR: UpdateLoginToken diterima dari server")
        try:
            r = await _export_token()
        except Exception as exc:
            _emit(f"QR: export setelah update gagal: {type(exc).__name__}: {exc}")
            return
        finished = await _handle_token_response(r, "setelah update")
        if not finished:
            _emit(
                "QR: setelah UpdateLoginToken server belum kembalikan Success "
                f"({type(r).__name__}); QR akan di-refresh."
            )

    handler = handlers.RawUpdateHandler(_on_update)
    app.add_handler(handler)

    # The dispatcher has to be running for raw handlers to fire. ``connect`` does
    # not start it (that's ``initialize``'s job), so we start it ourselves and
    # only stop it if we were the ones who started it.
    started_dispatcher = False
    try:
        if not getattr(app, "is_initialized", False):
            await app.dispatcher.start()
            started_dispatcher = True
            app.is_initialized = True
    except Exception as exc:
        _emit(f"QR: dispatcher.start gagal: {exc}")

    try:
        # Move to nearest DC up-front so we don't have to deal with migrate after
        # the user has already scanned the QR.
        try:
            nearest = await app.invoke(raw.functions.help.GetNearestDc())
            current_dc = await app.storage.dc_id()
            if nearest.nearest_dc and nearest.nearest_dc != current_dc:
                _emit(f"QR: switch ke nearest DC {nearest.nearest_dc} (dari {current_dc})")
                await _switch_dc(nearest.nearest_dc)
        except Exception as exc:
            _emit(f"QR: nearest DC lookup gagal: {type(exc).__name__}: {exc}")

        deadline = time.time() + timeout_seconds

        first = await _refresh_qr()
        if success_event.is_set():
            return True
        if first is None:
            # Try once more after a small delay before giving up.
            await asyncio.sleep(2)
            first = await _refresh_qr()
            if success_event.is_set():
                return True
            if first is None:
                _emit("QR: gagal mendapatkan token awal")
                return False

        while not success_event.is_set() and time.time() < deadline:
            # Wait until either the update fires, the QR token expires, or our
            # overall deadline lapses.
            now = int(time.time())
            expires_in = max(1, int(getattr(first, "expires", now + 30)) - now)
            wait_for = min(expires_in - 1 if expires_in > 1 else 1, max(1, int(deadline - time.time())))
            try:
                await asyncio.wait_for(success_event.wait(), timeout=wait_for)
            except asyncio.TimeoutError:
                pass

            if success_event.is_set():
                break

            # QR token likely expired — refresh and continue waiting.
            first = await _refresh_qr()
            if success_event.is_set():
                break
            if first is None:
                await asyncio.sleep(2)

        if success_event.is_set():
            return True

        # Last-chance check via storage / get_me in case the success was applied
        # but the event somehow didn't propagate.
        try:
            uid = await app.storage.user_id()
            if uid and int(uid) > 0:
                _emit("QR: authorization terdeteksi via storage (post-loop)")
                return True
        except Exception:
            pass
        try:
            me = await app.get_me()
            if me and getattr(me, "id", None):
                _emit("QR: authorization terdeteksi via get_me (post-loop)")
                return True
        except Exception:
            pass

        return False
    finally:
        try:
            app.remove_handler(handler)
        except Exception:
            pass

        if started_dispatcher:
            try:
                await app.dispatcher.stop()
            except Exception:
                pass
            try:
                app.is_initialized = False
            except Exception:
                pass
