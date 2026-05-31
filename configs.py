from dataclasses import dataclass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _candidate_env_paths() -> list[Path]:
    """Return ordered list of .env locations to try.

    Order matters: later entries override earlier ones (override=True), so the
    user-editable .env next to the .exe wins over the embedded bundle copy.
    """
    paths: list[Path] = []

    # 1. PyInstaller bundle data dir (embedded by build_distribusi.bat).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / ".env")

    # 2. Folder yang berisi .exe / executable (untuk override oleh end-user
    #    tanpa rebuild). Saat dijalankan langsung dengan python, ini = folder
    #    script.
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / ".env")
    else:
        paths.append(Path(__file__).resolve().parent / ".env")

    # 3. Current working directory (default load_dotenv behavior).
    paths.append(Path.cwd() / ".env")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _load_env_from_all_known_locations() -> None:
    for path in _candidate_env_paths():
        if path.exists():
            load_dotenv(path, override=True)


_load_env_from_all_known_locations()


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    sessions_dir: str = "sessions"
    logs_dir: str = "logs"
    members_csv: str = "members.csv"
    cooldowns_file: str = "account_cooldowns.json"
    checkpoint_file: str = "scrape_checkpoint.json"
    template_file: str = "message_template.md"

    @classmethod
    def from_env(cls) -> "Config":
        # Re-attempt load (idempotent) in case env file was created after import.
        _load_env_from_all_known_locations()
        api_id_raw = os.getenv("API_ID", "").strip()
        api_hash = os.getenv("API_HASH", "").strip()
        if not api_id_raw or not api_hash:
            raise ValueError("API_ID and API_HASH must be set in .env")
        return cls(api_id=int(api_id_raw), api_hash=api_hash)
