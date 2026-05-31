from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from utils import load_json, save_json_atomic


LICENSE_API_URL = "https://vibetool.id/api/license/validate"
PRODUCT_SLUG = "teleblaster-pro-version"
LICENSE_FILE = "license.json"
REQUEST_TIMEOUT = 20


@dataclass
class LicenseResult:
    valid: bool
    message: str
    error: str | None = None
    info: dict | None = None
    offline: bool = False


def normalize_key(raw: str) -> str:
    return (raw or "").strip().upper()


def _license_path() -> Path:
    return Path(LICENSE_FILE)


def load_saved_license() -> dict | None:
    data = load_json(str(_license_path()), default=None)
    if isinstance(data, dict) and data.get("key"):
        return data
    return None


def save_license(key: str, info: dict | None) -> None:
    payload = {
        "key": normalize_key(key),
        "product_slug": PRODUCT_SLUG,
        "info": info or {},
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json_atomic(str(_license_path()), payload)


def clear_saved_license() -> None:
    p = _license_path()
    if p.exists():
        p.unlink()


def is_info_expired(info: dict | None) -> bool:
    if not info or info.get("is_lifetime"):
        return False
    expires_at = info.get("expires_at")
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= dt


def format_license_summary(info: dict | None) -> str:
    if not info:
        return ""
    parts = []
    product = info.get("product") or {}
    if product.get("title"):
        parts.append(str(product["title"]))
    if info.get("is_lifetime"):
        parts.append("Lifetime")
    elif info.get("expires_at"):
        parts.append(f"Berlaku s/d {str(info['expires_at'])[:10]}")
    user = info.get("user") or {}
    if user.get("name"):
        parts.append(f"a.n. {user['name']}")
    return " | ".join(parts)


def validate_online(key: str) -> LicenseResult:
    key = normalize_key(key)
    if not key:
        return LicenseResult(valid=False, message="Lisensi tidak boleh kosong.", error="empty_key")

    body = json.dumps({"key": key, "product_slug": PRODUCT_SLUG}).encode("utf-8")
    request = urllib.request.Request(
        LICENSE_API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return LicenseResult(
                valid=False,
                message=f"Server lisensi mengembalikan error (HTTP {exc.code}).",
                error="http_error",
            )
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        return LicenseResult(
            valid=False,
            message="Tidak bisa terhubung ke server lisensi. Periksa koneksi internet.",
            error="network_error",
            offline=True,
        )
    except ValueError:
        return LicenseResult(
            valid=False,
            message="Respons server lisensi tidak valid.",
            error="bad_response",
        )

    valid = bool(data.get("valid"))
    return LicenseResult(
        valid=valid,
        message=data.get("message", "Lisensi valid." if valid else "Lisensi tidak valid."),
        error=data.get("error"),
        info=data.get("license"),
    )


def activate_license(key: str) -> LicenseResult:
    result = validate_online(key)
    if result.valid:
        save_license(key, result.info)
    return result


def check_license_on_startup() -> LicenseResult:
    saved = load_saved_license()
    if not saved or not saved.get("key"):
        return LicenseResult(
            valid=False,
            message="Belum ada lisensi tersimpan.",
            error="no_saved_license",
        )

    result = validate_online(saved["key"])
    if result.valid:
        save_license(saved["key"], result.info)
        return result

    if result.offline:
        cached = saved.get("info") or {}
        if not is_info_expired(cached):
            return LicenseResult(
                valid=True,
                message="Mode offline: memakai lisensi tersimpan yang masih berlaku.",
                info=cached,
                offline=True,
            )
        return LicenseResult(
            valid=False,
            message="Lisensi tersimpan sudah kedaluwarsa dan server tidak dapat dihubungi.",
            error="license_expired",
            offline=True,
        )

    clear_saved_license()
    return result
