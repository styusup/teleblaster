import asyncio
from pathlib import Path

from rich.prompt import Prompt

from account_manager import AccountManager
from configs import Config
from funcs.options_handlers.about import show_about
from funcs.options_handlers.add_members import handle_add_members
from funcs.options_handlers.broadcast_message import handle_broadcast
from funcs.options_handlers.login import handle_login
from funcs.options_handlers.manage_sessions import handle_manage_sessions
from funcs.options_handlers.scrape_members import handle_scrape
from funcs.ui import error, info, show_header, show_main_menu, success, warn
import license_manager
from logger import setup_logger
from utils import ensure_paths, normalize_menu_choice


def require_license_cli() -> bool:
    startup = license_manager.check_license_on_startup()
    if startup.valid:
        summary = license_manager.format_license_summary(startup.info)
        success("Lisensi aktif" + (f": {summary}" if summary else ""))
        return True

    info("Aplikasi ini memerlukan lisensi aktif dari vibetool.id")
    if startup.error not in (None, "no_saved_license"):
        warn(startup.message)

    while True:
        raw = Prompt.ask("Masukkan kunci lisensi (atau ketik 'q' untuk keluar)").strip()
        if raw.lower() in {"q", "quit", "exit", ""}:
            return False

        result = license_manager.activate_license(raw)
        if result.valid:
            summary = license_manager.format_license_summary(result.info)
            success("Lisensi valid" + (f": {summary}" if summary else ""))
            return True

        error(result.message)


async def app_main() -> None:
    if not require_license_cli():
        info("Lisensi belum aktif. Keluar.")
        return

    try:
        config = Config.from_env()
    except Exception as exc:
        error(str(exc))
        info("Buat file .env dengan API_ID dan API_HASH")
        return

    ensure_paths(config.sessions_dir, config.logs_dir)
    logger = setup_logger(config.logs_dir)
    manager = AccountManager(config)

    while True:
        show_header()
        info(f"{len(manager.list_sessions())} sessions loaded")
        show_main_menu()
        choice = normalize_menu_choice(Prompt.ask("Choose", default="00"))

        try:
            if choice == "01":
                await handle_login(config)
            elif choice == "02":
                await handle_scrape(config, manager)
            elif choice == "03":
                await handle_add_members(config, manager)
            elif choice == "04":
                await handle_broadcast(config, manager)
            elif choice == "05":
                await handle_manage_sessions(config, manager)
            elif choice == "99":
                show_about()
            elif choice == "00":
                break
            else:
                error("Invalid option")
        except KeyboardInterrupt:
            info("Interrupted")
        except Exception as exc:
            logger.exception("Unhandled error")
            error(f"Unhandled error: {exc}")


if __name__ == "__main__":
    asyncio.run(app_main())
