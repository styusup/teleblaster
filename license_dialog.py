from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

import license_manager


LICENSE_PAGE_URL = "https://vibetool.id/dashboard/licenses"

COLORS = {
    "bg": "#111318",
    "panel": "#1a1f29",
    "panel_2": "#202736",
    "text": "#e8ecf1",
    "muted": "#a9b4c0",
    "accent": "#4ea1ff",
    "accent_hover": "#6ab2ff",
    "ok": "#61d095",
    "err": "#ff6b6b",
}


def ensure_licensed(root: tk.Tk) -> bool:
    """Block until a valid license is active.

    Returns True when a valid license is present (either already saved or
    activated in the dialog), or False if the user closed the dialog without
    activating.
    """
    startup = license_manager.check_license_on_startup()
    if startup.valid:
        return True

    dialog = _LicenseDialog(root, startup)
    root.wait_window(dialog.win)
    return dialog.activated


class _LicenseDialog:
    def __init__(self, root: tk.Tk, startup: license_manager.LicenseResult):
        self.root = root
        self.activated = False
        self._busy = False
        self._pending: license_manager.LicenseResult | None = None

        win = tk.Toplevel(root)
        self.win = win
        win.title("Aktivasi Lisensi - Teleblaster")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        c = COLORS
        style.configure("Lic.TFrame", background=c["bg"])
        style.configure("LicCard.TFrame", background=c["panel"])
        style.configure("Lic.TLabel", background=c["panel"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("LicTitle.TLabel", background=c["panel"], foreground=c["text"], font=("Segoe UI Semibold", 16))
        style.configure("LicMuted.TLabel", background=c["panel"], foreground=c["muted"], font=("Segoe UI", 9))
        style.configure("LicOk.TLabel", background=c["panel"], foreground=c["ok"], font=("Segoe UI", 9))
        style.configure("LicErr.TLabel", background=c["panel"], foreground=c["err"], font=("Segoe UI", 9))
        style.configure("Lic.TEntry", fieldbackground=c["panel_2"], foreground=c["text"], insertcolor=c["text"], padding=8)
        style.configure("Lic.TButton", background=c["panel_2"], foreground=c["text"], borderwidth=0, padding=(12, 7), font=("Segoe UI", 10))
        style.map("Lic.TButton", background=[("active", c["accent_hover"]), ("pressed", c["accent"])], foreground=[("active", "#0f1722")])
        style.configure("LicAccent.TButton", background=c["accent"], foreground="#0f1722", borderwidth=0, padding=(12, 7), font=("Segoe UI Semibold", 10))
        style.map("LicAccent.TButton", background=[("active", c["accent_hover"]), ("pressed", c["accent"])])

        card = ttk.Frame(win, style="LicCard.TFrame", padding=22)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        ttk.Label(card, text="Aktivasi Lisensi", style="LicTitle.TLabel").pack(anchor="w")
        ttk.Label(
            card,
            text="Masukkan kunci lisensi Teleblaster Pro dari vibetool.id untuk mulai menggunakan aplikasi.",
            style="LicMuted.TLabel",
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(4, 16))

        ttk.Label(card, text="Kunci Lisensi", style="Lic.TLabel").pack(anchor="w")
        self.key_var = tk.StringVar()
        saved = license_manager.load_saved_license()
        if saved and saved.get("key"):
            self.key_var.set(saved["key"])
        self.entry = ttk.Entry(card, textvariable=self.key_var, style="Lic.TEntry", width=42, font=("Consolas", 12))
        self.entry.pack(anchor="w", fill="x", pady=(4, 4))
        self.entry.bind("<Return>", lambda _e: self._on_activate())

        ttk.Label(card, text="Format: XXXX-XXXX-XXXX-XXXX", style="LicMuted.TLabel").pack(anchor="w")

        self.status_var = tk.StringVar(value=_friendly_startup_message(startup))
        self.status_style = "LicErr.TLabel" if startup.error and startup.error != "no_saved_license" else "LicMuted.TLabel"
        self.status_label = ttk.Label(card, textvariable=self.status_var, style=self.status_style, wraplength=420, justify="left")
        self.status_label.pack(anchor="w", pady=(12, 14))

        btn_row = ttk.Frame(card, style="LicCard.TFrame")
        btn_row.pack(anchor="w", fill="x")
        self.activate_btn = ttk.Button(btn_row, text="Aktivasi", style="LicAccent.TButton", command=self._on_activate)
        self.activate_btn.pack(side="left")
        ttk.Button(btn_row, text="Beli / Lihat Lisensi", style="Lic.TButton", command=self._open_license_page).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Keluar", style="Lic.TButton", command=self._on_close).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", self._on_close)
        win.update_idletasks()
        self._center(win)
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))
        win.grab_set()
        win.focus_force()
        self.entry.focus_set()

    def _center(self, win: tk.Toplevel) -> None:
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        win.geometry(f"+{x}+{y}")

    def _set_status(self, text: str, style: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(style=style)

    def _open_license_page(self) -> None:
        try:
            webbrowser.open(LICENSE_PAGE_URL)
        except Exception:
            self._set_status(f"Buka manual: {LICENSE_PAGE_URL}", "LicMuted.TLabel")

    def _on_activate(self) -> None:
        if self._busy:
            return
        key = license_manager.normalize_key(self.key_var.get())
        if not key:
            self._set_status("Kunci lisensi tidak boleh kosong.", "LicErr.TLabel")
            return

        self._busy = True
        self._pending = None
        self.activate_btn.configure(state="disabled")
        self._set_status("Memvalidasi lisensi...", "LicMuted.TLabel")

        def _worker():
            self._pending = license_manager.activate_license(key)

        threading.Thread(target=_worker, daemon=True).start()
        self.win.after(120, self._poll_result)

    def _poll_result(self) -> None:
        if not self.win.winfo_exists():
            return
        if self._pending is None:
            self.win.after(120, self._poll_result)
            return
        result = self._pending
        self._pending = None
        self._on_validation_done(result)

    def _on_validation_done(self, result: license_manager.LicenseResult) -> None:
        self._busy = False
        if not self.win.winfo_exists():
            return
        self.activate_btn.configure(state="normal")

        if result.valid:
            self.activated = True
            self._set_status("Lisensi valid. Membuka aplikasi...", "LicOk.TLabel")
            summary = license_manager.format_license_summary(result.info)
            if summary:
                self._set_status(f"Lisensi valid: {summary}", "LicOk.TLabel")
            self.win.after(700, self._finish_success)
            return

        self._set_status(result.message, "LicErr.TLabel")

    def _finish_success(self) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()

    def _on_close(self) -> None:
        if self._busy:
            return
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


def _friendly_startup_message(startup: license_manager.LicenseResult) -> str:
    if startup.error == "no_saved_license":
        return "Aplikasi ini memerlukan lisensi aktif. Masukkan kunci lisensi Anda."
    if startup.error == "license_expired":
        return "Lisensi Anda sudah kedaluwarsa. Perpanjang atau masukkan kunci lisensi baru."
    if startup.error == "license_not_found":
        return "Lisensi tersimpan tidak valid lagi. Masukkan kunci lisensi yang benar."
    return startup.message
