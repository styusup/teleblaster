from __future__ import annotations

import asyncio
import html
import os
import re
import threading
from pathlib import Path
import random
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pyrogram_compat  # noqa: F401
from PIL import Image, ImageTk
from pyrogram import Client
from pyrogram import raw
from pyrogram.enums import ChatMembersFilter, ChatType, MessageEntityType, ParseMode
from pyrogram.errors import FloodWait, PeerFlood, PeerIdInvalid, SessionPasswordNeeded
from pyrogram.types import InputMediaDocument, InputMediaPhoto, InputMediaVideo

from account_manager import AccountManager
from configs import Config
from funcs.helpers import execute_with_rotation, load_checkpoint, resolve_target_chat, save_checkpoint, save_session_string
from license_dialog import ensure_licensed
from funcs.qr_auth import show_qr_and_wait_login
from utils import (
    append_members_dedup,
    ensure_paths,
    infer_gender,
    mask_phone,
    normalize_chat_target,
    per_group_members_path,
    random_delay,
    read_members_csv,
    write_members_csv_atomic,
)


class TelegramScraperGUI:
    AUTO_ACCOUNT_LABEL = "Auto (rotasi semua akun)"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Telegram Blaster By VibeTool.Club")
        self.root.geometry("1080x800")
        self.root.minsize(1000, 740)

        try:
            # Improve default sizing on high-DPI displays.
            self.root.tk.call("tk", "scaling", 1.15)
        except Exception:
            pass

        self.themes = {
            "dark": {
                "bg": "#0b0f17",
                "panel": "#141a26",
                "panel_2": "#1d2533",
                "panel_3": "#252e3f",
                "text": "#ecf1f8",
                "muted": "#94a3b8",
                "accent": "#5ea0ff",
                "accent_hover": "#7cb3ff",
                "accent_press": "#3d8aff",
                "border": "#2a3447",
                "border_strong": "#3a465d",
                "ok": "#34d399",
                "ok_hover": "#4ade8b",
                "danger": "#ef5d6f",
                "danger_hover": "#f57787",
                "warn": "#f5b454",
            },
            "light": {
                "bg": "#f5f7fb",
                "panel": "#ffffff",
                "panel_2": "#eef2f9",
                "panel_3": "#e1e8f4",
                "text": "#0f172a",
                "muted": "#475569",
                "accent": "#2563eb",
                "accent_hover": "#3b82f6",
                "accent_press": "#1d4ed8",
                "border": "#cbd5e1",
                "border_strong": "#94a3b8",
                "ok": "#16a34a",
                "ok_hover": "#22c55e",
                "danger": "#dc2626",
                "danger_hover": "#ef4444",
                "warn": "#d97706",
            },
        }
        self.theme_mode = tk.StringVar(value="dark")
        self.colors = self.themes["dark"]
        self._tab_canvases: list[tk.Canvas] = []

        # Initialize optional windows/widgets before theme refresh touches them.
        self.broadcast_log_window = None
        self.broadcast_log_window_text = None
        self.qr_window = None
        self.qr_image_label = None
        self.qr_info_var = None
        self.qr_photo_ref = None

        self._setup_theme()

        self.config = Config.from_env()
        ensure_paths(self.config.sessions_dir, self.config.logs_dir)
        self.manager = AccountManager(self.config)

        self.login_state: dict | None = None
        self.auth_busy = False
        # Cross-thread handshake for OTP login: the worker thread sends the OTP and waits
        # on this event for the user to click "Complete Login" with the code/2FA filled in.
        self._otp_complete_event: threading.Event | None = None
        self._otp_complete_data: dict | None = None
        self._otp_ready_for_completion = False
        self.group_candidates: list[dict] = []
        self.scrape_phone_hint: str | None = None
        self.scrape_strict_account: bool = False
        self.broadcast_rows: list[dict] = []
        self.broadcast_filtered_indices: list[int] = []
        self.broadcast_picked_rows: list[dict] = []
        self.broadcast_attachments: list[str] = []
        self.broadcast_log_lines: list[str] = []

        # Branding logo (cached PhotoImage instances keyed by pixel size).
        self._logo_path = Path(__file__).resolve().parent / "assets" / "vibetool_logo.png"
        self._logo_source: Image.Image | None = None
        self._logo_cache: dict[int, ImageTk.PhotoImage] = {}
        self._apply_window_icon()

        self.grup_scrapper_results: list[dict] = []
        self._grup_scrapper_index_by_iid: dict[str, dict] = {}

        # State untuk auto-refresh status cooldown di tab Sessions.
        # Map phone → remaining-seconds saat tick terakhir; dipakai
        # untuk mendeteksi transisi cooldown→Active dan log notifikasi.
        self._prev_cooldown_state: dict[str, int] = {}
        self._sessions_tick_after_id: str | None = None

        self._build_ui()
        self._refresh_sessions_view()
        # Kick off periodic cooldown countdown tick. Setelah ini
        # tab Sessions auto-refresh tiap 5 detik tanpa user harus
        # klik Refresh — dan saat ada akun yang cooldown-nya selesai,
        # event ditulis ke Activity Log.
        self._tick_sessions_cooldowns()

    def _load_logo(self, size: int) -> ImageTk.PhotoImage | None:
        if size in self._logo_cache:
            return self._logo_cache[size]
        try:
            if self._logo_source is None:
                if not self._logo_path.exists():
                    return None
                self._logo_source = Image.open(self._logo_path).convert("RGBA")
            resized = self._logo_source.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(resized)
        except Exception:
            return None
        self._logo_cache[size] = photo
        return photo

    def _apply_window_icon(self) -> None:
        photo = self._load_logo(64)
        if photo is None:
            return
        try:
            self.root.iconphoto(True, photo)
        except Exception:
            pass

    def _setup_theme(self) -> None:
        self.colors = self.themes.get(self.theme_mode.get(), self.themes["dark"])
        c = self.colors
        self.root.configure(bg=c["bg"])

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        ui_font = ("Segoe UI", 10)
        ui_font_med = ("Segoe UI Semibold", 10)
        header_font = ("Segoe UI Semibold", 18)
        small_font = ("Segoe UI", 9)
        on_accent = "#0a1220" if self.theme_mode.get() == "dark" else "#ffffff"

        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["panel"], relief="flat")
        style.configure("Toolbar.TFrame", background=c["panel"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"], font=ui_font)
        style.configure("Card.TLabel", background=c["panel"], foreground=c["text"], font=ui_font)
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=small_font)
        style.configure("CardMuted.TLabel", background=c["panel"], foreground=c["muted"], font=small_font)
        style.configure("Header.TLabel", background=c["bg"], foreground=c["text"], font=header_font)
        style.configure("SubHeader.TLabel", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 11))
        style.configure("Status.TLabel", background=c["bg"], foreground=c["ok"], font=ui_font_med)

        # Default (subtle) button: panel-toned with hover lift to accent
        style.configure(
            "TButton",
            background=c["panel_2"],
            foreground=c["text"],
            borderwidth=0,
            focusthickness=0,
            padding=(14, 8),
            font=ui_font,
        )
        style.map(
            "TButton",
            background=[("active", c["panel_3"]), ("pressed", c["panel_3"]), ("disabled", c["panel_2"])],
            foreground=[("disabled", c["muted"])],
        )

        # Accent (primary) button
        style.configure(
            "Accent.TButton",
            background=c["accent"],
            foreground=on_accent,
            borderwidth=0,
            focusthickness=0,
            padding=(14, 8),
            font=ui_font_med,
        )
        style.map(
            "Accent.TButton",
            background=[("active", c["accent_hover"]), ("pressed", c["accent_press"])],
            foreground=[("disabled", c["muted"])],
        )

        # Success (positive) button
        style.configure(
            "Success.TButton",
            background=c["ok"],
            foreground=on_accent,
            borderwidth=0,
            focusthickness=0,
            padding=(14, 8),
            font=ui_font_med,
        )
        style.map(
            "Success.TButton",
            background=[("active", c["ok_hover"]), ("pressed", c["ok"])],
            foreground=[("disabled", c["muted"])],
        )

        # Danger (destructive) button
        style.configure(
            "Danger.TButton",
            background=c["panel_2"],
            foreground=c["danger"],
            borderwidth=0,
            focusthickness=0,
            padding=(14, 8),
            font=ui_font_med,
        )
        style.map(
            "Danger.TButton",
            background=[("active", c["danger"]), ("pressed", c["danger_hover"])],
            foreground=[("active", on_accent), ("pressed", on_accent), ("disabled", c["muted"])],
        )

        # Ghost / link-style button
        style.configure(
            "Link.TButton",
            background=c["bg"],
            foreground=c["accent"],
            borderwidth=0,
            focusthickness=0,
            padding=(8, 6),
            font=ui_font,
        )
        style.map(
            "Link.TButton",
            background=[("active", c["panel"]), ("pressed", c["panel"])],
            foreground=[("active", c["accent_hover"])],
        )

        style.configure(
            "TEntry",
            fieldbackground=c["panel_2"],
            foreground=c["text"],
            insertcolor=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            padding=8,
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", c["accent"])],
            lightcolor=[("focus", c["accent"])],
            darkcolor=[("focus", c["accent"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=c["panel_2"],
            background=c["panel_2"],
            foreground=c["text"],
            bordercolor=c["border"],
            arrowcolor=c["text"],
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["panel_2"])],
            foreground=[("readonly", c["text"])],
            selectbackground=[("readonly", c["accent"])],
            selectforeground=[("readonly", on_accent)],
            bordercolor=[("focus", c["accent"])],
        )

        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"], font=ui_font, focuscolor=c["bg"])
        style.map("TCheckbutton", background=[("active", c["bg"])], foreground=[("active", c["text"])])
        style.configure("TRadiobutton", background=c["bg"], foreground=c["text"], font=ui_font, focuscolor=c["bg"])
        style.map("TRadiobutton", background=[("active", c["bg"])], foreground=[("active", c["text"])])
        style.configure("TSeparator", background=c["border"])
        style.configure(
            "TProgressbar",
            background=c["accent"],
            troughcolor=c["panel_2"],
            bordercolor=c["border"],
            lightcolor=c["accent"],
            darkcolor=c["accent"],
            thickness=8,
        )

        style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"], relief="solid", padding=10)
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"], font=ui_font_med)
        style.configure("Card.TLabelframe", background=c["panel"], bordercolor=c["border"], relief="solid", padding=12)
        style.configure("Card.TLabelframe.Label", background=c["panel"], foreground=c["accent"], font=ui_font_med)

        style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(8, 6, 8, 0))
        style.configure(
            "TNotebook.Tab",
            background=c["bg"],
            foreground=c["muted"],
            padding=(18, 10),
            font=ui_font_med,
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["panel"]), ("active", c["panel_2"])],
            foreground=[("selected", c["accent"]), ("active", c["text"])],
            expand=[("selected", (0, 0, 0, 0))],
        )

        style.configure("Vertical.TScrollbar", background=c["panel_2"], troughcolor=c["bg"], bordercolor=c["bg"], arrowcolor=c["muted"], gripcount=0)
        style.map("Vertical.TScrollbar", background=[("active", c["panel_3"])])
        style.configure("Horizontal.TScrollbar", background=c["panel_2"], troughcolor=c["bg"], bordercolor=c["bg"], arrowcolor=c["muted"], gripcount=0)
        style.map("Horizontal.TScrollbar", background=[("active", c["panel_3"])])

        style.configure(
            "Treeview",
            background=c["panel_2"],
            fieldbackground=c["panel_2"],
            foreground=c["text"],
            bordercolor=c["border"],
            borderwidth=0,
            rowheight=24,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=c["panel"],
            foreground=c["text"],
            font=("Segoe UI Semibold", 10),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", c["accent"])],
            foreground=[("selected", "#0f1722")],
        )
        style.map(
            "Treeview.Heading",
            background=[("active", c["panel_2"])],
        )

        self._refresh_manual_widget_theme()

    def _refresh_manual_widget_theme(self) -> None:
        for cv in getattr(self, "_tab_canvases", []):
            try:
                cv.configure(bg=self.colors["bg"], highlightbackground=self.colors["border"])
            except Exception:
                pass

        for name in [
            "log_box",
            "group_listbox",
            "broadcast_text",
            "broadcast_links",
            "broadcast_manual_targets",
            "broadcast_attachment_box",
            "broadcast_listbox",
            "broadcast_picked_listbox",
            "broadcast_log_box",
            "sessions_box",
            "broadcast_log_window_text",
        ]:
            w = getattr(self, name, None)
            if w is None:
                continue

            try:
                if isinstance(w, tk.Text):
                    self._style_text_widget(w, font=("Consolas", 10))
                elif isinstance(w, tk.Listbox):
                    self._style_listbox_widget(w, font=("Segoe UI", 10))
            except Exception:
                pass

        if getattr(self, "broadcast_log_window", None) is not None:
            try:
                self.broadcast_log_window.configure(bg=self.colors["bg"])
            except Exception:
                pass

    def _toggle_theme(self) -> None:
        current = self.theme_mode.get()
        self.theme_mode.set("light" if current == "dark" else "dark")
        self._setup_theme()
        if hasattr(self, "theme_button_var"):
            next_label = "Switch to Light" if self.theme_mode.get() == "dark" else "Switch to Dark"
            self.theme_button_var.set(next_label)
        self._log(f"Theme switched to {self.theme_mode.get()}")

    def _style_text_widget(self, widget: tk.Text, *, font: tuple[str, int] = ("Consolas", 10)) -> None:
        c = self.colors
        on_accent = "#0a1220" if self.theme_mode.get() == "dark" else "#ffffff"
        widget.configure(
            bg=c["panel_2"],
            fg=c["text"],
            insertbackground=c["text"],
            selectbackground=c["accent"],
            selectforeground=on_accent,
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
            borderwidth=0,
            relief=tk.FLAT,
            padx=10,
            pady=8,
            font=font,
        )

    def _style_listbox_widget(self, widget: tk.Listbox, *, font: tuple[str, int] = ("Segoe UI", 10)) -> None:
        c = self.colors
        on_accent = "#0a1220" if self.theme_mode.get() == "dark" else "#ffffff"
        widget.configure(
            bg=c["panel_2"],
            fg=c["text"],
            selectbackground=c["accent"],
            selectforeground=on_accent,
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
            relief=tk.FLAT,
            font=font,
            bd=0,
            activestyle="none",
        )

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)

        header_row = ttk.Frame(frame)
        header_row.pack(fill=tk.X)
        header_left = ttk.Frame(header_row)
        header_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        header_logo = self._load_logo(56)
        if header_logo is not None:
            self._header_logo_ref = header_logo  # keep reference alive
            ttk.Label(header_left, image=header_logo).pack(side=tk.LEFT, padx=(0, 14))

        header_text = ttk.Frame(header_left)
        header_text.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(
            header_text,
            text="Telegram Blaster",
            style="Header.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header_text,
            text="By VibeTool.Club  ·  Multi-account members scraping, adding & broadcasting  ·  v0.1",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        status_box = ttk.Frame(header_row)
        status_box.pack(side=tk.RIGHT, anchor="e")
        self.status_var = tk.StringVar(value="● Ready")
        ttk.Label(status_box, textvariable=self.status_var, style="Status.TLabel").pack(anchor="e")

        ttk.Separator(frame, orient="horizontal").pack(fill=tk.X, pady=(12, 12))

        notebook = ttk.Notebook(frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        login_tab, self.tab_login = self._create_scrollable_tab(notebook)
        scrape_tab, self.tab_scrape = self._create_scrollable_tab(notebook)
        grup_scrapper_tab, self.tab_grup_scrapper = self._create_scrollable_tab(notebook)
        add_tab, self.tab_add = self._create_scrollable_tab(notebook)
        broadcast_tab, self.tab_broadcast = self._create_scrollable_tab(notebook)
        sessions_tab, self.tab_sessions = self._create_scrollable_tab(notebook)
        account_manager_tab, self.tab_account_manager = self._create_scrollable_tab(notebook)
        about_tab, self.tab_about = self._create_scrollable_tab(notebook)

        notebook.add(login_tab, text="Login")
        notebook.add(scrape_tab, text="Members Scraper")
        notebook.add(grup_scrapper_tab, text="Grup Scrapper")
        notebook.add(add_tab, text="Members Adder")
        notebook.add(broadcast_tab, text="Broadcast")
        notebook.add(sessions_tab, text="Sessions")
        notebook.add(account_manager_tab, text="Account Manager")
        notebook.add(about_tab, text="About")

        self._build_login_tab()
        self._build_scrape_tab()
        self._build_grup_scrapper_tab()
        self._build_add_tab()
        self._build_broadcast_tab()
        self._build_sessions_tab()
        self._build_account_manager_tab()
        self._build_about_tab()
        self._reload_broadcast_members()

        log_wrap = ttk.LabelFrame(frame, text="Activity Log", padding=8)
        log_wrap.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.log_box = tk.Text(log_wrap, height=12, wrap=tk.WORD, font=("Consolas", 10))
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self._style_text_widget(self.log_box)

    def _create_scrollable_tab(self, notebook: ttk.Notebook) -> tuple[ttk.Frame, ttk.Frame]:
        container = ttk.Frame(notebook)
        canvas = tk.Canvas(
            container,
            bg=self.colors["bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            bd=0,
            relief=tk.FLAT,
        )
        v_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=v_scroll.set)

        content = ttk.Frame(canvas, padding=10)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_content_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfigure(window_id, width=event.width)

        def _on_mousewheel(event):
            delta = event.delta
            if delta == 0:
                return
            canvas.yview_scroll(int(-delta / 120), "units")

        def _on_enter(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _on_leave(_event):
            canvas.unbind_all("<MouseWheel>")

        content.bind("<Configure>", _on_content_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind("<Enter>", _on_enter)
        canvas.bind("<Leave>", _on_leave)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._tab_canvases.append(canvas)
        return container, content

    def _build_login_tab(self) -> None:
        frm = self.tab_login

        ttk.Label(frm, text="Phone Login", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Phone (+62...)").grid(row=1, column=0, sticky="w")
        self.login_phone = ttk.Entry(frm, width=32)
        self.login_phone.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Encryption Password").grid(row=2, column=0, sticky="w")
        self.login_enc_password = ttk.Entry(frm, show="*", width=32)
        self.login_enc_password.grid(row=2, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Send OTP", style="Accent.TButton", command=self._send_otp).grid(row=3, column=1, sticky="w", padx=8, pady=8)

        ttk.Label(frm, text="OTP Code").grid(row=4, column=0, sticky="w")
        self.login_otp = ttk.Entry(frm, width=32)
        self.login_otp.grid(row=4, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="2FA Password (optional)").grid(row=5, column=0, sticky="w")
        self.login_2fa = ttk.Entry(frm, show="*", width=32)
        self.login_2fa.grid(row=5, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Complete Login", style="Accent.TButton", command=self._complete_otp_login).grid(
            row=6, column=1, sticky="w", padx=8, pady=8
        )

        ttk.Separator(frm, orient=tk.HORIZONTAL).grid(row=7, column=0, columnspan=4, sticky="ew", pady=8)

        ttk.Label(frm, text="QR Login", font=("Segoe UI", 11, "bold")).grid(row=8, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Session Label Phone (+62...)").grid(row=9, column=0, sticky="w")
        self.qr_phone_label = ttk.Entry(frm, width=32)
        self.qr_phone_label.grid(row=9, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Encryption Password").grid(row=10, column=0, sticky="w")
        self.qr_enc_password = ttk.Entry(frm, show="*", width=32)
        self.qr_enc_password.grid(row=10, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Start QR Login", style="Accent.TButton", command=self._start_qr_login).grid(row=11, column=1, sticky="w", padx=8, pady=8)

        ttk.Label(
            frm,
            text="QR akan disimpan di file qr_login.png pada folder project.",
            foreground="#666",
        ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_scrape_tab(self) -> None:
        frm = self.tab_scrape

        ttk.Label(frm, text="Scrape Members", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Akun").grid(row=1, column=0, sticky="w")
        self.scrape_account = ttk.Combobox(frm, width=36, state="readonly", values=[self.AUTO_ACCOUNT_LABEL])
        self.scrape_account.set(self.AUTO_ACCOUNT_LABEL)
        self.scrape_account.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Button(frm, text="Refresh Akun", command=self._refresh_account_pickers).grid(
            row=1, column=2, sticky="w", padx=6
        )

        ttk.Label(frm, text="Mode").grid(row=2, column=0, sticky="w")
        self.scrape_mode = ttk.Combobox(
            frm,
            width=28,
            state="readonly",
            values=["Visible Members", "Hidden Members", "Visible + Hidden"],
        )
        self.scrape_mode.set("Visible Members")
        self.scrape_mode.grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Encryption Password").grid(row=3, column=0, sticky="w")
        self.scrape_password = ttk.Entry(frm, show="*", width=32)
        self.scrape_password.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Group username/link").grid(row=4, column=0, sticky="w")
        self.scrape_target = ttk.Entry(frm, width=48)
        self.scrape_target.grid(row=4, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Run Scrape", style="Accent.TButton", command=self._run_scrape).grid(row=5, column=1, sticky="w", padx=8, pady=8)

        ttk.Button(frm, text="Load My Joined Groups", command=self._load_joined_groups).grid(
            row=5, column=2, sticky="w", padx=6, pady=8
        )

        # Progress bar + label realtime saat scrape berjalan.
        # Label dan bar dipisah ke baris berbeda agar tidak saling menumpuk.
        self.scrape_progress_label = ttk.Label(frm, text="Idle.", style="Muted.TLabel")
        self.scrape_progress_label.grid(
            row=6, column=0, columnspan=3, sticky="w", padx=0, pady=(6, 0)
        )
        self.scrape_progress = ttk.Progressbar(
            frm, mode="determinate", length=400, maximum=100
        )
        self.scrape_progress.grid(
            row=7, column=0, columnspan=3, sticky="ew", padx=0, pady=(2, 8)
        )

        self.group_listbox = tk.Listbox(frm, height=9, width=78)
        self.group_listbox.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._style_listbox_widget(self.group_listbox)

        ttk.Button(frm, text="Use Selected Group", command=self._use_selected_group).grid(
            row=9, column=0, sticky="w", pady=6
        )

        ttk.Label(
            frm,
            text="Hasil disimpan ke members.csv",
            foreground="#666",
        ).grid(row=10, column=0, columnspan=3, sticky="w")

    def _build_add_tab(self) -> None:
        frm = self.tab_add

        ttk.Label(frm, text="Add Members", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Akun").grid(row=1, column=0, sticky="w")
        self.add_account = ttk.Combobox(frm, width=36, state="readonly", values=[self.AUTO_ACCOUNT_LABEL])
        self.add_account.set(self.AUTO_ACCOUNT_LABEL)
        self.add_account.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Button(frm, text="Refresh Akun", command=self._refresh_account_pickers).grid(
            row=1, column=2, sticky="w", padx=6
        )

        ttk.Label(frm, text="Mode").grid(row=2, column=0, sticky="w")
        self.add_mode = ttk.Combobox(frm, width=28, state="readonly", values=["Rush", "Calm"])
        self.add_mode.set("Rush")
        self.add_mode.grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Encryption Password").grid(row=3, column=0, sticky="w")
        self.add_password = ttk.Entry(frm, show="*", width=32)
        self.add_password.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Target group username/link").grid(row=4, column=0, sticky="w")
        self.add_target = ttk.Entry(frm, width=48)
        self.add_target.grid(row=4, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Run Adder", style="Accent.TButton", command=self._run_adder).grid(row=5, column=1, sticky="w", padx=8, pady=8)

    def _build_grup_scrapper_tab(self) -> None:
        frm = self.tab_grup_scrapper

        frm.grid_columnconfigure(1, weight=1)
        frm.grid_columnconfigure(2, weight=0)
        frm.grid_columnconfigure(3, weight=0)

        ttk.Label(frm, text="Grup Scrapper", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            frm,
            text="Cari grup/channel publik berdasarkan keyword niche, lalu join sekaligus.",
            style="Muted.TLabel",
        ).grid(row=0, column=1, columnspan=3, sticky="w", padx=8, pady=(0, 4))

        ttk.Label(frm, text="Akun").grid(row=1, column=0, sticky="w")
        self.grup_scrapper_account = ttk.Combobox(
            frm, width=36, state="readonly", values=[self.AUTO_ACCOUNT_LABEL]
        )
        self.grup_scrapper_account.set(self.AUTO_ACCOUNT_LABEL)
        self.grup_scrapper_account.grid(row=1, column=1, sticky="w", padx=8, pady=2)
        ttk.Button(
            frm, text="Refresh Akun", command=self._refresh_account_pickers
        ).grid(row=1, column=2, sticky="w", padx=6)

        ttk.Label(frm, text="Keyword niche").grid(row=2, column=0, sticky="w")
        self.grup_scrapper_query = ttk.Entry(frm, width=48)
        self.grup_scrapper_query.grid(row=2, column=1, columnspan=3, sticky="ew", padx=8, pady=2)
        self.grup_scrapper_query.bind("<Return>", lambda _e: self._run_grup_scrapper_search())

        ttk.Label(frm, text="Encryption Password").grid(row=3, column=0, sticky="w")
        self.grup_scrapper_password = ttk.Entry(frm, show="*", width=32)
        self.grup_scrapper_password.grid(row=3, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(frm, text="Tipe").grid(row=4, column=0, sticky="w")
        self.grup_scrapper_type = ttk.Combobox(
            frm,
            width=28,
            state="readonly",
            values=["Semua (Group + Channel)", "Group/Supergroup saja", "Channel saja"],
        )
        self.grup_scrapper_type.set("Semua (Group + Channel)")
        self.grup_scrapper_type.grid(row=4, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(frm, text="Limit hasil").grid(row=4, column=2, sticky="e", padx=(8, 4))
        self.grup_scrapper_limit = ttk.Entry(frm, width=8)
        self.grup_scrapper_limit.insert(0, "50")
        self.grup_scrapper_limit.grid(row=4, column=3, sticky="w")

        ttk.Label(frm, text="Delay join random (sec)").grid(row=5, column=0, sticky="w")
        delay_wrap = ttk.Frame(frm)
        delay_wrap.grid(row=5, column=1, sticky="w", padx=8, pady=2)
        self.grup_scrapper_delay_min = ttk.Entry(delay_wrap, width=5)
        self.grup_scrapper_delay_min.insert(0, "5")
        self.grup_scrapper_delay_min.pack(side=tk.LEFT)
        ttk.Label(delay_wrap, text="to").pack(side=tk.LEFT, padx=4)
        self.grup_scrapper_delay_max = ttk.Entry(delay_wrap, width=5)
        self.grup_scrapper_delay_max.insert(0, "15")
        self.grup_scrapper_delay_max.pack(side=tk.LEFT)

        self.grup_scrapper_skip_scam = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm,
            text="Skip grup/channel berlabel scam/fake saat Join",
            variable=self.grup_scrapper_skip_scam,
        ).grid(row=5, column=2, columnspan=2, sticky="w", padx=4)

        actions = ttk.Frame(frm)
        actions.grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 4))
        ttk.Button(
            actions, text="Cari Grup", style="Accent.TButton", command=self._run_grup_scrapper_search
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="Fetch Member Counts",
            command=self._fetch_grup_scrapper_stats,
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            actions,
            text="Join Selected",
            style="Accent.TButton",
            command=lambda: self._run_grup_scrapper_join(only_selected=True),
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            actions,
            text="Join All",
            command=lambda: self._run_grup_scrapper_join(only_selected=False),
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Export CSV", command=self._export_grup_scrapper_csv).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(actions, text="Clear", command=self._clear_grup_scrapper_results).pack(
            side=tk.LEFT, padx=6
        )

        tree_wrap = ttk.Frame(frm)
        tree_wrap.grid(row=7, column=0, columnspan=4, sticky="nsew", pady=(6, 4))
        frm.grid_rowconfigure(7, weight=1)

        columns = ("title", "type", "username", "members", "status")
        self.grup_scrapper_tree = ttk.Treeview(
            tree_wrap,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=14,
        )
        self.grup_scrapper_tree.heading("title", text="Judul")
        self.grup_scrapper_tree.heading("type", text="Tipe")
        self.grup_scrapper_tree.heading("username", text="Username/Link")
        self.grup_scrapper_tree.heading("members", text="Members")
        self.grup_scrapper_tree.heading("status", text="Status")
        self.grup_scrapper_tree.column("title", width=340, anchor="w")
        self.grup_scrapper_tree.column("type", width=110, anchor="center")
        self.grup_scrapper_tree.column("username", width=200, anchor="w")
        self.grup_scrapper_tree.column("members", width=90, anchor="e")
        self.grup_scrapper_tree.column("status", width=140, anchor="center")
        self.grup_scrapper_tree.bind("<Double-Button-1>", self._on_grup_scrapper_row_double_click)

        v_scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.grup_scrapper_tree.yview)
        self.grup_scrapper_tree.configure(yscrollcommand=v_scroll.set)
        self.grup_scrapper_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.grup_scrapper_stats_var = tk.StringVar(value="Hasil: 0")
        ttk.Label(
            frm,
            textvariable=self.grup_scrapper_stats_var,
            style="Muted.TLabel",
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(2, 0))

        ttk.Label(
            frm,
            text=(
                "Tip: Telegram membatasi hasil global search ~10–50 per query, jadi pakai keyword spesifik. "
                "Pilih akun spesifik di dropdown jika ingin Join hanya pakai 1 akun (tidak dirotasi). "
                "Double-click baris untuk salin link ke clipboard."
            ),
            style="Muted.TLabel",
            wraplength=900,
            justify=tk.LEFT,
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(2, 0))

    def _build_broadcast_tab(self) -> None:
        frm = self.tab_broadcast

        frm.grid_columnconfigure(0, minsize=200)
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_columnconfigure(2, minsize=170)
        frm.grid_columnconfigure(3, minsize=150)

        ttk.Label(frm, text="Broadcast Message", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Akun").grid(row=1, column=0, sticky="w")
        self.broadcast_account = ttk.Combobox(frm, width=36, state="readonly", values=[self.AUTO_ACCOUNT_LABEL])
        self.broadcast_account.set(self.AUTO_ACCOUNT_LABEL)
        self.broadcast_account.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Button(frm, text="Refresh Akun", command=self._refresh_account_pickers).grid(
            row=1, column=2, sticky="w", padx=6
        )

        ttk.Label(frm, text="Encryption Password").grid(row=2, column=0, sticky="w")
        self.broadcast_password = ttk.Entry(frm, show="*", width=32)
        self.broadcast_password.grid(row=2, column=1, sticky="ew", padx=8)

        ttk.Label(frm, text="Markdown file").grid(row=3, column=0, sticky="w")
        self.broadcast_file = ttk.Entry(frm, width=52)
        self.broadcast_file.insert(0, self.config.template_file)
        self.broadcast_file.grid(row=3, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(frm, text="Browse", command=self._browse_md).grid(row=3, column=3, padx=6, sticky="ew")

        ttk.Label(frm, text="Broadcast text (langsung)").grid(row=4, column=0, sticky="w")
        self.broadcast_text = tk.Text(frm, height=4, width=70, wrap=tk.WORD)
        self.broadcast_text.grid(row=4, column=1, columnspan=3, sticky="ew", padx=8)
        self._style_text_widget(self.broadcast_text, font=("Segoe UI", 10))

        ttk.Label(frm, text="Links (opsional, satu per baris)").grid(row=5, column=0, sticky="w")
        self.broadcast_links = tk.Text(frm, height=3, width=70, wrap=tk.WORD)
        self.broadcast_links.grid(row=5, column=1, columnspan=3, sticky="ew", padx=8)
        self._style_text_widget(self.broadcast_links, font=("Segoe UI", 10))

        ttk.Label(frm, text="Attachments (image/video/document)").grid(row=6, column=0, sticky="w")
        self.broadcast_attachment_box = tk.Listbox(frm, height=4, width=70)
        self.broadcast_attachment_box.grid(row=6, column=1, columnspan=2, sticky="ew", padx=8)
        self._style_listbox_widget(self.broadcast_attachment_box)
        attach_btns = ttk.Frame(frm)
        attach_btns.grid(row=6, column=3, sticky="nsew", padx=(0, 4))
        ttk.Button(attach_btns, text="Add Files", command=self._add_broadcast_attachments).pack(fill=tk.X)
        ttk.Button(attach_btns, text="Remove Selected", command=self._remove_selected_broadcast_attachment).pack(fill=tk.X, pady=4)
        ttk.Button(attach_btns, text="Clear Files", command=self._clear_broadcast_attachments).pack(fill=tk.X)
        ttk.Separator(attach_btns, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Label(attach_btns, text="Delay random (sec)").pack(anchor="w")
        delay_wrap = ttk.Frame(attach_btns)
        delay_wrap.pack(anchor="w", pady=(2, 0))
        self.broadcast_delay_min = ttk.Entry(delay_wrap, width=5)
        self.broadcast_delay_min.insert(0, "5")
        self.broadcast_delay_min.pack(side=tk.LEFT)
        ttk.Label(delay_wrap, text="to").pack(side=tk.LEFT, padx=4)
        self.broadcast_delay_max = ttk.Entry(delay_wrap, width=5)
        self.broadcast_delay_max.insert(0, "20")
        self.broadcast_delay_max.pack(side=tk.LEFT)

        # Frame untuk stack 2 checkbox di col 0 row 7 tanpa mengubah
        # row layout di bawahnya (Search/Manual targets/Listbox dll).
        opts_frame = ttk.Frame(frm)
        opts_frame.grid(row=7, column=0, sticky="w")

        self.broadcast_selected_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts_frame,
            text="Broadcast only selected members",
            variable=self.broadcast_selected_only,
        ).pack(anchor="w")

        # Auto-rotate ke akun lain saat akun aktif kena PEER_FLOOD /
        # FloodWait >= 1 jam. Default off agar perilaku lama tetap
        # default (kalau akun terblok, broadcast berhenti).
        self.broadcast_auto_rotate_on_block = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame,
            text="Auto-rotate akun saat terblok (PEER_FLOOD / FW >= 1h)",
            variable=self.broadcast_auto_rotate_on_block,
        ).pack(anchor="w")

        ttk.Button(frm, text="Run Broadcast", style="Accent.TButton", command=self._run_broadcast).grid(row=7, column=1, sticky="w", padx=8, pady=8)

        ttk.Button(frm, text="Reload Scraped Members", command=self._reload_broadcast_members).grid(
            row=7, column=2, sticky="ew", padx=6, pady=8
        )

        ttk.Button(frm, text="Open Broadcast Log", command=self._open_broadcast_log_window).grid(
            row=7, column=3, sticky="ew", padx=6, pady=8
        )

        ttk.Label(frm, text="Search").grid(row=8, column=0, sticky="w")
        self.broadcast_search = ttk.Entry(frm, width=40)
        self.broadcast_search.grid(row=8, column=1, columnspan=2, sticky="ew", padx=8)
        self.broadcast_search.bind("<KeyRelease>", self._on_broadcast_search_changed)
        ttk.Button(frm, text="Clear", command=self._clear_broadcast_search).grid(row=8, column=3, sticky="ew", padx=6)

        ttk.Label(frm, text="Manual targets (opsional: username/ID/link, pisah baris atau koma)").grid(
            row=9, column=0, sticky="w", pady=(6, 0)
        )
        self.broadcast_manual_targets = tk.Text(frm, height=3, width=70, wrap=tk.WORD)
        self.broadcast_manual_targets.grid(row=9, column=1, columnspan=2, sticky="ew", padx=8, pady=(6, 0))
        self.broadcast_manual_targets.bind("<KeyRelease>", self._on_manual_targets_changed)
        self._style_text_widget(self.broadcast_manual_targets, font=("Segoe UI", 10))

        manual_btns = ttk.Frame(frm)
        manual_btns.grid(row=9, column=3, sticky="nsew", padx=6, pady=(6, 0))
        ttk.Button(manual_btns, text="Load .txt", command=self._load_manual_targets_file).pack(fill=tk.X)
        ttk.Button(manual_btns, text="Clear Targets", command=self._clear_manual_targets).pack(fill=tk.X, pady=(4, 0))

        self.broadcast_count_var = tk.StringVar(value="Contacts: 0 shown / 0 total | Selected: 0")
        ttk.Label(frm, textvariable=self.broadcast_count_var, foreground="#666").grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        self.broadcast_empty_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.broadcast_empty_var, style="Muted.TLabel").grid(
            row=10, column=3, sticky="e", pady=(6, 0)
        )

        list_wrap = ttk.Frame(frm)
        list_wrap.grid(row=11, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        list_wrap.grid_columnconfigure(0, weight=1)
        list_wrap.grid_rowconfigure(0, weight=1)

        self.broadcast_listbox = tk.Listbox(list_wrap, height=13, width=96, selectmode=tk.EXTENDED)
        self.broadcast_listbox.grid(row=0, column=0, sticky="nsew")
        self.broadcast_listbox.bind("<<ListboxSelect>>", self._on_broadcast_selection_changed)
        self.broadcast_listbox.bind("<MouseWheel>", self._on_broadcast_listbox_mousewheel)

        self.broadcast_listbox_scroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.broadcast_listbox.yview)
        self.broadcast_listbox_scroll.grid(row=0, column=1, sticky="ns")
        self.broadcast_listbox.configure(yscrollcommand=self.broadcast_listbox_scroll.set)
        self._style_listbox_widget(self.broadcast_listbox)

        action_row = ttk.Frame(frm)
        action_row.grid(row=12, column=0, columnspan=4, sticky="ew", pady=6)
        ttk.Button(action_row, text="Select All", command=self._select_all_broadcast_members).pack(side=tk.LEFT)
        ttk.Button(action_row, text="Clear Selection", command=self._clear_broadcast_selection).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            action_row,
            text="Add Selected to Recipients ▼",
            style="Accent.TButton",
            command=self._add_selected_to_picked,
        ).pack(side=tk.LEFT, padx=(20, 8))
        ttk.Button(action_row, text="Remove from Recipients", command=self._remove_picked_recipients).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Clear Recipients", command=self._clear_picked_recipients).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            action_row,
            text="Hapus Hasil Scrape",
            style="Danger.TButton",
            command=self._clear_scraped_members,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            action_row,
            text="Import CSV",
            command=self._import_broadcast_csv,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        ttk.Label(frm, text="Recipients (broadcast hanya ke list ini bila tidak kosong)", foreground="#666").grid(
            row=13, column=0, columnspan=4, sticky="w", pady=(8, 2)
        )
        picked_wrap = ttk.Frame(frm)
        picked_wrap.grid(row=14, column=0, columnspan=4, sticky="nsew")
        picked_wrap.grid_columnconfigure(0, weight=1)
        picked_wrap.grid_rowconfigure(0, weight=1)

        self.broadcast_picked_listbox = tk.Listbox(picked_wrap, height=7, width=96, selectmode=tk.EXTENDED)
        self.broadcast_picked_listbox.grid(row=0, column=0, sticky="nsew")
        self._style_listbox_widget(self.broadcast_picked_listbox)
        self.broadcast_picked_listbox_scroll = ttk.Scrollbar(picked_wrap, orient=tk.VERTICAL, command=self.broadcast_picked_listbox.yview)
        self.broadcast_picked_listbox_scroll.grid(row=0, column=1, sticky="ns")
        self.broadcast_picked_listbox.configure(yscrollcommand=self.broadcast_picked_listbox_scroll.set)

        self.broadcast_last_log_var = tk.StringVar(value="Last log: -")
        ttk.Label(frm, textvariable=self.broadcast_last_log_var, foreground="#666").grid(
            row=15, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        ttk.Label(frm, text="Broadcast Activity Log", foreground="#666").grid(row=16, column=0, sticky="w", pady=(6, 0))
        self.broadcast_log_box = tk.Text(frm, height=6, width=96, wrap=tk.WORD, font=("Consolas", 9))
        self.broadcast_log_box.grid(row=17, column=0, columnspan=4, sticky="ew")
        self._style_text_widget(self.broadcast_log_box, font=("Consolas", 9))

        self.broadcast_progress_var = tk.StringVar(value="Progress: 0/0 | Sent: 0 | Failed: 0")
        ttk.Label(frm, textvariable=self.broadcast_progress_var, foreground="#0078D4").grid(
            row=18, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        self.broadcast_progress = ttk.Progressbar(frm, mode="determinate", maximum=100, value=0)
        self.broadcast_progress.grid(row=19, column=0, columnspan=4, sticky="ew", pady=(2, 0))

        frm.grid_rowconfigure(11, weight=1)

    def _build_sessions_tab(self) -> None:
        frm = self.tab_sessions

        ttk.Label(frm, text="Sessions", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        # Listbox supaya user bisa klik untuk memilih session yang
        # akan dihapus. `self.session_phones` menyimpan phone (full,
        # unmasked) per baris untuk mapping idx → phone.
        self.session_phones: list[str] = []
        self.sessions_box = tk.Listbox(frm, height=16, width=90, font=("Consolas", 10))
        self.sessions_box.grid(row=1, column=0, columnspan=4, sticky="nsew")
        self._style_listbox_widget(self.sessions_box)

        ttk.Button(frm, text="Refresh", command=self._refresh_sessions_view).grid(row=2, column=0, pady=8, sticky="w")
        ttk.Button(
            frm,
            text="Hapus Akun Terpilih",
            style="Danger.TButton",
            command=self._remove_selected_session,
        ).grid(row=2, column=1, pady=8, sticky="w", padx=6)

        ttk.Label(frm, text="Encryption Password").grid(row=3, column=0, sticky="w")
        self.sessions_password = ttk.Entry(frm, show="*", width=28)
        self.sessions_password.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Test Sessions", command=self._test_sessions).grid(row=3, column=2, sticky="w")
        ttk.Button(frm, text="Remove Inactive", command=self._remove_inactive_sessions).grid(row=3, column=3, sticky="w", padx=6)

        frm.grid_rowconfigure(1, weight=1)
        frm.grid_columnconfigure(0, weight=1)

    def _build_account_manager_tab(self) -> None:
        """Tab "Account Manager".

        Tampilan per-akun dari grup/channel yang sudah di-join akun
        tersebut, plus tombol "Left Selected"/"Left All" untuk leave
        grup yang dipilih. Dipakai untuk management akun cepat tanpa
        harus buka Telegram per akun.

        Layout:
          row 0: judul + password
          row 1: tombol Refresh Selected
          row 2 (kiri): listbox daftar akun login
          row 2 (kanan): treeview grup/channel yang sudah di-join +
                        scrollbar
          row 3 (kanan): tombol Left Selected / Left All / Export CSV
        """
        frm = self.tab_account_manager

        ttk.Label(
            frm, text="Account Manager", font=("Segoe UI", 11, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Encryption Password").grid(row=0, column=1, sticky="e", padx=(8, 4))
        self.account_manager_password = ttk.Entry(frm, show="*", width=28)
        self.account_manager_password.grid(row=0, column=2, sticky="w", padx=(0, 8))

        ttk.Button(
            frm,
            text="Load Groups dari Akun Terpilih",
            style="Accent.TButton",
            command=self._account_manager_load_selected,
        ).grid(row=0, column=3, sticky="w", padx=(0, 6))

        # Kiri: listbox akun (multi-select supaya bisa cek beberapa
        # akun sekaligus tanpa harus klik satu-satu).
        ttk.Label(
            frm, text="Akun login (Ctrl/Shift untuk multi-select):"
        ).grid(row=1, column=0, sticky="w", pady=(4, 2))
        self._account_manager_phones: list[str] = []
        self.account_manager_accounts = tk.Listbox(
            frm,
            height=18,
            width=32,
            font=("Consolas", 10),
            exportselection=False,
            selectmode=tk.EXTENDED,
        )
        self.account_manager_accounts.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        self._style_listbox_widget(self.account_manager_accounts)
        self.account_manager_accounts.bind(
            "<<ListboxSelect>>", self._on_account_manager_account_pick
        )

        acc_btns = ttk.Frame(frm)
        acc_btns.grid(row=3, column=0, sticky="ew", pady=(8, 0), padx=(0, 8))
        ttk.Button(
            acc_btns, text="Pilih Semua", command=self._account_manager_select_all_accounts
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            acc_btns, text="Refresh", command=self._account_manager_refresh_accounts
        ).pack(side=tk.LEFT)

        # Kanan: treeview grup/channel yang sudah di-join akun terpilih.
        ttk.Label(
            frm, text="Grup/Channel yang sudah di-join:"
        ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 2))

        tree_wrap = ttk.Frame(frm)
        tree_wrap.grid(row=2, column=1, columnspan=3, sticky="nsew")

        self.account_manager_tree = ttk.Treeview(
            tree_wrap,
            columns=("akun", "title", "type", "username", "members", "id"),
            show="headings",
            height=18,
            selectmode="extended",
        )
        self.account_manager_tree.heading("akun", text="Akun")
        self.account_manager_tree.heading("title", text="Judul")
        self.account_manager_tree.heading("type", text="Tipe")
        self.account_manager_tree.heading("username", text="Username/Link")
        self.account_manager_tree.heading("members", text="Members")
        self.account_manager_tree.heading("id", text="ID")
        self.account_manager_tree.column("akun", width=130, anchor="center")
        self.account_manager_tree.column("title", width=280, anchor="w")
        self.account_manager_tree.column("type", width=100, anchor="center")
        self.account_manager_tree.column("username", width=180, anchor="w")
        self.account_manager_tree.column("members", width=80, anchor="e")
        self.account_manager_tree.column("id", width=110, anchor="e")

        v_scroll = ttk.Scrollbar(
            tree_wrap, orient=tk.VERTICAL, command=self.account_manager_tree.yview
        )
        self.account_manager_tree.configure(yscrollcommand=v_scroll.set)
        self.account_manager_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # `_account_manager_index_by_iid` map iid Treeview → dict info chat
        # untuk pengambilan id saat user pilih row & klik Left.
        self._account_manager_index_by_iid: dict[str, dict] = {}
        # `_account_manager_cache` simpan list grup per phone supaya
        # switch akun tidak fetch ulang (sampai user klik refresh).
        self._account_manager_cache: dict[str, list[dict]] = {}
        # Phone yang terakhir dipilih (untuk leave_selected supaya tahu
        # akun mana yang dipakai untuk leave_chat).
        self._account_manager_current_phone: str | None = None

        actions = ttk.Frame(frm)
        actions.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(
            actions,
            text="Joinkan ke Akun Lain",
            style="Accent.TButton",
            command=self._account_manager_join_to_other,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            actions,
            text="Left Selected",
            style="Danger.TButton",
            command=self._account_manager_leave_selected,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            actions,
            text="Left All",
            style="Danger.TButton",
            command=self._account_manager_leave_all,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            actions,
            text="Export CSV",
            command=self._account_manager_export_csv,
        ).pack(side=tk.LEFT, padx=(0, 6))

        frm.grid_rowconfigure(2, weight=1)
        frm.grid_columnconfigure(1, weight=1)

        # Seed list akun.
        self._account_manager_refresh_accounts()

    def _build_about_tab(self) -> None:
        about_logo = self._load_logo(160)
        if about_logo is not None:
            self._about_logo_ref = about_logo  # keep reference alive
            ttk.Label(self.tab_about, image=about_logo).pack(anchor="w", pady=(4, 8))

        ttk.Label(
            self.tab_about,
            text="Telegram Blaster",
            style="Header.TLabel",
        ).pack(anchor="w", pady=(4, 2))
        ttk.Label(
            self.tab_about,
            text="By VibeTool.Club  ·  v0.1",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        text = (
            "GUI desktop multi-akun Telegram untuk scraping members, adding members,\n"
            "dan broadcasting pesan + attachment.\n\n"
            "Data session disimpan lokal dan terenkripsi (Fernet + PBKDF2).\n"
            "Gunakan hanya untuk akun/grup yang Anda kelola secara legal.\n"
            "Patuhi Telegram Terms of Service & hukum lokal Anda.\n\n"
            "© VibeTool.Club  —  https://vibetool.club"
        )
        ttk.Label(self.tab_about, text=text, justify=tk.LEFT).pack(anchor="w", pady=(2, 12))

        ttk.Label(self.tab_about, text="Appearance", font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(2, 6))
        ttk.Label(
            self.tab_about,
            text="Gunakan tombol ini untuk ganti Dark/Light theme.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        initial_label = "Switch to Light" if self.theme_mode.get() == "dark" else "Switch to Dark"
        self.theme_button_var = tk.StringVar(value=initial_label)
        ttk.Button(
            self.tab_about,
            textvariable=self.theme_button_var,
            style="Accent.TButton",
            command=self._toggle_theme,
        ).pack(anchor="w")

    def _set_status(self, text: str) -> None:
        prefix = "● " if not text.startswith("●") else ""
        self.status_var.set(f"{prefix}{text}")

    def _log(self, text: str) -> None:
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)

    def _log_broadcast(self, text: str) -> None:
        self.broadcast_log_lines.append(text)
        if len(self.broadcast_log_lines) > 500:
            self.broadcast_log_lines = self.broadcast_log_lines[-500:]

        if hasattr(self, "broadcast_last_log_var"):
            shown = text if len(text) <= 130 else text[:130] + "..."
            self.broadcast_last_log_var.set(f"Last log: {shown}")

        if hasattr(self, "broadcast_log_box"):
            self.broadcast_log_box.insert(tk.END, text + "\n")
            self.broadcast_log_box.see(tk.END)

        if self.broadcast_log_window_text is not None:
            self.broadcast_log_window_text.insert(tk.END, text + "\n")
            self.broadcast_log_window_text.see(tk.END)

        self._log(text)

    def _open_broadcast_log_window(self) -> None:
        if self.broadcast_log_window is not None and self.broadcast_log_window.winfo_exists():
            self.broadcast_log_window.deiconify()
            self.broadcast_log_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Broadcast Activity Log")
        win.geometry("760x360")
        win.configure(bg=self.colors["bg"])

        box = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
        box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._style_text_widget(box)

        for line in self.broadcast_log_lines:
            box.insert(tk.END, line + "\n")
        box.see(tk.END)

        self.broadcast_log_window = win
        self.broadcast_log_window_text = box

        def _on_close():
            self.broadcast_log_window_text = None
            self.broadcast_log_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _show_qr_popup(self, image_path: str, url: str) -> None:
        if self.qr_window is None or not self.qr_window.winfo_exists():
            win = tk.Toplevel(self.root)
            win.title("Scan QR Login")
            win.geometry("420x520")
            win.configure(bg=self.colors["bg"])

            info_var = tk.StringVar(value="Scan QR ini dari Telegram mobile: Settings > Devices > Link Desktop Device")
            info = ttk.Label(win, textvariable=info_var, wraplength=390, justify=tk.LEFT)
            info.pack(anchor="w", padx=12, pady=(12, 8))

            lbl = ttk.Label(win)
            lbl.pack(anchor="center", padx=12, pady=(0, 8))

            ttk.Label(win, text="QR akan auto-refresh jika token berubah.", style="Muted.TLabel").pack(anchor="w", padx=12)

            self.qr_window = win
            self.qr_image_label = lbl
            self.qr_info_var = info_var

            def _on_close():
                self.qr_window = None
                self.qr_image_label = None
                self.qr_info_var = None
                self.qr_photo_ref = None
                win.destroy()

            win.protocol("WM_DELETE_WINDOW", _on_close)

        if self.qr_window is None or self.qr_image_label is None:
            return

        try:
            img = Image.open(image_path)
            img = img.resize((320, 320))
            photo = ImageTk.PhotoImage(img)
            self.qr_image_label.configure(image=photo)
            self.qr_photo_ref = photo
            if self.qr_info_var is not None:
                self.qr_info_var.set(
                    "Scan QR ini dari Telegram mobile: Settings > Devices > Link Desktop Device"
                )
            self.qr_window.deiconify()
            self.qr_window.lift()
        except Exception as exc:
            self._log(f"Gagal render QR popup: {exc}")

    def _close_qr_popup(self) -> None:
        if self.qr_window is not None and self.qr_window.winfo_exists():
            self.qr_window.destroy()
        self.qr_window = None
        self.qr_image_label = None
        self.qr_info_var = None
        self.qr_photo_ref = None

    def _post(self, fn) -> None:
        self.root.after(0, fn)

    def _run_async_job(self, coro, done_message: str | None = None) -> None:
        def _worker():
            try:
                asyncio.run(coro)
                if done_message:
                    self._post(lambda: self._log(done_message))
            except Exception as exc:
                self._post(lambda e=exc: messagebox.showerror("Error", str(e)))
                self._post(lambda e=exc: self._log(f"Error: {e}"))
            finally:
                self._post(lambda: self._set_status("Ready"))
                self._post(self._refresh_sessions_view)

        self._set_status("Running...")
        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _extract_flood_wait_seconds(error_text: str) -> int | None:
        m = re.search(r"FLOOD_WAIT_?(\d+)", (error_text or "").upper())
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        m2 = re.search(r"WAIT OF (\d+) SECONDS", (error_text or "").upper())
        if m2:
            try:
                return int(m2.group(1))
            except Exception:
                return None
        return None

    def _send_otp(self) -> None:
        if self.auth_busy:
            messagebox.showinfo("Login", "Proses login sedang berjalan. Tunggu sampai selesai.")
            return

        phone = self.login_phone.get().strip()
        enc_pw = self.login_enc_password.get().strip()
        if not phone or not enc_pw:
            messagebox.showwarning("Input", "Phone dan encryption password wajib diisi")
            return

        # The send_code request and the subsequent sign_in MUST share the same Pyrogram
        # Client instance + auth_key + MTProto session. Splitting them across two
        # asyncio.run() calls (different event loops) causes the server to reject the
        # phone_code_hash with PHONE_CODE_EXPIRED almost instantly. So we run the whole
        # flow inside one coroutine and use a threading.Event to wait for the user to
        # click "Complete Login".
        self.auth_busy = True
        self._otp_complete_event = threading.Event()
        self._otp_complete_data = None
        self._otp_ready_for_completion = False
        self.login_state = {"phone": phone, "enc_pw": enc_pw}

        async def _job():
            app = Client(
                name=f"otp_flow_{re.sub(r'\D+', '', phone)}",
                api_id=self.config.api_id,
                api_hash=self.config.api_hash,
                in_memory=True,
            )
            try:
                await app.connect()
                try:
                    sent = await app.send_code(phone)
                except Exception as exc:
                    wait_s = self._extract_flood_wait_seconds(str(exc))
                    if wait_s:
                        raise RuntimeError(
                            f"Terlalu sering minta OTP. Tunggu sekitar {wait_s} detik lalu klik Send OTP lagi."
                        ) from exc
                    raise

                self._post(
                    lambda p=phone: self._log(
                        f"OTP terkirim ke {p}. Input OTP lalu klik Complete Login."
                    )
                )
                self._post(lambda: setattr(self, "_otp_ready_for_completion", True))

                ev = self._otp_complete_event
                if ev is None:
                    return

                loop = asyncio.get_event_loop()
                while True:
                    completed = await loop.run_in_executor(None, ev.wait, 1.0)
                    if completed:
                        break
                    if not self.auth_busy:
                        # Window closed or job aborted from the outside.
                        return

                data = self._otp_complete_data or {}
                if data.get("cancelled"):
                    self._post(lambda: self._log("OTP login dibatalkan."))
                    return

                otp = (data.get("otp") or "").strip()
                twofa = (data.get("twofa") or "").strip()
                if not otp:
                    raise RuntimeError("OTP kosong saat Complete Login.")

                try:
                    await app.sign_in(
                        phone_number=phone,
                        phone_code_hash=sent.phone_code_hash,
                        phone_code=otp,
                    )
                except SessionPasswordNeeded:
                    if not twofa:
                        raise RuntimeError(
                            "Akun butuh 2FA password. Isi field 2FA Password lalu klik Complete Login lagi."
                        )
                    await app.check_password(twofa)
                except Exception as exc:
                    err_text = str(exc).upper()
                    if "PHONE_CODE_EXPIRED" in err_text:
                        raise RuntimeError(
                            "Kode OTP kadaluarsa di server. Klik Send OTP lagi untuk request kode baru."
                        ) from exc
                    if "PHONE_CODE_INVALID" in err_text:
                        # Allow the user to retry with the same phone_code_hash by clicking
                        # Complete Login again — keep the worker waiting on the event.
                        self._otp_complete_data = None
                        self._otp_complete_event = threading.Event()
                        self._post(
                            lambda: self._log(
                                "Kode OTP tidak valid. Perbaiki kode lalu klik Complete Login lagi."
                            )
                        )
                        self._post(
                            lambda: messagebox.showwarning(
                                "OTP Invalid",
                                "Kode OTP yang diinput salah. Perbaiki lalu klik Complete Login lagi.",
                            )
                        )
                        ev = self._otp_complete_event
                        completed = False
                        while not completed:
                            completed = await loop.run_in_executor(None, ev.wait, 1.0)
                            if not self.auth_busy:
                                return
                        data = self._otp_complete_data or {}
                        if data.get("cancelled"):
                            return
                        otp = (data.get("otp") or "").strip()
                        twofa = (data.get("twofa") or "").strip()
                        await app.sign_in(
                            phone_number=phone,
                            phone_code_hash=sent.phone_code_hash,
                            phone_code=otp,
                        )
                    else:
                        wait_s = self._extract_flood_wait_seconds(err_text)
                        if wait_s:
                            raise RuntimeError(
                                f"Terlalu sering request OTP. Tunggu sekitar {wait_s} detik, lalu klik Send OTP lagi."
                            ) from exc
                        raise

                me = await app.get_me()
                if not me:
                    raise RuntimeError("Login gagal diverifikasi: akun belum authorized")

                session_str = await app.export_session_string()
                await save_session_string(
                    config=self.config,
                    phone=phone,
                    session_string=session_str,
                    password=enc_pw,
                )
                self._post(
                    lambda p=phone: self._log(f"Login sukses untuk {p}. Session terenkripsi tersimpan.")
                )
                self._post(self._refresh_sessions_view)
            finally:
                try:
                    await app.disconnect()
                except Exception:
                    pass
                self._post(lambda: setattr(self, "auth_busy", False))
                self._post(lambda: setattr(self, "_otp_ready_for_completion", False))
                self._post(lambda: setattr(self, "_otp_complete_event", None))
                self._post(lambda: setattr(self, "_otp_complete_data", None))
                self._post(lambda: setattr(self, "login_state", None))

        self._run_async_job(_job())

    def _complete_otp_login(self) -> None:
        if not self.auth_busy or not self._otp_ready_for_completion or self._otp_complete_event is None:
            messagebox.showwarning("Login", "Klik Send OTP dulu dan tunggu OTP terkirim.")
            return
        if self._otp_complete_event.is_set():
            messagebox.showinfo("Login", "Sedang memproses login. Tunggu hasilnya.")
            return

        otp = self.login_otp.get().replace(" ", "").strip()
        twofa = self.login_2fa.get().strip()

        state_phone = ((self.login_state or {}).get("phone") or "").strip()
        input_phone = self.login_phone.get().strip()
        if state_phone and input_phone and state_phone != input_phone:
            messagebox.showwarning(
                "Login",
                "Nomor berubah setelah Send OTP. Klik Send OTP lagi untuk nomor terbaru.",
            )
            return

        if not otp:
            messagebox.showwarning("Login", "OTP wajib diisi")
            return

        self._otp_complete_data = {"otp": otp, "twofa": twofa}
        self._otp_complete_event.set()

    def _start_qr_login(self) -> None:
        if self.auth_busy:
            messagebox.showinfo("Login", "Proses login sedang berjalan. Tunggu sampai selesai.")
            return

        phone_label = self.qr_phone_label.get().strip()
        enc_pw = self.qr_enc_password.get().strip()
        if not phone_label or not enc_pw:
            messagebox.showwarning("Input", "Session label phone dan encryption password wajib diisi")
            return

        self.auth_busy = True

        async def _job():
            app = Client(
                name=f"qr_{phone_label}",
                api_id=self.config.api_id,
                api_hash=self.config.api_hash,
                in_memory=True,
            )
            try:
                await app.connect()
                self._post(lambda: self._log("Menyiapkan QR login..."))
                ok = await show_qr_and_wait_login(
                    app,
                    self.config.api_id,
                    self.config.api_hash,
                    timeout_seconds=300,
                    out_path="qr_login.png",
                    on_qr_file=lambda p, u: self._post(lambda pp=p, uu=u: self._show_qr_popup(pp, uu)),
                    on_event=lambda msg: self._post(lambda m=msg: self._log(m)),
                )
                if not ok:
                    # Defensive check: in some flows Telegram already authorizes session even when token polling misses success state.
                    try:
                        _me = await app.get_me()
                        if _me:
                            ok = True
                    except Exception:
                        pass
                if not ok:
                    try:
                        _uid = await app.storage.user_id()
                        if _uid and int(_uid) > 0:
                            ok = True
                    except Exception:
                        pass
                if not ok:
                    raise RuntimeError("QR login timeout atau invalid")

                try:
                    me = await app.get_me()
                    if me.phone_number:
                        phone_label_local = f"+{me.phone_number}"
                    else:
                        phone_label_local = phone_label
                except Exception:
                    phone_label_local = phone_label

                session_str = await app.export_session_string()
                await save_session_string(
                    config=self.config,
                    phone=phone_label_local,
                    session_string=session_str,
                    password=enc_pw,
                )
                self._post(
                    lambda: self._log(
                        "QR login sukses. Session tersimpan. "
                        "Jika QR sulit dibaca, scan file qr_login.png di root project."
                    )
                )
                self._post(self._close_qr_popup)
            finally:
                await app.disconnect()
                self._post(self._close_qr_popup)
                self._post(lambda: setattr(self, "auth_busy", False))

        self._run_async_job(_job())

    def _run_scrape(self) -> None:
        password = self.scrape_password.get().strip()
        target = self.scrape_target.get().strip()
        mode = self.scrape_mode.get()
        if not password or not target:
            messagebox.showwarning("Input", "Password dan target group wajib diisi")
            return

        selected_phone = self._parse_account_choice(self.scrape_account.get()) if hasattr(self, "scrape_account") else None
        if selected_phone:
            self.scrape_phone_hint = selected_phone
            self.scrape_strict_account = True
            self._log(f"Scrape menggunakan akun terpilih: {mask_phone(selected_phone)}")
        else:
            self.scrape_strict_account = False

        # Reset progress UI segera sebelum job mulai.
        self._post(lambda: self._set_scrape_progress("Mulai...", 0, None, indeterminate=True))

        async def _job():
            try:
                if mode == "Visible Members":
                    await self._scrape_visible(password, target)
                elif mode == "Hidden Members":
                    await self._scrape_hidden(password, target)
                else:
                    # "Visible + Hidden" — jalankan berurutan; per-grup CSV
                    # otomatis tergabung lewat append_members_dedup. Popup
                    # per-fase di-suppress agar hanya satu ringkasan akhir.
                    self._post(
                        lambda: self._set_scrape_progress(
                            "Phase 1/2: Visible Members...", 0, None, indeterminate=True
                        )
                    )
                    visible = await self._scrape_visible(password, target, show_popup=False)
                    self._post(
                        lambda: self._set_scrape_progress(
                            "Phase 2/2: Hidden Members...", 0, None, indeterminate=True
                        )
                    )
                    hidden = await self._scrape_hidden(password, target, show_popup=False)
                    self._post(
                        lambda v=visible, h=hidden: self._show_combined_scrape_summary(v, h)
                    )
            finally:
                self._post(lambda: self._set_scrape_progress("Selesai.", 0, None, indeterminate=False))

        self._run_async_job(_job())

    @staticmethod
    def _format_phase_summary(
        *,
        phase: str,
        phone: str,
        chat_title: str,
        fetched: int,
        global_before: int,
        global_after: int,
        per_group_path: str | None,
        per_group_before: int,
        per_group_after: int,
    ) -> str:
        """Format ringkasan satu fase scrape (Visible atau Hidden).

        Membedakan dengan jelas antara:
          - `fetched`: jumlah user yang berhasil dibaca dari Telegram pada fase ini
          - `per_group`: jumlah baris di file CSV per-grup setelah dedup
          - `global members.csv`: jumlah baris di file gabungan semua grup

        Sebelumnya hanya menampilkan `before`/`after` dari `members.csv` global,
        yang membingungkan karena angkanya jauh lebih besar dari isi file
        per-grup (sumber kebenaran yang user lihat).
        """
        added_pg = max(per_group_after - per_group_before, 0)
        added_global = max(global_after - global_before, 0)
        title = (chat_title or "").strip()
        head = f"{phase} scrape via {phone}"
        if title:
            head += f" ({title})"
        head += " selesai."
        lines = [
            head,
            f"  Fetched dari grup: {fetched} member",
        ]
        if per_group_path:
            lines.append(
                f"  Per-grup CSV: {per_group_after} record (+{added_pg} unik baru fase ini)"
            )
            lines.append(f"  File: {per_group_path}")
        lines.append(
            f"  members.csv global: {global_after} record (+{added_global} unik baru fase ini)"
        )
        return "\n".join(lines)

    def _show_combined_scrape_summary(self, visible: dict | None, hidden: dict | None) -> None:
        # Ambil snapshot terakhir dari per-grup & global agar user punya angka
        # tunggal yang bisa di-cross-check langsung dengan file CSV di disk.
        last_stage = hidden or visible
        first_stage = visible or hidden

        lines = ["Visible + Hidden scrape selesai."]
        title = (last_stage or {}).get("chat_title") or ""
        if title:
            lines.append(f"  Grup: {title}")
        phone = (last_stage or {}).get("phone")
        if phone:
            lines.append(f"  Akun: {phone}")
        lines.append("")

        for stage_label, stage in (("Visible", visible), ("Hidden", hidden)):
            if not stage:
                continue
            fetched = stage.get("fetched", 0)
            pg_added = max(stage.get("per_group_after", 0) - stage.get("per_group_before", 0), 0)
            global_added = max(stage.get("global_after", 0) - stage.get("global_before", 0), 0)
            lines.append(
                f"  {stage_label}: fetched {fetched}, +{pg_added} unik di per-grup, "
                f"+{global_added} unik di members.csv"
            )

        # Total final (sumber kebenaran user) — dari fase terakhir yang ditulis.
        if last_stage:
            pg_total = last_stage.get("per_group_after", 0)
            global_total = last_stage.get("global_after", 0)
            pg_path = last_stage.get("per_group_path") or (first_stage or {}).get("per_group_path")
            lines.append("")
            lines.append(f"  TOTAL per-grup (Visible+Hidden, dedup): {pg_total} record")
            if pg_path:
                lines.append(f"  File: {pg_path}")
            lines.append(f"  TOTAL members.csv global: {global_total} record")

        summary = "\n".join(lines)
        self._log(summary)
        messagebox.showinfo("Scrape Result", summary)

    def _set_scrape_progress(
        self,
        text: str,
        current: int,
        total: int | None,
        *,
        indeterminate: bool = False,
    ) -> None:
        """Update progress bar + label di tab Members Scraper.

        - `indeterminate=True` → animasi marquee (saat total tidak diketahui,
          mis. Hidden scrape yang iterasi history). Label tetap tampilkan
          jumlah member yang sudah ditemukan kalau `current > 0`.
        - `total>0`, `indeterminate=False` → bar 0..100% berdasarkan
          current/total. Label tampilkan angka + persen.
        - `total=None`, `indeterminate=False`, `current>0` → label tampilkan
          count saja (untuk fase final/transition).
        - `current=0`, `total=None`, `indeterminate=False` → bar reset 0%,
          label tampilkan teks status saja.
        """
        if not hasattr(self, "scrape_progress") or not hasattr(self, "scrape_progress_label"):
            return

        if indeterminate:
            try:
                self.scrape_progress.configure(mode="indeterminate", maximum=100)
                self.scrape_progress.start(80)
            except Exception:
                pass
            if current and current > 0:
                self.scrape_progress_label.configure(
                    text=f"{text} — {current} member ditemukan"
                )
            else:
                self.scrape_progress_label.configure(text=text)
            return

        # Stop indeterminate animation kalau aktif.
        try:
            self.scrape_progress.stop()
        except Exception:
            pass

        if total and total > 0:
            pct = min(100, int(current * 100 / total))
            self.scrape_progress.configure(mode="determinate", maximum=100, value=pct)
            self.scrape_progress_label.configure(
                text=f"{text}: {current} / {total} ({pct}%)"
            )
        else:
            self.scrape_progress.configure(mode="determinate", maximum=100, value=0)
            if current:
                self.scrape_progress_label.configure(text=f"{text}: {current} member")
            else:
                self.scrape_progress_label.configure(text=text)

    def _load_joined_groups(self) -> None:
        password = self.scrape_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Isi Encryption Password dulu")
            return

        selected_phone = self._parse_account_choice(self.scrape_account.get()) if hasattr(self, "scrape_account") else None

        async def _job():
            sessions = self.manager.list_sessions()
            if not sessions:
                raise RuntimeError("Belum ada session login")

            if selected_phone:
                phone = selected_phone
            else:
                # Use first available account; if all cooldown, still try first stored account for group listing.
                phone = self.manager.get_next_phone() or sessions[0].phone
            app = await self.manager.build_client(phone, password)
            groups: list[dict] = []
            try:
                await app.connect()
                async for dialog in app.get_dialogs():
                    chat = dialog.chat
                    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
                        continue

                    title = chat.title or str(chat.id)
                    members_count = getattr(chat, "members_count", None)
                    if members_count is None:
                        try:
                            full_chat = await app.get_chat(chat.id)
                            members_count = getattr(full_chat, "members_count", None)
                        except Exception:
                            members_count = None

                    if chat.username:
                        target = f"@{chat.username}"
                    else:
                        target = str(chat.id)

                    groups.append(
                        {
                            "title": title,
                            "target": target,
                            "id": str(chat.id),
                            "members_count": members_count,
                            "source_phone": phone,
                        }
                    )
            finally:
                await app.disconnect()

            groups.sort(key=lambda x: x["title"].lower())
            self._post(lambda g=groups: self._set_group_candidates(g))
            self._post(lambda p=phone: setattr(self, "scrape_phone_hint", p))
            self._post(lambda: self._log(f"Loaded {len(groups)} joined groups"))

        self._run_async_job(_job())

    def _set_group_candidates(self, groups: list[dict]) -> None:
        self.group_candidates = groups
        self.group_listbox.delete(0, tk.END)
        for item in groups:
            count = item.get("members_count")
            count_text = str(count) if isinstance(count, int) and count >= 0 else "?"
            self.group_listbox.insert(tk.END, f"{item['title']} ({count_text}) | {item['target']}")

    async def _find_phone_with_target_access(self, password: str, target: str, preferred_phone: str | None = None) -> str | None:
        sessions = self.manager.list_sessions()
        if not sessions:
            return None

        phones = [s.phone for s in sessions]
        if preferred_phone and preferred_phone in phones:
            phones = [preferred_phone] + [p for p in phones if p != preferred_phone]

        for phone in phones:
            app = None
            try:
                app = await self.manager.build_client(phone, password)
                await app.connect()
                await resolve_target_chat(app, target)
                return phone
            except Exception:
                pass
            finally:
                if app is not None:
                    try:
                        await app.disconnect()
                    except Exception:
                        pass

        return None

    async def _execute_with_scrape_hint(self, password: str, target: str, operation):
        preferred_phone = (self.scrape_phone_hint or "").strip()
        strict = bool(getattr(self, "scrape_strict_account", False))

        # For numeric group IDs, find an account that can actually resolve this target — only when not strict.
        if not strict and target.strip().lstrip("-").isdigit():
            discovered = await self._find_phone_with_target_access(password, target, preferred_phone=preferred_phone)
            if discovered:
                preferred_phone = discovered
                self.scrape_phone_hint = discovered

        if preferred_phone:
            app = None
            try:
                app = await self.manager.build_client(preferred_phone, password)
                await app.connect()
                result = await operation(app, preferred_phone)
                return result, preferred_phone
            except Exception as exc:
                if strict:
                    self._post(
                        lambda p=preferred_phone, e=exc: self._log(
                            f"Akun terpilih {mask_phone(p)} gagal: {type(e).__name__}: {e}"
                        )
                    )
                    raise
                self._post(
                    lambda p=preferred_phone, e=exc: self._log(
                        f"Akun hint {p} gagal untuk scrape, fallback ke rotasi akun: {type(e).__name__}: {e}"
                    )
                )
            finally:
                if app is not None:
                    try:
                        await app.disconnect()
                    except Exception:
                        pass

        return await execute_with_rotation(self.manager, password, operation)

    async def _execute_on_account(self, password: str, account_phone: str | None, operation):
        """Execute `operation(app, phone)` either on a specific account (no rotation) or via rotation.

        When `account_phone` is given, the operation runs only on that account. FloodWait < 1h is
        respected with a single retry; FloodWait >= 1h sets the cooldown and surfaces a clear error.
        When `account_phone` is None, falls back to `execute_with_rotation`.
        """
        if not account_phone:
            return await execute_with_rotation(self.manager, password, operation)

        app = await self.manager.build_client(account_phone, password)
        await app.connect()
        try:
            try:
                result = await operation(app, account_phone)
                return result, account_phone
            except FloodWait as fw:
                wait = int(fw.value)
                if wait >= 3600:
                    self.manager.set_cooldown(phone=account_phone, seconds=wait)
                    raise RuntimeError(
                        f"Akun {mask_phone(account_phone)} kena FloodWait {wait}s; cooldown dipasang. "
                        "Pilih akun lain di dropdown atau tunggu cooldown habis."
                    ) from fw
                await asyncio.sleep(wait + 2)
                result = await operation(app, account_phone)
                return result, account_phone
        finally:
            try:
                await app.disconnect()
            except Exception:
                pass

    def _use_selected_group(self) -> None:
        selected = self.group_listbox.curselection()
        if not selected:
            messagebox.showinfo("Groups", "Pilih satu grup dari daftar dulu")
            return
        idx = selected[0]
        if idx < 0 or idx >= len(self.group_candidates):
            return
        chosen = self.group_candidates[idx]
        target = chosen["target"]
        source_phone = (chosen.get("source_phone") or "").strip()
        if source_phone:
            self.scrape_phone_hint = source_phone
        self.scrape_target.delete(0, tk.END)
        self.scrape_target.insert(0, target)
        self._log(f"Selected target: {target}")

    def _reload_broadcast_members(self) -> None:
        rows = read_members_csv(self.config.members_csv)
        self.broadcast_rows = rows
        if not hasattr(self, "broadcast_listbox"):
            return

        self._apply_broadcast_filter()

    def _clear_scraped_members(self) -> None:
        total = len(self.broadcast_rows)
        csv_path = Path(self.config.members_csv)
        csv_exists = csv_path.exists()

        if total == 0 and not csv_exists:
            messagebox.showinfo("Hapus Hasil Scrape", "List hasil scrape sudah kosong.")
            return

        msg_lines = [
            f"Hapus semua hasil scrape ({total} kontak)?",
            "",
            "Tindakan ini akan:",
            "  - Mengosongkan daftar kontak hasil scrape di GUI",
        ]
        if csv_exists:
            msg_lines.append(f"  - Membackup file {csv_path.name} ke folder backups/ lalu menghapusnya")
        msg_lines.append("")
        msg_lines.append("Picked Recipients & Manual Targets TIDAK terhapus.")
        msg_lines.append("Lanjut?")

        if not messagebox.askyesno("Konfirmasi Hapus", "\n".join(msg_lines)):
            return

        backup_path: Path | None = None
        if csv_exists:
            try:
                backups_dir = csv_path.parent / "backups"
                backups_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d-%H%M%S")
                backup_path = backups_dir / f"{csv_path.stem}.{ts}{csv_path.suffix}.bak"
                csv_path.replace(backup_path)
            except Exception as exc:
                messagebox.showerror(
                    "Hapus Hasil Scrape",
                    f"Gagal backup/hapus {csv_path.name}: {exc}",
                )
                return

        self.broadcast_rows = []
        self.broadcast_filtered_indices = []
        if hasattr(self, "broadcast_listbox"):
            self.broadcast_listbox.delete(0, tk.END)
        self._update_broadcast_contact_stats()
        self._apply_broadcast_filter()

        if backup_path is not None:
            self._log_broadcast(f"Hasil scrape dihapus. Backup: {backup_path}")
            self._log(f"members.csv di-backup ke {backup_path}")
        else:
            self._log_broadcast("Hasil scrape (in-memory) dikosongkan.")

    def _import_broadcast_csv(self) -> None:
        """Import 1+ file CSV hasil Members Scraper ke daftar Broadcast.

        Multi-select didukung; tiap file akan di-append ke `members.csv`
        global via `append_members_dedup` (dedup by ID), jadi:
          - Tidak overwrite data yang sudah ada.
          - Tidak ada duplikat antar-file.
          - Persist setelah restart aplikasi.
        """
        # Default folder: Hasil Scrape Member/ (kalau ada) untuk akses cepat.
        per_group_dir = Path("Hasil Scrape Member")
        initial_dir = str(per_group_dir if per_group_dir.exists() else Path.cwd())

        paths = filedialog.askopenfilenames(
            title="Import CSV ke Broadcast (multi-select didukung)",
            initialdir=initial_dir,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not paths:
            return

        before_total = len(self.broadcast_rows)
        per_file_stats: list[tuple[str, int, int]] = []  # (file, read, added)
        skipped_files: list[tuple[str, str]] = []  # (file, reason)

        for path_str in paths:
            path = Path(path_str)
            try:
                rows = read_members_csv(str(path))
            except Exception as exc:
                skipped_files.append((path.name, f"baca gagal: {type(exc).__name__}: {exc}"))
                continue

            if not rows:
                skipped_files.append((path.name, "file kosong / tidak ada baris"))
                continue

            # Validasi minimum: harus ada kolom ID (untuk dedup).
            sample_keys = set(rows[0].keys())
            if "ID" not in sample_keys:
                skipped_files.append((path.name, "kolom 'ID' tidak ditemukan"))
                continue

            # Filter row tanpa ID — tidak bisa dedup, lebih aman skip.
            valid_rows = [r for r in rows if (r.get("ID") or "").strip()]
            if not valid_rows:
                skipped_files.append((path.name, "tidak ada baris dengan ID terisi"))
                continue

            try:
                before, after = append_members_dedup(self.config.members_csv, valid_rows)
            except Exception as exc:
                skipped_files.append((path.name, f"append gagal: {type(exc).__name__}: {exc}"))
                continue

            added = after - before
            per_file_stats.append((path.name, len(valid_rows), added))

        # Reload list dari members.csv yang sudah diupdate.
        self._reload_broadcast_members()
        after_total = len(self.broadcast_rows)
        added_total = after_total - before_total

        lines = [
            f"Import CSV selesai.",
            f"  Total kontak sebelum: {before_total}",
            f"  Total kontak sesudah: {after_total} (+{added_total} unik baru)",
            "",
        ]
        if per_file_stats:
            lines.append("Per file:")
            for fname, read_n, added_n in per_file_stats:
                lines.append(f"  - {fname}: {read_n} dibaca, +{added_n} unik baru")
        if skipped_files:
            lines.append("")
            lines.append("File di-skip:")
            for fname, reason in skipped_files:
                lines.append(f"  - {fname}: {reason}")

        messagebox.showinfo("Import CSV", "\n".join(lines))
        self._log_broadcast(
            f"Import CSV: {len(per_file_stats)} file diimport, +{added_total} kontak unik baru "
            f"(total kini {after_total})"
        )

    def _apply_broadcast_filter(self) -> None:
        if not hasattr(self, "broadcast_listbox"):
            return

        query = ""
        if hasattr(self, "broadcast_search"):
            query = self.broadcast_search.get().strip().lower()

        self.broadcast_listbox.delete(0, tk.END)
        self.broadcast_filtered_indices = []
        for idx, row in enumerate(self.broadcast_rows):
            name = (row.get("Name") or "").strip() or "<No Name>"
            username = (row.get("Username") or "").strip()
            username_text = f"@{username}" if username else "-"
            uid = (row.get("ID") or "").strip()

            haystack = f"{name} {username_text} {uid}".lower()
            if query and query not in haystack:
                continue

            self.broadcast_filtered_indices.append(idx)
            self.broadcast_listbox.insert(tk.END, f"{name} | {username_text} | {uid}")

        shown = len(self.broadcast_filtered_indices)
        total = len(self.broadcast_rows)
        if hasattr(self, "broadcast_empty_var"):
            if total == 0:
                self.broadcast_empty_var.set("Belum ada kontak. Jalankan scrape dulu.")
            elif shown == 0:
                self.broadcast_empty_var.set("No filtered results")
            else:
                self.broadcast_empty_var.set("")

        self._update_broadcast_contact_stats()

    def _update_broadcast_contact_stats(self) -> None:
        if not hasattr(self, "broadcast_count_var"):
            return

        shown = len(self.broadcast_filtered_indices)
        total = len(self.broadcast_rows)
        selected = len(self.broadcast_listbox.curselection()) if hasattr(self, "broadcast_listbox") else 0
        manual = len(self._parse_manual_targets()) if hasattr(self, "broadcast_manual_targets") else 0
        picked = len(self.broadcast_picked_rows) if hasattr(self, "broadcast_picked_rows") else 0
        self.broadcast_count_var.set(
            f"Contacts: {shown} shown / {total} total | Selected: {selected} | Picked: {picked} | Manual: {manual}"
        )

    def _on_broadcast_selection_changed(self, _event=None) -> None:
        self._update_broadcast_contact_stats()

    def _on_broadcast_listbox_mousewheel(self, event) -> str:
        if event.delta:
            self.broadcast_listbox.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _on_manual_targets_changed(self, _event=None) -> None:
        self._update_broadcast_contact_stats()

    def _load_manual_targets_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load manual targets",
            filetypes=[("Text file", "*.txt"), ("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")

        if hasattr(self, "broadcast_manual_targets"):
            self.broadcast_manual_targets.delete("1.0", tk.END)
            self.broadcast_manual_targets.insert("1.0", text)
        self._update_broadcast_contact_stats()

    def _clear_manual_targets(self) -> None:
        if hasattr(self, "broadcast_manual_targets"):
            self.broadcast_manual_targets.delete("1.0", tk.END)
        self._update_broadcast_contact_stats()

    def _parse_manual_targets(self) -> list[str]:
        if not hasattr(self, "broadcast_manual_targets"):
            return []

        raw = self.broadcast_manual_targets.get("1.0", tk.END)
        parts = re.split(r"[\n,;]+", raw)
        out: list[str] = []
        seen: set[str] = set()
        for part in parts:
            token = normalize_chat_target((part or "").strip())
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out

    @staticmethod
    def _recipient_key(row: dict) -> str:
        username = (row.get("Username") or "").strip().lower()
        uid = (row.get("ID") or "").strip()
        raw_target = (row.get("Raw Target") or "").strip().lower()
        if username:
            return f"u:{username}"
        if uid:
            return f"id:{uid}"
        if raw_target:
            return f"r:{raw_target}"
        return ""

    def _build_manual_recipient_rows(self) -> list[dict]:
        rows: list[dict] = []
        for target in self._parse_manual_targets():
            if target.lstrip("-").isdigit():
                rows.append(
                    {
                        "Name": "Manual Target",
                        "ID": target,
                        "Username": "",
                        "Access Hash": "",
                        "Group Name": "",
                        "Group ID": "",
                        "Raw Target": target,
                        "_source": "manual",
                    }
                )
            else:
                rows.append(
                    {
                        "Name": "Manual Target",
                        "ID": "",
                        "Username": target.lstrip("@"),
                        "Access Hash": "",
                        "Group Name": "",
                        "Group ID": "",
                        "Raw Target": target,
                        "_source": "manual",
                    }
                )
        return rows

    def _merge_recipients(self, csv_rows: list[dict], manual_rows: list[dict]) -> list[dict]:
        merged: list[dict] = []
        seen: set[str] = set()
        for row in csv_rows + manual_rows:
            key = self._recipient_key(row)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

    def _on_broadcast_search_changed(self, _event=None) -> None:
        self._apply_broadcast_filter()

    def _clear_broadcast_search(self) -> None:
        if hasattr(self, "broadcast_search"):
            self.broadcast_search.delete(0, tk.END)
        self._apply_broadcast_filter()

    def _reset_broadcast_progress(self, total: int) -> None:
        if not hasattr(self, "broadcast_progress"):
            return
        self.broadcast_progress.configure(maximum=max(1, total), value=0)
        self.broadcast_progress_var.set(f"Progress: 0/{total} | Sent: 0 | Failed: 0")

    def _update_broadcast_progress(self, processed: int, total: int, sent: int, failed: int) -> None:
        if not hasattr(self, "broadcast_progress"):
            return
        self.broadcast_progress.configure(maximum=max(1, total), value=processed)
        self.broadcast_progress_var.set(
            f"Progress: {processed}/{total} | Sent: {sent} | Failed: {failed}"
        )

    def _select_all_broadcast_members(self) -> None:
        if not self.broadcast_rows:
            return
        self.broadcast_listbox.selection_set(0, tk.END)
        self._update_broadcast_contact_stats()

    def _clear_broadcast_selection(self) -> None:
        self.broadcast_listbox.selection_clear(0, tk.END)
        self._update_broadcast_contact_stats()

    def _format_member_label(self, row: dict) -> str:
        name = (row.get("Name") or "").strip() or "<No Name>"
        username = (row.get("Username") or "").strip()
        username_text = f"@{username}" if username else "-"
        uid = (row.get("ID") or "").strip()
        return f"{name} | {username_text} | {uid}"

    def _refresh_picked_listbox(self) -> None:
        if not hasattr(self, "broadcast_picked_listbox"):
            return
        self.broadcast_picked_listbox.delete(0, tk.END)
        for row in self.broadcast_picked_rows:
            self.broadcast_picked_listbox.insert(tk.END, self._format_member_label(row))
        self._update_broadcast_contact_stats()

    def _add_selected_to_picked(self) -> None:
        if not hasattr(self, "broadcast_listbox"):
            return
        sel = self.broadcast_listbox.curselection()
        if not sel:
            messagebox.showinfo("Recipients", "Pilih dulu satu/lebih kontak di list scraping")
            return

        existing_ids = {(r.get("ID") or "").strip() for r in self.broadcast_picked_rows}
        added = 0
        for idx in sel:
            if 0 <= idx < len(self.broadcast_filtered_indices):
                src_idx = self.broadcast_filtered_indices[idx]
                row = self.broadcast_rows[src_idx]
                rid = (row.get("ID") or "").strip()
                if rid and rid in existing_ids:
                    continue
                self.broadcast_picked_rows.append(dict(row))
                if rid:
                    existing_ids.add(rid)
                added += 1

        self._refresh_picked_listbox()
        self._log_broadcast(f"Recipients: tambah {added} kontak (total: {len(self.broadcast_picked_rows)})")

    def _remove_picked_recipients(self) -> None:
        if not hasattr(self, "broadcast_picked_listbox"):
            return
        sel = self.broadcast_picked_listbox.curselection()
        if not sel:
            messagebox.showinfo("Recipients", "Pilih dulu kontak di list Recipients yang akan dihapus")
            return
        keep = [row for idx, row in enumerate(self.broadcast_picked_rows) if idx not in set(sel)]
        removed = len(self.broadcast_picked_rows) - len(keep)
        self.broadcast_picked_rows = keep
        self._refresh_picked_listbox()
        self._log_broadcast(f"Recipients: hapus {removed} kontak (sisa: {len(self.broadcast_picked_rows)})")

    def _clear_picked_recipients(self) -> None:
        if not self.broadcast_picked_rows:
            return
        if not messagebox.askyesno("Recipients", f"Kosongkan list Recipients ({len(self.broadcast_picked_rows)} kontak)?"):
            return
        self.broadcast_picked_rows = []
        self._refresh_picked_listbox()
        self._log_broadcast("Recipients: dikosongkan")

    def _write_per_group_csv(
        self, chat_info: dict, rows: list[dict]
    ) -> tuple[str | None, int, int]:
        """Tulis hasil scrape ke `Hasil Scrape Member/<title>.csv` (per-grup) dengan dedup.

        Returns (path, before, after) — `before`/`after` adalah jumlah baris di
        file per-grup sebelum dan setelah append (sesuai semantik
        `append_members_dedup`). Mengembalikan (None, 0, 0) bila tidak ada
        rows / title kosong / gagal tulis.
        """
        if not rows:
            return None, 0, 0
        title = (chat_info or {}).get("title") or ""
        if not title.strip():
            # Fallback ke Group ID kalau title kosong, agar file tetap punya nama deskriptif.
            title = f"group_{(chat_info or {}).get('id') or 'untitled'}"
        try:
            path = per_group_members_path(self.config.members_csv, title)
            before, after = append_members_dedup(str(path), rows)
            return str(path), before, after
        except Exception as exc:
            self._post(
                lambda e=exc: self._log(
                    f"Gagal tulis per-grup CSV: {type(e).__name__}: {e}"
                )
            )
            return None, 0, 0

    async def _scrape_visible(self, password: str, target: str, *, show_popup: bool = True) -> dict | None:
        rows: list[dict] = []
        chat_info: dict = {"title": "", "id": "", "members_count": None}

        async def _op(app, _phone: str):
            chat = await resolve_target_chat(app, target)
            chat_info["title"] = chat.title or ""
            chat_info["id"] = str(chat.id)
            total_members = getattr(chat, "members_count", None)
            chat_info["members_count"] = total_members

            self._post(
                lambda t=total_members: self._set_scrape_progress(
                    "Visible scrape...",
                    0,
                    t if isinstance(t, int) and t > 0 else None,
                    indeterminate=not (isinstance(t, int) and t > 0),
                )
            )

            count = 0
            async for member in app.get_chat_members(chat.id, filter=ChatMembersFilter.SEARCH):
                user = member.user
                if not user or user.is_bot:
                    continue
                access_hash = await self._resolve_access_hash(app, int(user.id))
                full_name = (user.first_name or "") + (f" {user.last_name}" if user.last_name else "")
                rows.append(
                    {
                        "Name": full_name,
                        "ID": str(user.id),
                        "Username": user.username or "",
                        "Access Hash": str(access_hash or ""),
                        "Gender": infer_gender(full_name),
                        "Group Name": chat.title or "",
                        "Group ID": str(chat.id),
                    }
                )
                count += 1
                # Update tiap 10 member supaya UI tidak overload event loop.
                if count % 10 == 0:
                    self._post(
                        lambda c=count, t=total_members: self._set_scrape_progress(
                            "Visible scrape",
                            c,
                            t if isinstance(t, int) and t > 0 else None,
                        )
                    )
            # Final update.
            self._post(
                lambda c=count, t=total_members: self._set_scrape_progress(
                    "Visible scrape",
                    c,
                    t if isinstance(t, int) and t > 0 else None,
                )
            )
            return True

        _, phone = await self._execute_with_scrape_hint(password, target, _op)
        fetched = len(rows)
        global_before, global_after = append_members_dedup(self.config.members_csv, rows)
        per_group_path, pg_before, pg_after = self._write_per_group_csv(chat_info, rows)
        summary = self._format_phase_summary(
            phase="Visible",
            phone=phone,
            chat_title=chat_info.get("title") or "",
            fetched=fetched,
            global_before=global_before,
            global_after=global_after,
            per_group_path=per_group_path,
            per_group_before=pg_before,
            per_group_after=pg_after,
        )
        self._post(lambda s=summary: self._log(s))
        if show_popup:
            self._post(lambda s=summary: messagebox.showinfo("Scrape Result", s))
        self._post(self._reload_broadcast_members)
        return {
            "phase": "visible",
            "phone": phone,
            "chat_title": chat_info.get("title") or "",
            "fetched": fetched,
            "global_before": global_before,
            "global_after": global_after,
            "per_group_path": per_group_path,
            "per_group_before": pg_before,
            "per_group_after": pg_after,
        }

    async def _scrape_hidden(self, password: str, target: str, *, show_popup: bool = True) -> dict | None:
        checkpoint = load_checkpoint(self.config.checkpoint_file)
        start_from = 0
        users: dict[str, dict] = {}
        chat_info: dict = {"title": "", "id": "", "members_count": None}
        if checkpoint.get("target") == target and checkpoint.get("last_message_id"):
            start_from = int(checkpoint.get("last_message_id", 0))
            users = checkpoint.get("users", {})

        async def _op(app, _phone: str):
            chat = await resolve_target_chat(app, target)
            chat_info["title"] = chat.title or ""
            chat_info["id"] = str(chat.id)
            chat_info["members_count"] = getattr(chat, "members_count", None)

            # Hidden scrape iterasi message history — kita tidak tahu total messages,
            # jadi pakai indeterminate animation + count unique users yang ditemukan.
            self._post(
                lambda: self._set_scrape_progress(
                    "Hidden scrape (membaca history)...", 0, None, indeterminate=True
                )
            )
            counter = 0

            async def _row_for_user(u) -> dict:
                access_hash = await self._resolve_access_hash(app, int(u.id))
                full_name = (u.first_name or "") + (f" {u.last_name}" if u.last_name else "")
                return {
                    "Name": full_name,
                    "ID": str(u.id),
                    "Username": u.username or "",
                    "Access Hash": str(access_hash or ""),
                    "Gender": infer_gender(full_name),
                    "Group Name": chat.title or "",
                    "Group ID": str(chat.id),
                }

            async for msg in app.get_chat_history(chat.id):
                if start_from and msg.id >= start_from:
                    continue

                extracted: dict[str, dict] = {}
                if msg.from_user and not msg.from_user.is_bot:
                    extracted[str(msg.from_user.id)] = await _row_for_user(msg.from_user)

                if getattr(msg, "forward_from", None) and not msg.forward_from.is_bot:
                    extracted[str(msg.forward_from.id)] = await _row_for_user(msg.forward_from)

                entities = msg.entities or []
                text = msg.text or msg.caption or ""
                for ent in entities:
                    if ent.type == MessageEntityType.TEXT_MENTION and getattr(ent, "user", None):
                        u = ent.user
                        if not u.is_bot:
                            extracted[str(u.id)] = await _row_for_user(u)
                    elif ent.type == MessageEntityType.MENTION:
                        mention = text[ent.offset : ent.offset + ent.length].strip().lstrip("@")
                        if mention:
                            try:
                                u = await app.get_users(mention)
                                if u and not u.is_bot:
                                    extracted[str(u.id)] = await _row_for_user(u)
                            except Exception:
                                pass

                if extracted:
                    new_users = {uid: row for uid, row in extracted.items() if uid not in users}
                    users.update(extracted)
                    counter += len(extracted)
                    if new_users:
                        # Update progress hanya saat ada user UNIK baru, tidak per-message.
                        # Pakai indeterminate=True agar marquee tetap jalan + label
                        # update angka unique yang ditemukan secara realtime.
                        self._post(
                            lambda c=len(users): self._set_scrape_progress(
                                "Hidden scrape (membaca history)",
                                c,
                                None,
                                indeterminate=True,
                            )
                        )
                    if counter % 50 == 0:
                        save_checkpoint(
                            self.config.checkpoint_file,
                            {"target": target, "last_message_id": msg.id, "users": users},
                        )

            return True

        _, phone = await self._execute_with_scrape_hint(password, target, _op)
        rows_list = list(users.values())
        fetched = len(rows_list)
        global_before, global_after = append_members_dedup(self.config.members_csv, rows_list)
        per_group_path, pg_before, pg_after = self._write_per_group_csv(chat_info, rows_list)
        save_checkpoint(self.config.checkpoint_file, {})
        summary = self._format_phase_summary(
            phase="Hidden",
            phone=phone,
            chat_title=chat_info.get("title") or "",
            fetched=fetched,
            global_before=global_before,
            global_after=global_after,
            per_group_path=per_group_path,
            per_group_before=pg_before,
            per_group_after=pg_after,
        )
        self._post(lambda s=summary: self._log(s))
        if show_popup:
            self._post(lambda s=summary: messagebox.showinfo("Scrape Result", s))
        self._post(self._reload_broadcast_members)
        return {
            "phase": "hidden",
            "phone": phone,
            "chat_title": chat_info.get("title") or "",
            "fetched": fetched,
            "global_before": global_before,
            "global_after": global_after,
            "per_group_path": per_group_path,
            "per_group_before": pg_before,
            "per_group_after": pg_after,
        }

    def _run_adder(self) -> None:
        password = self.add_password.get().strip()
        target = self.add_target.get().strip()
        mode = self.add_mode.get()
        if not password or not target:
            messagebox.showwarning("Input", "Password dan target wajib diisi")
            return

        adder_account_phone = self._parse_account_choice(self.add_account.get()) if hasattr(self, "add_account") else None
        if adder_account_phone:
            self._log(f"Adder menggunakan akun terpilih: {mask_phone(adder_account_phone)} (rotasi dimatikan)")

        async def _job():
            rows = read_members_csv(self.config.members_csv)
            if not rows:
                raise RuntimeError("members.csv kosong")

            rush_mode = mode == "Rush"
            processed_ids: set[str] = set()
            added = 0
            skipped = 0

            for row in rows:
                uid = row.get("ID", "").strip()
                if not uid:
                    skipped += 1
                    continue

                try:
                    async def _op(app, _phone: str):
                        await app.add_chat_members(target, int(uid))
                        return True

                    _, used_phone = await self._execute_on_account(password, adder_account_phone, _op)
                    added += 1
                    processed_ids.add(uid)
                    self._post(lambda p=used_phone, u=uid: self._log(f"Added {u} via {p}"))
                    await asyncio.sleep(random_delay(3, 8))
                except Exception:
                    skipped += 1
                    processed_ids.add(uid)

            if rush_mode and processed_ids:
                remaining = [r for r in rows if r.get("ID", "") not in processed_ids]
                write_members_csv_atomic(self.config.members_csv, remaining)

            self._post(lambda: self._log(f"Adder selesai. added={added}, skipped={skipped}"))

        self._run_async_job(_job())

    @staticmethod
    def _classify_grup_scrapper_chat(ch) -> str | None:
        cls_name = type(ch).__name__
        if cls_name in {"ChatForbidden", "ChannelForbidden"}:
            return None
        if cls_name == "Chat":
            return "group"
        if getattr(ch, "megagroup", False):
            return "group"
        if getattr(ch, "broadcast", False):
            return "channel"
        return None

    def _grup_scrapper_row_values(self, r: dict) -> tuple:
        title = r.get("title", "") or "(no title)"
        markers = []
        if r.get("verified"):
            markers.append("[v]")
        if r.get("scam"):
            markers.append("[!scam]")
        if markers:
            title = " ".join(markers) + " " + title

        type_text = {
            "group": "Group",
            "channel": "Channel",
        }.get(r.get("type"), "Other")

        username = (r.get("username") or "").strip()
        username_text = f"@{username}" if username else "(private/no username)"

        members = r.get("members")
        if isinstance(members, int) and members >= 0:
            members_text = f"{members:,}"
        else:
            members_text = "?"

        return (title, type_text, username_text, members_text, r.get("status", ""))

    def _set_grup_scrapper_results(self, rows: list[dict], used_phone: str, query: str) -> None:
        self.grup_scrapper_results = rows
        self.grup_scrapper_tree.delete(*self.grup_scrapper_tree.get_children())
        self._grup_scrapper_index_by_iid = {}
        for r in rows:
            iid = self.grup_scrapper_tree.insert("", tk.END, values=self._grup_scrapper_row_values(r))
            r["_iid"] = iid
            self._grup_scrapper_index_by_iid[iid] = r

        joined = sum(1 for r in rows if r.get("joined"))
        groups = sum(1 for r in rows if r.get("type") == "group")
        channels = sum(1 for r in rows if r.get("type") == "channel")
        self.grup_scrapper_stats_var.set(
            f"Hasil: {len(rows)} (group={groups}, channel={channels}, joined={joined}) "
            f"via {used_phone}, query='{query}'"
        )

    def _refresh_grup_scrapper_row(self, r: dict) -> None:
        iid = r.get("_iid")
        if iid and self.grup_scrapper_tree.exists(iid):
            self.grup_scrapper_tree.item(iid, values=self._grup_scrapper_row_values(r))

    def _clear_grup_scrapper_results(self) -> None:
        self.grup_scrapper_results = []
        self._grup_scrapper_index_by_iid = {}
        if hasattr(self, "grup_scrapper_tree"):
            self.grup_scrapper_tree.delete(*self.grup_scrapper_tree.get_children())
        if hasattr(self, "grup_scrapper_stats_var"):
            self.grup_scrapper_stats_var.set("Hasil: 0")

    def _on_grup_scrapper_row_double_click(self, _event=None) -> None:
        sel = self.grup_scrapper_tree.selection()
        if not sel:
            return
        item = self._grup_scrapper_index_by_iid.get(sel[0])
        if not item:
            return
        username = (item.get("username") or "").strip()
        link = f"https://t.me/{username}" if username else item.get("title", "")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(link)
            self._log(f"Link copied: {link}")
        except Exception:
            pass

    def _run_grup_scrapper_search(self) -> None:
        query = self.grup_scrapper_query.get().strip()
        password = self.grup_scrapper_password.get().strip()
        if not query:
            messagebox.showwarning("Input", "Keyword tidak boleh kosong")
            return
        if not password:
            messagebox.showwarning("Input", "Encryption Password wajib diisi")
            return

        try:
            limit = int((self.grup_scrapper_limit.get() or "50").strip())
        except ValueError:
            messagebox.showwarning("Input", "Limit harus berupa angka")
            return
        limit = max(1, min(100, limit))

        type_filter = self.grup_scrapper_type.get()
        skip_scam = bool(self.grup_scrapper_skip_scam.get()) if hasattr(self, "grup_scrapper_skip_scam") else True
        account_phone = self._parse_account_choice(self.grup_scrapper_account.get()) if hasattr(self, "grup_scrapper_account") else None
        if account_phone:
            self._log(f"Grup Scrapper search menggunakan akun terpilih: {mask_phone(account_phone)}")

        async def _job():
            async def _op(app, _phone: str):
                return await app.invoke(raw.functions.contacts.Search(q=query, limit=limit))

            result, used_phone = await self._execute_on_account(password, account_phone, _op)

            chats = list(getattr(result, "chats", []) or [])
            rows: list[dict] = []
            seen_ids: set[int] = set()
            for ch in chats:
                cid = getattr(ch, "id", None)
                if cid is None or cid in seen_ids:
                    continue
                seen_ids.add(cid)

                kind = self._classify_grup_scrapper_chat(ch)
                if kind is None:
                    continue

                if type_filter == "Group/Supergroup saja" and kind != "group":
                    continue
                if type_filter == "Channel saja" and kind != "channel":
                    continue

                username = (getattr(ch, "username", None) or "").strip()
                title = getattr(ch, "title", None) or "(no title)"
                participants_count = getattr(ch, "participants_count", None)
                verified = bool(getattr(ch, "verified", False))
                scam = bool(getattr(ch, "scam", False)) or bool(getattr(ch, "fake", False))
                joined = not bool(getattr(ch, "left", True))
                access_hash = getattr(ch, "access_hash", None)

                if joined:
                    status = "Joined"
                elif scam:
                    status = "Scam/Fake"
                else:
                    status = "Belum join"

                rows.append(
                    {
                        "id": str(cid),
                        "raw_id": int(cid),
                        "access_hash": access_hash,
                        "title": title,
                        "type": kind,
                        "username": username,
                        "members": participants_count if isinstance(participants_count, int) else None,
                        "verified": verified,
                        "scam": scam,
                        "joined": joined,
                        "skip_scam": skip_scam,
                        "status": status,
                        "_class": type(ch).__name__,
                    }
                )

            rows.sort(
                key=lambda r: (
                    0 if r["type"] == "group" else 1,
                    -(r.get("members") or 0),
                    r["title"].lower(),
                )
            )

            self._post(lambda r=rows, p=used_phone, q=query: self._set_grup_scrapper_results(r, p, q))
            self._post(
                lambda n=len(rows), q=query: self._log(
                    f"Grup Scrapper: ditemukan {n} grup/channel publik untuk '{q}'"
                )
            )

        self._run_async_job(_job())

    def _fetch_grup_scrapper_stats(self) -> None:
        password = self.grup_scrapper_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption Password wajib diisi")
            return

        if not self.grup_scrapper_results:
            messagebox.showinfo("Stats", "Belum ada hasil pencarian")
            return

        pending = [
            r for r in self.grup_scrapper_results
            if r.get("members") is None and (r.get("username") or "").strip()
        ]
        if not pending:
            messagebox.showinfo("Stats", "Semua item sudah punya member count")
            return

        account_phone = self._parse_account_choice(self.grup_scrapper_account.get()) if hasattr(self, "grup_scrapper_account") else None

        async def _job():
            async def _op(app, _phone: str):
                for it in pending:
                    try:
                        chat = await app.get_chat(it["username"])
                        members_count = getattr(chat, "members_count", None)
                        if isinstance(members_count, int):
                            it["members"] = members_count
                            self._post(lambda x=it: self._refresh_grup_scrapper_row(x))
                    except Exception as exc:
                        self._post(
                            lambda t=it["title"], e=exc: self._log(
                                f"Get stats {t} gagal: {type(e).__name__}: {e}"
                            )
                        )
                    await asyncio.sleep(0.4)
                return True

            await self._execute_on_account(password, account_phone, _op)
            self._post(lambda: self._log("Fetch member counts selesai"))

        self._run_async_job(_job())

    @staticmethod
    def _format_join_status(*, ok: int, fail: int, total: int) -> str:
        """Format kolom Status di Grup Scrapper saat multi-akun join.

        ``total`` = jumlah akun yang ikut join batch. ``ok``/``fail``
        = berapa akun yang sukses/gagal join target ini.

        Single-akun → tetap "Joined" / "Failed" (kompatibel dengan
        tampilan lama). Multi-akun → tampilkan rasio "Joined N/M",
        "Joined N/M, gagal G", atau "Failed (N/M)".
        """
        if total <= 1:
            if ok > 0:
                return "Joined"
            if fail > 0:
                return "Failed"
            return "Pending"
        if ok + fail == 0:
            return "Pending"
        if ok == total:
            return f"Joined ({ok}/{total})"
        if ok > 0 and fail > 0:
            return f"Joined {ok}/{total}, gagal {fail}"
        if ok > 0:
            return f"Joined {ok}/{total} (sebagian)"
        return f"Failed ({fail}/{total})"

    def _run_grup_scrapper_join(self, only_selected: bool) -> None:
        password = self.grup_scrapper_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption Password wajib diisi")
            return

        if not self.grup_scrapper_results:
            messagebox.showinfo("Join", "Belum ada hasil pencarian")
            return

        if only_selected:
            sel_iids = self.grup_scrapper_tree.selection()
            if not sel_iids:
                messagebox.showinfo("Join", "Tidak ada item yang dipilih di tabel")
                return
            targets = [self._grup_scrapper_index_by_iid[i] for i in sel_iids if i in self._grup_scrapper_index_by_iid]
        else:
            targets = list(self.grup_scrapper_results)

        skip_scam = bool(self.grup_scrapper_skip_scam.get()) if hasattr(self, "grup_scrapper_skip_scam") else True

        targets = [
            t for t in targets
            if not t.get("joined") and (t.get("username") or "").strip() and not (skip_scam and t.get("scam"))
        ]
        if not targets:
            messagebox.showinfo(
                "Join",
                "Tidak ada target yang bisa di-join (semua sudah join, tanpa username publik, atau di-skip karena scam).",
            )
            return

        try:
            delay_min = float((self.grup_scrapper_delay_min.get() or "5").strip())
            delay_max = float((self.grup_scrapper_delay_max.get() or "15").strip())
        except ValueError:
            messagebox.showwarning("Input", "Delay min/max harus berupa angka")
            return
        if delay_min < 0 or delay_max < delay_min:
            messagebox.showwarning("Input", "Delay tidak valid. min >= 0 dan min <= max")
            return

        # Multi-account picker: user bisa centang banyak akun supaya
        # tiap akun ikut join semua target. Preselect dari combobox
        # `grup_scrapper_account` kalau user sudah pilih akun spesifik.
        default_phone = (
            self._parse_account_choice(self.grup_scrapper_account.get())
            if hasattr(self, "grup_scrapper_account")
            else None
        )
        picked_phones = self._pick_join_accounts(
            title="Pilih akun yang akan join",
            default_phone=default_phone,
        )
        if not picked_phones:
            return

        # Skip akun yang cooldown.
        cooled_down = [
            p for p in picked_phones
            if self.manager.get_cooldown_remaining(p) > 0
        ]
        active_phones = [p for p in picked_phones if p not in cooled_down]
        if not active_phones:
            messagebox.showwarning(
                "Akun",
                "Semua akun yang dipilih sedang cooldown. Pilih akun lain atau tunggu.",
            )
            return
        if cooled_down:
            cool_str = ", ".join(mask_phone(p) for p in cooled_down)
            if not messagebox.askyesno(
                "Akun Cooldown",
                (
                    f"Akun berikut sedang cooldown dan akan di-skip:\n  {cool_str}\n\n"
                    f"Lanjut dengan {len(active_phones)} akun lainnya?"
                ),
            ):
                return

        n_accounts = len(active_phones)
        accounts_label = ", ".join(mask_phone(p) for p in active_phones)
        total_ops = n_accounts * len(targets)

        if not messagebox.askyesno(
            "Konfirmasi Join",
            (
                f"Akan join {len(targets)} grup/channel dengan delay random "
                f"{delay_min:.1f}-{delay_max:.1f} detik.\n\n"
                f"Akun ({n_accounts}): {accounts_label}\n"
                f"Total operasi: {total_ops} join.\n\nLanjutkan?"
            ),
        ):
            return

        # Reset cumulative state untuk setiap target di batch ini.
        for t in targets:
            t["_join_ok_count"] = 0
            t["_join_fail_count"] = 0

        self._log(
            f"[Grup Scrapper] Join multi-akun: {n_accounts} akun × {len(targets)} grup "
            f"= {total_ops} operasi"
        )

        async def _job():
            op_idx = 0
            total_ok = 0
            total_fail = 0

            async def _join_once(app: Client, link: str) -> None:
                try:
                    await app.join_chat(link)
                except Exception as exc:
                    err = (str(exc) or "").upper()
                    if "USER_ALREADY_PARTICIPANT" in err or "ALREADY_PARTICIPANT" in err:
                        return
                    raise

            def _bump_status(item: dict) -> None:
                item["status"] = self._format_join_status(
                    ok=int(item.get("_join_ok_count", 0)),
                    fail=int(item.get("_join_fail_count", 0)),
                    total=n_accounts,
                )
                self._post(lambda it=item: self._refresh_grup_scrapper_row(it))

            for ai, phone in enumerate(active_phones):
                self._post(
                    lambda p=phone, ai=ai, total=n_accounts: self._log(
                        f"[Grup Scrapper] Akun ({ai + 1}/{total}) {mask_phone(p)}: connecting..."
                    )
                )
                try:
                    app = await self.manager.build_client(phone, password)
                    await app.connect()
                except Exception as exc:
                    self._post(
                        lambda p=phone, e=exc: self._log(
                            f"[Grup Scrapper] Akun {mask_phone(p)} connect gagal: "
                            f"{type(e).__name__}: {e}"
                        )
                    )
                    for t in targets:
                        t["_join_fail_count"] = int(t.get("_join_fail_count", 0)) + 1
                        total_fail += 1
                        _bump_status(t)
                    continue

                try:
                    for ti, item in enumerate(targets):
                        op_idx += 1
                        username = (item.get("username") or "").strip()
                        title = item.get("title", "")
                        if not username:
                            item["_join_fail_count"] = int(item.get("_join_fail_count", 0)) + 1
                            total_fail += 1
                            _bump_status(item)
                            continue

                        link = f"@{username}"
                        abort_account = False
                        try:
                            try:
                                await _join_once(app, link)
                            except FloodWait as fw:
                                wait = int(fw.value)
                                if wait >= 3600:
                                    self.manager.set_cooldown(phone=phone, seconds=wait)
                                    raise RuntimeError(
                                        f"Akun {mask_phone(phone)} kena FloodWait {wait}s; "
                                        "skip akun ini untuk sisa batch."
                                    ) from fw
                                self._post(
                                    lambda w=wait: self._log(
                                        f"[Grup Scrapper] FloodWait {w}s, retry setelah delay"
                                    )
                                )
                                await asyncio.sleep(wait + 2)
                                await _join_once(app, link)

                            item["_join_ok_count"] = int(item.get("_join_ok_count", 0)) + 1
                            item["joined"] = True  # min. 1 akun sukses → dianggap joined
                            total_ok += 1
                            _bump_status(item)
                            self._post(
                                lambda t=title, p=phone, n=op_idx, tot=total_ops: self._log(
                                    f"[Grup Scrapper] Joined {t} via {mask_phone(p)} "
                                    f"({n}/{tot})"
                                )
                            )
                        except Exception as exc:
                            item["_join_fail_count"] = int(item.get("_join_fail_count", 0)) + 1
                            total_fail += 1
                            _bump_status(item)
                            err_text = f"{type(exc).__name__}: {exc}"
                            self._post(
                                lambda t=title, p=phone, e=err_text: self._log(
                                    f"[Grup Scrapper] Join {t} via {mask_phone(p)} gagal: {e}"
                                )
                            )
                            if "cooldown" in err_text.lower() or "FloodWait" in err_text:
                                abort_account = True

                        if abort_account:
                            self._post(
                                lambda p=phone: self._log(
                                    f"[Grup Scrapper] Akun {mask_phone(p)} skip sisa target (cooldown)."
                                )
                            )
                            for rem_item in targets[ti + 1:]:
                                rem_item["_join_fail_count"] = int(rem_item.get("_join_fail_count", 0)) + 1
                                total_fail += 1
                                _bump_status(rem_item)
                            break

                        if ti < len(targets) - 1:
                            d = random_delay(delay_min, delay_max)
                            self._post(
                                lambda d=d: self._log(
                                    f"[Grup Scrapper] Sleep {d:.1f}s sebelum join berikutnya"
                                )
                            )
                            await asyncio.sleep(d)
                finally:
                    try:
                        await app.disconnect()
                    except Exception:
                        pass

                if ai < n_accounts - 1:
                    d = random_delay(delay_min, delay_max)
                    self._post(
                        lambda d=d: self._log(
                            f"[Grup Scrapper] Sleep {d:.1f}s sebelum akun berikutnya"
                        )
                    )
                    await asyncio.sleep(d)

            summary = (
                f"Grup Scrapper join selesai: ok={total_ok}, gagal={total_fail}, "
                f"total ops={total_ops}, akun={n_accounts}, target={len(targets)}"
            )
            self._post(lambda s=summary: self._log(s))
            self._post(lambda s=summary: messagebox.showinfo("Join Result", s))

        self._run_async_job(_job())

    def _export_grup_scrapper_csv(self) -> None:
        if not self.grup_scrapper_results:
            messagebox.showinfo("Export", "Belum ada hasil untuk diekspor")
            return

        path = filedialog.asksaveasfilename(
            title="Save Grup Scrapper CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile="grup_scrapper.csv",
        )
        if not path:
            return

        import csv as _csv
        headers = ["Title", "Type", "Username", "Link", "Members", "Status", "Verified", "Scam", "ID"]
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = _csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                for r in self.grup_scrapper_results:
                    username = (r.get("username") or "").strip()
                    link = f"https://t.me/{username}" if username else ""
                    writer.writerow(
                        {
                            "Title": r.get("title", ""),
                            "Type": r.get("type", ""),
                            "Username": username,
                            "Link": link,
                            "Members": r.get("members") if isinstance(r.get("members"), int) else "",
                            "Status": r.get("status", ""),
                            "Verified": "yes" if r.get("verified") else "",
                            "Scam": "yes" if r.get("scam") else "",
                            "ID": r.get("id", ""),
                        }
                    )
            messagebox.showinfo("Export", f"Saved {len(self.grup_scrapper_results)} rows to {path}")
            self._log(f"Grup Scrapper CSV saved to {path}")
        except Exception as exc:
            messagebox.showerror("Export", f"Gagal menyimpan: {exc}")

    def _browse_md(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose markdown file",
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")],
        )
        if path:
            self.broadcast_file.delete(0, tk.END)
            self.broadcast_file.insert(0, path)

    def _add_broadcast_attachments(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose attachments",
            filetypes=[
                ("Media and Documents", "*.png;*.jpg;*.jpeg;*.webp;*.gif;*.mp4;*.mov;*.mkv;*.avi;*.pdf;*.txt;*.doc;*.docx;*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        existing = set(self.broadcast_attachments)
        for p in paths:
            if p not in existing:
                self.broadcast_attachments.append(p)
                existing.add(p)
        self._refresh_attachment_box()

    def _remove_selected_broadcast_attachment(self) -> None:
        selected = list(self.broadcast_attachment_box.curselection())
        if not selected:
            return
        for idx in reversed(selected):
            if 0 <= idx < len(self.broadcast_attachments):
                self.broadcast_attachments.pop(idx)
        self._refresh_attachment_box()

    def _clear_broadcast_attachments(self) -> None:
        self.broadcast_attachments = []
        self._refresh_attachment_box()

    def _refresh_attachment_box(self) -> None:
        if not hasattr(self, "broadcast_attachment_box"):
            return
        self.broadcast_attachment_box.delete(0, tk.END)
        for p in self.broadcast_attachments:
            self.broadcast_attachment_box.insert(tk.END, Path(p).name)

    def _build_broadcast_html(self) -> str:
        direct_text = self.broadcast_text.get("1.0", tk.END).strip() if hasattr(self, "broadcast_text") else ""
        md_path = self.broadcast_file.get().strip()

        if direct_text:
            content = direct_text
        elif md_path and Path(md_path).exists():
            content = Path(md_path).read_text(encoding="utf-8")
        else:
            content = ""

        links_raw = self.broadcast_links.get("1.0", tk.END).strip() if hasattr(self, "broadcast_links") else ""
        links = [ln.strip() for ln in links_raw.splitlines() if ln.strip()]
        if links:
            link_block = "\n\n" + "\n".join(links)
            content = (content + link_block).strip()

        return self._md_to_html(content)

    def _get_broadcast_text_and_links(self) -> tuple[str, list[str]]:
        direct_text = self.broadcast_text.get("1.0", tk.END).strip() if hasattr(self, "broadcast_text") else ""
        links_raw = self.broadcast_links.get("1.0", tk.END).strip() if hasattr(self, "broadcast_links") else ""
        links = [ln.strip() for ln in links_raw.splitlines() if ln.strip()]
        return direct_text, links

    def _resolve_selected_ids(self, selected_only: bool, selected_indices: tuple[int, ...]) -> set[str] | None:
        if not selected_only:
            return None

        manual_count = len(self._parse_manual_targets()) if hasattr(self, "broadcast_manual_targets") else 0
        if not selected_indices:
            # selected-only applies to scraped members; manual targets are allowed without list selection.
            if manual_count > 0:
                return set()
            raise RuntimeError("Pilih member dulu di daftar Broadcast atau nonaktifkan mode selected-only")

        selected_ids: set[str] = set()
        for idx in selected_indices:
            if 0 <= idx < len(self.broadcast_filtered_indices):
                src_idx = self.broadcast_filtered_indices[idx]
                uid = (self.broadcast_rows[src_idx].get("ID") or "").strip()
                if uid:
                    selected_ids.add(uid)

        if not selected_ids and manual_count > 0:
            return set()
        return selected_ids

    def _build_broadcast_preview(
        self,
        recipients_count: int,
        direct_text: str,
        links: list[str],
        attachments: list[str],
        at_risk_ids: list[str] | None = None,
    ) -> str:
        lines = [
            "Konfirmasi Broadcast",
            "",
            f"Recipients: {recipients_count}",
            f"Attachments: {len(attachments)}",
            f"Links: {len(links)}",
            "",
        ]

        at_risk_ids = at_risk_ids or []
        if at_risk_ids:
            lines.append(
                f"WARN: {len(at_risk_ids)} target adalah numeric ID tanpa @username & tanpa access_hash."
            )
            lines.append(
                "Telegram tidak bisa kirim ke ID yang belum pernah dikenal akun ini."
            )
            lines.append(
                "Ini akan di-probe via group Anda (cap 500); yang tetap tidak ketemu akan FAIL."
            )
            lines.append("Tip: pakai @username, atau scrape group berisi user ini dulu.")
            lines.append("ID berisiko (5 pertama):")
            for rid in at_risk_ids[:5]:
                lines.append(f"- {rid}")
            if len(at_risk_ids) > 5:
                lines.append(f"- ... (+{len(at_risk_ids) - 5} more)")
            lines.append("")

        if direct_text:
            compact = re.sub(r"\s+", " ", direct_text).strip()
            if len(compact) > 220:
                compact = compact[:220] + "..."
            lines.append("Text preview:")
            lines.append(compact)
            lines.append("")

        if links:
            lines.append("Link preview:")
            for ln in links[:5]:
                lines.append(f"- {ln}")
            if len(links) > 5:
                lines.append(f"- ... (+{len(links) - 5} more)")
            lines.append("")

        if attachments:
            lines.append("Attachment preview:")
            for p in attachments[:5]:
                lines.append(f"- {Path(p).name}")
            if len(attachments) > 5:
                lines.append(f"- ... (+{len(attachments) - 5} more)")
            lines.append("")

        lines.append("Lanjut kirim sekarang?")
        return "\n".join(lines)

    @staticmethod
    def _is_image_file(path: str) -> bool:
        return Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    @staticmethod
    def _is_video_file(path: str) -> bool:
        return Path(path).suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}

    @staticmethod
    def _html_to_plain_text(text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text or "").strip()
        return html.unescape(text)

    async def _send_broadcast_payload(self, app, chat_target, html: str, attachments: list[str]) -> None:
        html = (html or "").strip()
        html_for_caption = html[:1024] if html else ""
        plain_caption = self._html_to_plain_text(html_for_caption) if html_for_caption else ""

        if not attachments:
            if not html:
                raise RuntimeError("Konten broadcast kosong")
            await app.send_message(chat_target, html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            return

        if len(attachments) == 1:
            path = attachments[0]
            if html and len(html) > 1024:
                await app.send_message(chat_target, html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                html_for_caption = ""
                plain_caption = ""

            try:
                if self._is_image_file(path):
                    await app.send_photo(
                        chat_target,
                        path,
                        caption=html_for_caption or None,
                        parse_mode=ParseMode.HTML if html_for_caption else None,
                    )
                elif self._is_video_file(path):
                    await app.send_video(
                        chat_target,
                        path,
                        caption=html_for_caption or None,
                        parse_mode=ParseMode.HTML if html_for_caption else None,
                    )
                else:
                    await app.send_document(
                        chat_target,
                        path,
                        caption=html_for_caption or None,
                        parse_mode=ParseMode.HTML if html_for_caption else None,
                    )
            except AttributeError as exc:
                if "is_premium" not in str(exc):
                    raise
                # Fallback for Pyrogram parser bug observed on media captions.
                safe_caption = plain_caption or None
                if self._is_image_file(path):
                    await app.send_photo(chat_target, path, caption=safe_caption)
                elif self._is_video_file(path):
                    await app.send_video(chat_target, path, caption=safe_caption)
                else:
                    await app.send_document(chat_target, path, caption=safe_caption)
            return

        if html and len(html) > 1024:
            await app.send_message(chat_target, html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            html_for_caption = ""

        media = []
        for idx, path in enumerate(attachments):
            caption = html_for_caption if idx == 0 and html_for_caption else None
            if self._is_image_file(path):
                media.append(InputMediaPhoto(path, caption=caption, parse_mode=ParseMode.HTML if caption else None))
            elif self._is_video_file(path):
                media.append(InputMediaVideo(path, caption=caption, parse_mode=ParseMode.HTML if caption else None))
            else:
                media.append(InputMediaDocument(path, caption=caption, parse_mode=ParseMode.HTML if caption else None))

        try:
            await app.send_media_group(chat_target, media)
        except AttributeError as exc:
            if "is_premium" not in str(exc):
                raise
            media_plain = []
            for idx, path in enumerate(attachments):
                caption = plain_caption if idx == 0 and plain_caption else None
                if self._is_image_file(path):
                    media_plain.append(InputMediaPhoto(path, caption=caption))
                elif self._is_video_file(path):
                    media_plain.append(InputMediaVideo(path, caption=caption))
                else:
                    media_plain.append(InputMediaDocument(path, caption=caption))
            await app.send_media_group(chat_target, media_plain)

    async def _send_broadcast_payload_input_user(self, app, user_id: int, access_hash: int, html_text: str, attachments: list[str]) -> None:
        """Raw fallback kirim ke peer ID-only via InputPeerUser.

        Dipakai sebagai jaring pengaman saat `fetch_peers` gagal mempopulate
        peer storage Pyrogram (jarang, biasanya akun benar-benar tidak punya
        akses ke user tsb). Mendukung:
          - text saja → `messages.SendMessage`
          - 1+ attachment → upload via `app.save_file` lalu
            `messages.SendMedia` per attachment dengan caption hanya di
            attachment pertama (mengikuti perilaku UI).
        """
        peer = raw.types.InputPeerUser(user_id=user_id, access_hash=access_hash)
        plain_text = re.sub(r"<[^>]+>", "", html_text or "").strip()
        plain_text = html.unescape(plain_text)

        if not attachments:
            if not plain_text:
                raise RuntimeError("Konten text kosong untuk fallback InputUser")
            await app.invoke(
                raw.functions.messages.SendMessage(
                    peer=peer,
                    message=plain_text,
                    random_id=random.randint(1, 2_147_483_647),
                    no_webpage=True,
                )
            )
            return

        # Caption hanya di attachment pertama (1024 char limit).
        first_caption = (plain_text[:1024] or "") if plain_text else ""
        long_text_remainder = (
            plain_text if plain_text and len(plain_text) > 1024 else ""
        )

        if long_text_remainder:
            # Kirim dulu text panjang sebagai message terpisah agar tidak
            # terpotong sebagai caption.
            await app.invoke(
                raw.functions.messages.SendMessage(
                    peer=peer,
                    message=long_text_remainder,
                    random_id=random.randint(1, 2_147_483_647),
                    no_webpage=True,
                )
            )
            first_caption = ""

        for idx, path in enumerate(attachments):
            caption = first_caption if idx == 0 else ""
            try:
                uploaded = await app.save_file(path)
            except Exception as exc:
                raise RuntimeError(
                    f"Gagal upload {os.path.basename(path)} ke Telegram: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            file_name = os.path.basename(path)
            if self._is_image_file(path):
                media = raw.types.InputMediaUploadedPhoto(file=uploaded)
            elif self._is_video_file(path):
                media = raw.types.InputMediaUploadedDocument(
                    file=uploaded,
                    mime_type="video/mp4",
                    attributes=[
                        raw.types.DocumentAttributeFilename(file_name=file_name),
                    ],
                )
            else:
                mime = "application/octet-stream"
                try:
                    guess = app.guess_mime_type(path)
                    if guess:
                        mime = guess
                except Exception:
                    pass
                media = raw.types.InputMediaUploadedDocument(
                    file=uploaded,
                    mime_type=mime,
                    attributes=[
                        raw.types.DocumentAttributeFilename(file_name=file_name),
                    ],
                )

            await app.invoke(
                raw.functions.messages.SendMedia(
                    peer=peer,
                    media=media,
                    message=caption,
                    random_id=random.randint(1, 2_147_483_647),
                )
            )

    async def _resolve_access_hash(self, app, user_id: int) -> int | None:
        try:
            peer = await app.resolve_peer(user_id)
            if isinstance(peer, raw.types.InputPeerUser):
                return int(peer.access_hash)
        except Exception:
            return None
        return None

    async def _resolve_access_hash_with_hints(self, app, user_id: int, group_id_raw: str) -> int | None:
        resolved = await self._resolve_access_hash(app, user_id)
        if resolved is not None:
            return resolved

        try:
            maybe_user = await app.get_users(user_id)
            if maybe_user:
                resolved = await self._resolve_access_hash(app, user_id)
                if resolved is not None:
                    return resolved
        except Exception:
            pass

        if group_id_raw and group_id_raw.lstrip("-").isdigit():
            group_id = int(group_id_raw)

            try:
                member = await app.get_chat_member(group_id, user_id)
                if member and getattr(member, "user", None):
                    resolved = await self._resolve_access_hash(app, user_id)
                    if resolved is not None:
                        return resolved
            except Exception:
                pass

            try:
                scanned = 0
                async for member in app.get_chat_members(group_id, filter=ChatMembersFilter.SEARCH):
                    scanned += 1
                    u = getattr(member, "user", None)
                    if not u:
                        if scanned >= 300:
                            break
                        continue

                    if int(u.id) == int(user_id):
                        resolved = await self._resolve_access_hash(app, user_id)
                        if resolved is not None:
                            return resolved
                        break

                    if scanned >= 300:
                        break
            except Exception:
                pass

        # Last fallback: probe joined groups and try to resolve member by ID.
        # Higher cap than before so users with many groups still get a chance to find the target.
        try:
            checked = 0
            async for dialog in app.get_dialogs():
                chat = getattr(dialog, "chat", None)
                if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
                    continue
                checked += 1
                try:
                    member = await app.get_chat_member(chat.id, user_id)
                    if member and getattr(member, "user", None):
                        resolved = await self._resolve_access_hash(app, user_id)
                        if resolved is not None:
                            return resolved
                except Exception:
                    pass

                if checked >= 500:
                    break
        except Exception:
            pass

        return None

    def _enrich_manual_rows_with_known_data(self, manual_rows: list[dict], known_rows: list[dict]) -> list[dict]:
        if not manual_rows:
            return []

        by_id: dict[str, dict] = {}
        by_username: dict[str, dict] = {}
        for row in known_rows:
            rid = (row.get("ID") or "").strip()
            run = (row.get("Username") or "").strip().lower()
            if rid and rid not in by_id:
                by_id[rid] = row
            if run and run not in by_username:
                by_username[run] = row

        out: list[dict] = []
        for row in manual_rows:
            enriched = dict(row)
            rid = (enriched.get("ID") or "").strip()
            run = (enriched.get("Username") or "").strip().lower()

            match = None
            if rid:
                match = by_id.get(rid)
            elif run:
                match = by_username.get(run)

            if match:
                if not enriched.get("Username"):
                    enriched["Username"] = (match.get("Username") or "").strip()
                if not enriched.get("Access Hash"):
                    enriched["Access Hash"] = (match.get("Access Hash") or "").strip()
                if not enriched.get("Group ID"):
                    enriched["Group ID"] = (match.get("Group ID") or "").strip()
                if not enriched.get("Name") or enriched.get("Name") == "Manual Target":
                    enriched["Name"] = (match.get("Name") or "Manual Target").strip()

            out.append(enriched)

        return out

    def _run_broadcast(self) -> None:
        password = self.broadcast_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption password wajib diisi")
            return

        try:
            delay_min = float(self.broadcast_delay_min.get().strip()) if hasattr(self, "broadcast_delay_min") else 5.0
            delay_max = float(self.broadcast_delay_max.get().strip()) if hasattr(self, "broadcast_delay_max") else 20.0
        except Exception:
            messagebox.showwarning("Input", "Delay min/max harus berupa angka")
            return

        if delay_min < 0 or delay_max < 0 or delay_min > delay_max:
            messagebox.showwarning("Input", "Range delay tidak valid. Gunakan min <= max dan >= 0")
            return

        direct_text, links = self._get_broadcast_text_and_links()
        html_preview = self._build_broadcast_html()
        attachments = list(self.broadcast_attachments)
        if not html_preview and not attachments:
            messagebox.showwarning("Input", "Isi text/link atau tambahkan attachment dulu")
            return

        picked_rows_snapshot: list[dict] = list(self.broadcast_picked_rows) if hasattr(self, "broadcast_picked_rows") else []
        use_picked = bool(picked_rows_snapshot)

        selected_indices = tuple(self.broadcast_listbox.curselection()) if hasattr(self, "broadcast_listbox") else tuple()
        selected_only = bool(self.broadcast_selected_only.get()) if hasattr(self, "broadcast_selected_only") else False

        if use_picked:
            selected_ids: set[str] | None = None  # picked drives the recipient list directly
        else:
            try:
                selected_ids = self._resolve_selected_ids(selected_only, selected_indices)
            except Exception as exc:
                messagebox.showwarning("Broadcast", str(exc))
                return

        all_rows_preview = read_members_csv(self.config.members_csv)
        manual_rows_preview = self._build_manual_recipient_rows()
        manual_rows_preview = self._enrich_manual_rows_with_known_data(manual_rows_preview, all_rows_preview)
        if not all_rows_preview and not manual_rows_preview and not picked_rows_snapshot:
            messagebox.showwarning("Broadcast", "members.csv kosong")
            return

        if use_picked:
            preview_rows = list(picked_rows_snapshot)
        else:
            preview_rows = all_rows_preview
            if selected_ids is not None:
                preview_rows = [r for r in all_rows_preview if (r.get("ID") or "").strip() in selected_ids]
                if not preview_rows:
                    self._log_broadcast("Tidak ada member scrape yang terpilih; hanya manual targets yang akan dipakai jika ada")

        preview_rows = [{**row, "_source": "csv"} for row in preview_rows]
        preview_rows = self._merge_recipients(preview_rows, manual_rows_preview)
        if not preview_rows:
            messagebox.showwarning("Broadcast", "Tidak ada target broadcast setelah filter/manual targets")
            return

        # Pre-broadcast risk audit: numeric IDs without username and without access hash
        # cannot be reached by Telegram unless a probe finds them in your joined groups.
        at_risk_ids = [
            (row.get("ID") or "").strip()
            for row in preview_rows
            if (row.get("ID") or "").strip().isdigit()
            and not (row.get("Username") or "").strip()
            and not (row.get("Access Hash") or "").strip()
        ]
        confirm_text = self._build_broadcast_preview(
            recipients_count=len(preview_rows),
            direct_text=direct_text,
            links=links,
            attachments=attachments,
            at_risk_ids=at_risk_ids,
        )
        if not messagebox.askyesno("Confirm Broadcast", confirm_text):
            self._log_broadcast("Broadcast dibatalkan user")
            return

        if at_risk_ids:
            self._log_broadcast(
                f"WARN: {len(at_risk_ids)} target adalah numeric ID tanpa @username/access_hash; "
                "akan di-probe via dialog group (cap 500). Yang tetap gagal akan masuk failed."
            )

        broadcast_account_phone = (
            self._parse_account_choice(self.broadcast_account.get())
            if hasattr(self, "broadcast_account")
            else None
        )
        if broadcast_account_phone:
            self._log_broadcast(
                f"Broadcast akan menggunakan akun terpilih: {mask_phone(broadcast_account_phone)} (rotasi dimatikan)"
            )
        if use_picked:
            self._log_broadcast(
                f"Broadcast pakai Recipients list: {len(picked_rows_snapshot)} kontak (mode pick; selection di list scraping diabaikan)"
            )

        async def _job():
            html = self._build_broadcast_html()
            if len(html) > 4096:
                raise RuntimeError("Message lebih dari 4096 chars setelah konversi")

            all_rows = read_members_csv(self.config.members_csv)
            manual_rows = self._build_manual_recipient_rows()
            manual_rows = self._enrich_manual_rows_with_known_data(manual_rows, all_rows)
            if not all_rows and not manual_rows and not picked_rows_snapshot:
                raise RuntimeError("members.csv kosong dan manual targets juga kosong")

            if use_picked:
                rows = list(picked_rows_snapshot)
            else:
                rows = all_rows
                if selected_ids is not None:
                    rows = [r for r in all_rows if (r.get("ID") or "").strip() in selected_ids]
            rows = [{**row, "_source": "csv"} for row in rows]
            rows = self._merge_recipients(rows, manual_rows)
            if not rows:
                raise RuntimeError("Tidak ada target broadcast setelah filter/manual targets")

            done_ids: set[str] = set()
            sent = 0
            failed = 0
            processed = 0
            total = len(rows)

            self._post(lambda t=total, d1=delay_min, d2=delay_max: self._log_broadcast(f"Broadcast mulai. target={t}, delay={d1:.1f}-{d2:.1f}s"))
            self._post(lambda t=total: self._reset_broadcast_progress(t))

            # Optimasi: kalau pakai akun spesifik (rotasi dimatikan),
            # build & connect Client SEKALI di luar loop dan reuse untuk
            # semua iterasi. Ini menghilangkan handshake MTProto 3-10s
            # per recipient yang sebelumnya bikin startup broadcast lambat
            # (sama dengan fix Grup Scrapper join di PR #4).
            shared_app = None
            shared_phone: str | None = None
            shared_aborted = False  # set true kalau akun terblok & no rotasi
            burned_phones: set[str] = set()  # phones yang kena PEER_FLOOD/FW long
            auto_rotate_on_block = (
                bool(self.broadcast_auto_rotate_on_block.get())
                if hasattr(self, "broadcast_auto_rotate_on_block")
                else False
            )
            # Pool koneksi untuk Auto rotation mode (broadcast_account_phone=None).
            # Di mode rotasi, sebelumnya tiap iterasi build_client+connect+disconnect
            # akun yang dipilih → MTProto handshake 3-10s per recipient. Pool ini
            # cache app per-phone: koneksi pertama tiap akun bayar handshake sekali,
            # iterasi berikutnya pakai akun yang sama instan. Disconnect semua di
            # akhir _job(). Mirror dari fix shared_app (PR #14) tapi multi-akun.
            clients_pool: dict[str, "Client"] = {}
            rotation_excluded: set[str] = set()  # cooldown / connect failure
            if broadcast_account_phone:
                try:
                    shared_app = await self.manager.build_client(
                        broadcast_account_phone, password
                    )
                    await shared_app.connect()
                    shared_phone = broadcast_account_phone
                except Exception as exc:
                    self._post(
                        lambda e=exc: self._log_broadcast(
                            f"Gagal connect ke akun terpilih: {type(e).__name__}: {e}"
                        )
                    )
                    raise

            async def _try_rotate_to_next_account() -> bool:
                """Switch shared_app/shared_phone ke akun login lain yang
                tidak sedang cooldown & belum burned di sesi ini.

                Returns True kalau berhasil pindah & connect, False kalau
                tidak ada kandidat / gagal connect ke semuanya.
                """
                nonlocal shared_app, shared_phone
                while True:
                    next_phone = self.manager.get_next_phone(exclude=burned_phones)
                    if not next_phone:
                        self._post(
                            lambda: self._log_broadcast(
                                "Tidak ada akun lain yang tersedia untuk rotasi; broadcast dihentikan."
                            )
                        )
                        return False
                    # Tutup koneksi lama dulu (kalau ada).
                    if shared_app is not None:
                        try:
                            await shared_app.disconnect()
                        except Exception:
                            pass
                        shared_app = None
                    try:
                        new_app = await self.manager.build_client(next_phone, password)
                        await new_app.connect()
                        shared_app = new_app
                        shared_phone = next_phone
                        self._post(
                            lambda p=next_phone: self._log_broadcast(
                                f"Rotasi ke akun {mask_phone(p)}; lanjut broadcast."
                            )
                        )
                        return True
                    except Exception as exc:
                        # Akun ini gagal connect (mis. session corrupt) —
                        # skip & coba kandidat berikut.
                        burned_phones.add(next_phone)
                        self._post(
                            lambda p=next_phone, e=exc: self._log_broadcast(
                                f"Gagal connect ke akun rotasi {mask_phone(p)}: {type(e).__name__}: {e}; coba akun lain..."
                            )
                        )
                        continue

            async def _call_op_shared(op):
                """Run op pada shared_app (single-account mode).

                FloodWait < 1h → sleep + retry sekali pada app yang sama.
                FloodWait >= 1h atau PEER_FLOOD → set cooldown:
                  - Kalau auto_rotate_on_block aktif: pindah ke akun lain
                    yang available, retry row yang sama.
                  - Kalau tidak: tandai shared_aborted & raise, broadcast
                    berhenti (sisa target di-mark failed cepat tanpa
                    mencoba kirim).
                """
                nonlocal shared_aborted
                while True:
                    try:
                        result = await op(shared_app, shared_phone)
                        return result, shared_phone
                    except FloodWait as fw:
                        wait = int(fw.value)
                        if wait >= 3600:
                            self.manager.set_cooldown(
                                phone=shared_phone, seconds=wait
                            )
                            burned_phones.add(shared_phone)
                            self._post(
                                lambda p=shared_phone, w=wait: self._log_broadcast(
                                    f"Akun {mask_phone(p)} kena FloodWait {w}s; cooldown dipasang."
                                )
                            )
                            if auto_rotate_on_block:
                                if await _try_rotate_to_next_account():
                                    continue  # retry row pada akun baru
                            shared_aborted = True
                            raise RuntimeError(
                                f"Akun {mask_phone(shared_phone)} kena FloodWait {wait}s; cooldown dipasang. "
                                "Broadcast dihentikan; centang 'Auto-rotate akun' atau pilih akun lain."
                            ) from fw
                        await asyncio.sleep(wait + 2)
                        # retry on same app — loop continues
                        continue
                    except PeerFlood as pf:
                        # Telegram mendeteksi spam; akun di-rate-limit
                        # secara global (biasanya 2-24 jam). Set cooldown
                        # 2 jam (sama default dengan execute_with_rotation).
                        self.manager.set_cooldown(
                            phone=shared_phone, seconds=7200
                        )
                        burned_phones.add(shared_phone)
                        self._post(
                            lambda p=shared_phone: self._log_broadcast(
                                f"Akun {mask_phone(p)} kena PEER_FLOOD; cooldown 2 jam dipasang."
                            )
                        )
                        if auto_rotate_on_block:
                            if await _try_rotate_to_next_account():
                                continue  # retry row pada akun baru
                        shared_aborted = True
                        raise RuntimeError(
                            f"Akun {mask_phone(shared_phone)} kena PEER_FLOOD; broadcast dihentikan. "
                            "Centang 'Auto-rotate akun' atau pilih akun lain."
                        ) from pf

            async def _get_or_build_pooled(phone: str):
                """Ambil app dari pool kalau ada, atau build+connect baru.

                Kalau gagal connect (mis. session corrupt), tandai
                `rotation_excluded` agar tidak dipilih ulang.
                """
                if phone in clients_pool:
                    return clients_pool[phone]
                try:
                    app = await self.manager.build_client(phone, password)
                    await app.connect()
                    clients_pool[phone] = app
                    return app
                except Exception as exc:
                    rotation_excluded.add(phone)
                    self._post(
                        lambda p=phone, e=exc: self._log_broadcast(
                            f"Gagal connect ke akun {mask_phone(p)}: {type(e).__name__}: {e}; skip akun ini."
                        )
                    )
                    return None

            async def _call_op_rotation_pooled(op, target_label: str = ""):
                """Auto rotation mode dengan koneksi pool.

                Mirror `execute_with_rotation` tapi reuse koneksi yang
                sudah dibangun (bukan disconnect tiap iterasi). Round-robin
                via `manager.get_next_phone(exclude=rotation_excluded)`.

                Penanganan exception:
                  - FloodWait >= 1h / PeerFlood: set cooldown akun,
                    exclude permanen sesi ini, disconnect+pop dari pool,
                    lanjut akun lain.
                  - FloodWait < 1h: sleep + retry pada akun yang sama.
                  - PeerIdInvalid: akun ini tidak kenal target (umum
                    untuk target ID-only kalau access_hash CSV di-scrape
                    pakai akun lain — access_hash di Telegram bersifat
                    per-akun). Exclude akun ini SEMENTARA hanya untuk
                    target ini, lanjut coba akun lain.
                """
                # Exclude sementara hanya untuk target sekarang — di-reset
                # tiap kali fungsi dipanggil.
                pid_invalid_tried: set[str] = set()
                for _ in range(50):
                    phone = self.manager.get_next_phone(
                        exclude=rotation_excluded | pid_invalid_tried
                    )
                    if not phone:
                        # Semua akun sudah dicoba untuk target ini
                        if pid_invalid_tried and not rotation_excluded:
                            raise RuntimeError(
                                f"Tidak ada akun yang kenal target "
                                f"{target_label or '(ID-only)'} "
                                f"({len(pid_invalid_tried)} akun dicoba). "
                                "Akses hash di CSV mungkin di-scrape pakai akun "
                                "yang berbeda — akun broadcast sekarang tidak "
                                "punya hak kirim ke user ini. Solusi: pakai "
                                "@username, atau scrape ulang pakai akun yang "
                                "akan dipakai broadcast."
                            )
                        wait_s = self.manager.seconds_until_next_available()
                        raise RuntimeError(
                            "Tidak ada akun available untuk broadcast"
                            if wait_s <= 0
                            else f"Semua akun cooldown; tunggu ~{wait_s}s"
                        )
                    app = await _get_or_build_pooled(phone)
                    if app is None:
                        continue
                    try:
                        result = await op(app, phone)
                        return result, phone
                    except FloodWait as fw:
                        wait = int(fw.value)
                        if wait >= 3600:
                            self.manager.set_cooldown(phone=phone, seconds=wait)
                            rotation_excluded.add(phone)
                            bad = clients_pool.pop(phone, None)
                            if bad is not None:
                                try:
                                    await bad.disconnect()
                                except Exception:
                                    pass
                            self._post(
                                lambda p=phone, w=wait: self._log_broadcast(
                                    f"Akun {mask_phone(p)} kena FloodWait {w}s; "
                                    "cooldown dipasang. Rotasi ke akun lain..."
                                )
                            )
                            continue
                        await asyncio.sleep(wait + 2)
                        # retry pada akun yang sama (masih di pool)
                        try:
                            result = await op(app, phone)
                            return result, phone
                        except Exception:
                            # kalau retry tetap gagal, fall through ke
                            # exception handler luar (rotasi)
                            raise
                    except PeerFlood:
                        self.manager.set_cooldown(phone=phone, seconds=7200)
                        rotation_excluded.add(phone)
                        bad = clients_pool.pop(phone, None)
                        if bad is not None:
                            try:
                                await bad.disconnect()
                            except Exception:
                                pass
                        self._post(
                            lambda p=phone: self._log_broadcast(
                                f"Akun {mask_phone(p)} kena PEER_FLOOD; "
                                "cooldown 2h dipasang. Rotasi ke akun lain..."
                            )
                        )
                        continue
                    except PeerIdInvalid:
                        # Akun ini tidak kenal target (access_hash mismatch
                        # / belum pernah lihat user). Coba akun lain di pool
                        # — mungkin ada yang sudah punya peer ini di
                        # storage atau bisa resolve via groupnya.
                        pid_invalid_tried.add(phone)
                        self._post(
                            lambda p=phone, t=target_label: self._log_broadcast(
                                f"Akun {mask_phone(p)} tidak kenal target {t}; "
                                "coba akun lain..."
                            )
                        )
                        continue
                raise RuntimeError(
                    "Gagal selesaikan setelah rotasi banyak akun"
                )

            for row in rows:
                uid = row.get("ID", "").strip()
                username = (row.get("Username") or "").strip()
                access_hash_raw = (row.get("Access Hash") or "").strip()
                group_id_raw = (row.get("Group ID") or "").strip()
                raw_target = (row.get("Raw Target") or "").strip()
                source = (row.get("_source") or "csv").strip()
                chat_target = f"@{username}" if username else (uid or raw_target)
                display_target = f"@{username}" if username else (uid or raw_target)
                if not chat_target:
                    failed += 1
                    processed += 1
                    self._post(lambda p=processed, t=total, s=sent, f=failed: self._update_broadcast_progress(p, t, s, f))
                    continue
                try:
                    async def _op(app, _phone: str):
                        target = chat_target
                        if target.lstrip("-").isdigit():
                            target = int(target)

                        try:
                            await self._send_broadcast_payload(app, target, html, attachments)
                        except PeerIdInvalid as initial_pid:
                            # For ID-only recipients, prime peer cache using access hash then retry once.
                            if username:
                                raise
                            if not uid.isdigit():
                                raise

                            try:
                                access_hash: int | None = None
                                if access_hash_raw:
                                    access_hash = int(access_hash_raw)
                                else:
                                    access_hash = await self._resolve_access_hash_with_hints(app, int(uid), group_id_raw)

                                if access_hash is None:
                                    # Akun ini tidak punya access_hash. Re-raise
                                    # PeerIdInvalid agar `_call_op_rotation_pooled`
                                    # bisa coba akun lain yang mungkin kenal user
                                    # ini (mis. akun lain yang join group yang
                                    # sama). Fall back ke RuntimeError dengan
                                    # pesan jelas hanya kalau semua akun gagal.
                                    raise initial_pid

                                # Prime Pyrogram peer storage. `users.GetUsers`
                                # mengembalikan list `User` raw — kita HARUS
                                # passing ke `fetch_peers` agar tersimpan di
                                # storage; tanpa ini, retry `send_*` di bawah
                                # akan tetap kena PeerIdInvalid (bug yang
                                # terlihat saat manual targets numeric ID +
                                # attachment).
                                users_resp = await app.invoke(
                                    raw.functions.users.GetUsers(
                                        id=[raw.types.InputUser(user_id=int(uid), access_hash=access_hash)]
                                    )
                                )
                                if users_resp:
                                    try:
                                        await app.fetch_peers(users_resp)
                                    except Exception:
                                        pass

                                try:
                                    await self._send_broadcast_payload(app, int(uid), html, attachments)
                                except PeerIdInvalid:
                                    # Final fallback for ID-only peers: direct
                                    # raw send via InputPeerUser. Mendukung
                                    # text + attachment (foto/video/dokumen).
                                    await self._send_broadcast_payload_input_user(
                                        app=app,
                                        user_id=int(uid),
                                        access_hash=access_hash,
                                        html_text=html,
                                        attachments=attachments,
                                    )
                            except Exception:
                                raise

                        return True

                    if shared_aborted:
                        # Akun kena FloodWait long-cooldown; sisa target
                        # tidak akan terkirim — mark failed dan lanjut.
                        raise RuntimeError(
                            "Broadcast dihentikan: akun terblok (PEER_FLOOD/FW) & tidak ada rotasi."
                        )
                    if shared_app is not None:
                        _, used_phone = await _call_op_shared(_op)
                    else:
                        # Auto rotation mode: gunakan pool koneksi
                        # supaya akun yang sama tidak handshake ulang
                        # tiap iterasi. Lewatkan display_target supaya
                        # log fallback PeerIdInvalid jelas target apa.
                        _, used_phone = await _call_op_rotation_pooled(
                            _op, display_target
                        )
                    sent += 1
                    if source == "csv" and uid:
                        done_ids.add(uid)
                    processed += 1
                    self._post(
                        lambda p=used_phone, d=display_target, n=processed, t=total: self._log_broadcast(
                            f"Broadcast sent to {d} via {p} ({n}/{t})"
                        )
                    )
                    self._post(lambda p=processed, t=total, s=sent, f=failed: self._update_broadcast_progress(p, t, s, f))
                    if processed < total:
                        delay_s = random_delay(delay_min, delay_max)
                        self._post(lambda d=delay_s: self._log_broadcast(f"Sleep {d:.1f}s sebelum kirim berikutnya"))
                        await asyncio.sleep(delay_s)
                except Exception as exc:
                    failed += 1
                    processed += 1
                    if source == "csv" and uid:
                        done_ids.add(uid)
                    self._post(
                        lambda d=display_target, e=exc: self._log_broadcast(
                            f"Broadcast failed to {d}: {type(e).__name__}: {e}"
                        )
                    )
                    self._post(lambda p=processed, t=total, s=sent, f=failed: self._update_broadcast_progress(p, t, s, f))

            if done_ids:
                remaining = [r for r in all_rows if r.get("ID", "") not in done_ids]
                write_members_csv_atomic(self.config.members_csv, remaining)

            summary = f"Broadcast selesai. sent={sent}, failed={failed}, total={total}"
            self._post(lambda s=summary: self._log_broadcast(s))
            self._post(lambda s=summary: messagebox.showinfo("Broadcast Result", s))
            self._post(self._reload_broadcast_members)

            # Disconnect koneksi yang dipakai broadcast.
            # - shared_app: single-account mode (PR #14)
            # - clients_pool: rotation mode pool (commit ini)
            # MTProto handshake mahal (3-10s) — kita reuse 1 koneksi
            # per akun untuk semua iterasi, jadi cukup disconnect sekali
            # di akhir.
            if shared_app is not None:
                try:
                    await shared_app.disconnect()
                except Exception:
                    pass
            for pooled_app in list(clients_pool.values()):
                try:
                    await pooled_app.disconnect()
                except Exception:
                    pass
            clients_pool.clear()

        self._run_async_job(_job())

    @staticmethod
    def _format_cooldown(remaining_seconds: int) -> str:
        """Format detik tersisa cooldown jadi string yang mudah dibaca.

        Contoh:
          0    → "Active"
          59   → "Cooldown 59s"
          125  → "Cooldown 2m05s"
          7321 → "Cooldown 2h02m01s"
        """
        secs = int(remaining_seconds)
        if secs <= 0:
            return "Active"
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"Cooldown {h}h{m:02d}m{s:02d}s"
        if m > 0:
            return f"Cooldown {m}m{s:02d}s"
        return f"Cooldown {s}s"

    def _refresh_sessions_view(self, skip_pickers: bool = False) -> None:
        """Render ulang listbox akun di tab Sessions.

        Setiap baris di-color-code: hijau untuk akun Active, oranye
        untuk akun yang masih cooldown. Selection user (kalau ada)
        di-preserve agar klik Refresh / auto-tick tidak membatalkan
        baris yang sudah dipilih untuk "Hapus Akun Terpilih".

        ``skip_pickers=True`` dipakai oleh tick auto-refresh agar
        combobox akun (Login/Broadcast/Scrape) tidak ikut di-reset
        tiap 5 detik (mengganggu kalau dropdown sedang dibuka user).
        """
        sessions = self.manager.list_sessions()

        saved_selection = tuple(self.sessions_box.curselection())

        self.sessions_box.delete(0, tk.END)
        self.session_phones = []
        if not sessions:
            self.sessions_box.insert(tk.END, "Belum ada akun login tersimpan.")
            self.sessions_box.insert(tk.END, "Silakan login dari tab Login lalu klik Complete Login/QR Login.")
            self.session_phones = ["", ""]  # placeholder agar idx tetap sinkron
        else:
            ok_color = self.colors.get("ok", "#34d399")
            warn_color = self.colors.get("warn", "#f5b454")
            for sess in sessions:
                rem = int(self.manager.get_cooldown_remaining(sess.phone))
                status = self._format_cooldown(rem)
                idx = self.sessions_box.size()
                self.sessions_box.insert(
                    tk.END, f"{sess.phone} | {mask_phone(sess.phone)} | {status}"
                )
                self.session_phones.append(sess.phone)
                try:
                    self.sessions_box.itemconfig(
                        idx, foreground=(warn_color if rem > 0 else ok_color)
                    )
                except tk.TclError:
                    # Beberapa platform menolak itemconfig pada
                    # widget yang belum sepenuhnya ter-mapped; warna
                    # hanya kosmetik, lanjut tanpa color.
                    pass

        for sel in saved_selection:
            try:
                if isinstance(sel, int) and 0 <= sel < self.sessions_box.size():
                    self.sessions_box.selection_set(sel)
            except tk.TclError:
                pass

        if not skip_pickers:
            self._refresh_account_pickers()

    def _tick_sessions_cooldowns(self) -> None:
        """Tick periodik (~5 detik) untuk update countdown cooldown
        di tab Sessions.

        - Saat ada akun cooldown aktif: re-render listbox supaya
          countdown turun real-time.
        - Saat akun keluar cooldown (transisi cooldown→Active):
          tulis notifikasi ke Activity Log + re-render supaya
          warna baris berubah dari oranye ke hijau.
        - Saat semua akun Active: tidak re-render (hemat resources),
          tapi tick tetap dijadwalkan agar siap mendeteksi cooldown
          baru saat broadcast jalan.
        """
        self._sessions_tick_after_id = None
        try:
            sessions = self.manager.list_sessions()
            current_phones: set[str] = set()
            any_active_cooldown = False
            any_transition = False
            for sess in sessions:
                current_phones.add(sess.phone)
                rem = int(self.manager.get_cooldown_remaining(sess.phone))
                prev = int(self._prev_cooldown_state.get(sess.phone, 0))
                if prev > 0 and rem <= 0:
                    any_transition = True
                    try:
                        self._log(
                            f"Akun {mask_phone(sess.phone)} cooldown selesai, siap dipakai."
                        )
                    except Exception:
                        pass
                self._prev_cooldown_state[sess.phone] = rem
                if rem > 0:
                    any_active_cooldown = True

            # Buang entri prev untuk akun yang sudah dihapus user.
            for stale in list(self._prev_cooldown_state.keys()):
                if stale not in current_phones:
                    self._prev_cooldown_state.pop(stale, None)

            if any_active_cooldown or any_transition:
                self._refresh_sessions_view(skip_pickers=True)
        except Exception:
            # Tick error tidak boleh men-crash UI; coba lagi di tick
            # berikutnya. Akar masalah biasanya transient (mis. file
            # session sedang ditulis ulang).
            pass
        finally:
            try:
                self._sessions_tick_after_id = self.root.after(
                    5000, self._tick_sessions_cooldowns
                )
            except Exception:
                # root sudah destroyed (app closed). Berhenti.
                self._sessions_tick_after_id = None

    def _remove_selected_session(self) -> None:
        """Hapus session akun yang dipilih di listbox.

        Step:
          1. Validasi pilihan + password.
          2. Konfirmasi via dialog.
          3. Best-effort `app.log_out()` agar session di-invalidate di
             sisi Telegram (kalau gagal/sudah invalid, lanjut tetap
             hapus file lokal).
          4. Hapus file session via `manager.remove_session(phone)`.
          5. Refresh view.
        """
        selection = self.sessions_box.curselection()
        if not selection:
            messagebox.showinfo(
                "Hapus Akun",
                "Pilih satu akun di list dulu (klik baris yang mau dihapus).",
            )
            return
        idx = selection[0]
        if idx >= len(self.session_phones):
            return
        phone = self.session_phones[idx]
        if not phone:
            messagebox.showinfo("Hapus Akun", "Baris ini bukan akun (placeholder).")
            return

        password = self.sessions_password.get().strip()
        if not password:
            messagebox.showwarning(
                "Input",
                "Encryption password wajib diisi (untuk logout dari Telegram sebelum hapus).",
            )
            return

        if not messagebox.askyesno(
            "Konfirmasi Hapus Akun",
            f"Hapus akun {mask_phone(phone)}?\n\n"
            "Tindakan ini akan:\n"
            f"  - Logout dari Telegram (best-effort)\n"
            f"  - Menghapus file session lokal ({phone}.json)\n\n"
            "Untuk login ulang, gunakan tab Login.",
        ):
            return

        async def _job():
            # Best-effort logout — kalau gagal (mis. session sudah
            # invalid), tetap lanjut hapus file lokal supaya user
            # tidak stuck.
            logout_ok = False
            logout_err: str | None = None
            try:
                app = await self.manager.build_client(phone, password)
                await app.connect()
                try:
                    await app.log_out()
                    logout_ok = True
                except Exception as exc:
                    logout_err = f"{type(exc).__name__}: {exc}"
                finally:
                    try:
                        await app.disconnect()
                    except Exception:
                        pass
            except Exception as exc:
                logout_err = f"{type(exc).__name__}: {exc}"

            removed = self.manager.remove_session(phone)
            self._post(self._refresh_sessions_view)

            if logout_ok and removed:
                msg = f"OK {mask_phone(phone)}: logout Telegram + hapus file sukses."
            elif removed:
                msg = (
                    f"PARTIAL {mask_phone(phone)}: file dihapus, tapi logout gagal "
                    f"({logout_err}). Session mungkin masih aktif di sisi Telegram."
                )
            else:
                msg = f"GAGAL hapus {mask_phone(phone)}: file tidak ditemukan."
            self._post(lambda m=msg: self._log(m))

        self._run_async_job(_job())

    def _account_choices(self) -> list[str]:
        choices = [self.AUTO_ACCOUNT_LABEL]
        for sess in self.manager.list_sessions():
            choices.append(f"{sess.phone} | {mask_phone(sess.phone)}")
        return choices

    def _parse_account_choice(self, value: str) -> str | None:
        if not value or value == self.AUTO_ACCOUNT_LABEL:
            return None
        return value.split("|", 1)[0].strip() or None

    def _refresh_account_pickers(self) -> None:
        choices = self._account_choices()
        for combobox_name in (
            "scrape_account",
            "add_account",
            "broadcast_account",
            "grup_scrapper_account",
        ):
            cb = getattr(self, combobox_name, None)
            if cb is None:
                continue
            current = cb.get()
            cb.configure(values=choices)
            if current not in choices:
                cb.set(self.AUTO_ACCOUNT_LABEL)

        # Sync listbox akun di tab Account Manager juga.
        if hasattr(self, "account_manager_accounts"):
            try:
                self._account_manager_refresh_accounts()
            except Exception:
                pass

    # =========================================================================
    # Multi-account picker dialog (dipakai oleh Grup Scrapper join).
    # =========================================================================
    def _pick_join_accounts(
        self, *, title: str = "Pilih akun untuk join", default_phone: str | None = None
    ) -> list[str] | None:
        """Modal dialog untuk multi-select akun login.

        Return list phone yang dipilih user, atau ``None`` kalau dialog
        di-cancel. Saat user pilih "Auto" (kosongkan semua → semua akun
        masuk), return berisi SEMUA akun login (urut sesuai listing).

        ``default_phone``: kalau diset & ada di list, akan ter-preselect.
        Kalau tidak diset, semua akun akan ter-preselect (semua akun
        akan join).
        """
        sessions = self.manager.list_sessions()
        if not sessions:
            messagebox.showinfo("Akun", "Belum ada akun login. Login dulu lewat tab Login.")
            return None

        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.configure(bg=self.colors.get("bg", "#0b0f17"))

        ttk.Label(
            win,
            text="Centang akun yang akan ikut join (Ctrl/Shift untuk multi-select):",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        lb = tk.Listbox(
            win,
            height=min(16, max(4, len(sessions))),
            width=48,
            font=("Consolas", 10),
            selectmode=tk.MULTIPLE,
            exportselection=False,
        )
        lb.pack(fill=tk.BOTH, expand=True, padx=12)
        self._style_listbox_widget(lb)

        ok_color = self.colors.get("ok", "#34d399")
        warn_color = self.colors.get("warn", "#f5b454")
        for sess in sessions:
            rem = int(self.manager.get_cooldown_remaining(sess.phone))
            status = self._format_cooldown(rem)
            idx = lb.size()
            lb.insert(tk.END, f"{sess.phone} | {mask_phone(sess.phone)} | {status}")
            try:
                lb.itemconfig(idx, foreground=(warn_color if rem > 0 else ok_color))
            except tk.TclError:
                pass

        # Preselect: kalau default_phone diset → hanya itu; kalau tidak
        # → semua akun (mode Auto = semua akun ikut).
        if default_phone:
            for i, sess in enumerate(sessions):
                if sess.phone == default_phone:
                    lb.selection_set(i)
                    break
        else:
            lb.selection_set(0, tk.END)

        result: dict[str, list[str] | None] = {"phones": None}

        def _confirm() -> None:
            picked_idxs = list(lb.curselection())
            if not picked_idxs:
                messagebox.showwarning(
                    "Akun", "Pilih minimal satu akun (atau klik Cancel).",
                    parent=win,
                )
                return
            result["phones"] = [sessions[i].phone for i in picked_idxs]
            win.destroy()

        def _cancel() -> None:
            result["phones"] = None
            win.destroy()

        def _all() -> None:
            lb.selection_set(0, tk.END)

        def _none() -> None:
            lb.selection_clear(0, tk.END)

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=12, pady=12)
        ttk.Button(btns, text="Pilih Semua", command=_all).pack(side=tk.LEFT)
        ttk.Button(btns, text="Kosongkan", command=_none).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="OK", style="Accent.TButton", command=_confirm).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        win.bind("<Escape>", lambda _e: _cancel())
        win.bind("<Return>", lambda _e: _confirm())

        # Tunggu user close dialog.
        self.root.wait_window(win)
        return result["phones"]

    # =========================================================================
    # Account Manager tab: list grup yang sudah di-join per akun + leave.
    # =========================================================================
    def _account_manager_refresh_accounts(self) -> None:
        """Refresh listbox akun di tab Account Manager dari sessions."""
        if not hasattr(self, "account_manager_accounts"):
            return
        sessions = self.manager.list_sessions()
        self.account_manager_accounts.delete(0, tk.END)
        self._account_manager_phones = []
        ok_color = self.colors.get("ok", "#34d399")
        warn_color = self.colors.get("warn", "#f5b454")
        for sess in sessions:
            rem = int(self.manager.get_cooldown_remaining(sess.phone))
            status = self._format_cooldown(rem)
            idx = self.account_manager_accounts.size()
            self.account_manager_accounts.insert(
                tk.END, f"{mask_phone(sess.phone)} | {status}"
            )
            self._account_manager_phones.append(sess.phone)
            try:
                self.account_manager_accounts.itemconfig(
                    idx, foreground=(warn_color if rem > 0 else ok_color)
                )
            except tk.TclError:
                pass

    def _account_manager_selected_phones(self) -> list[str]:
        """Return list phone (full) untuk semua akun yang dipilih di
        listbox kiri tab Account Manager.
        """
        sel = self.account_manager_accounts.curselection()
        return [
            self._account_manager_phones[i]
            for i in sel
            if i < len(self._account_manager_phones)
        ]

    def _account_manager_select_all_accounts(self) -> None:
        self.account_manager_accounts.selection_set(0, tk.END)
        self._on_account_manager_account_pick()

    def _on_account_manager_account_pick(self, _event=None) -> None:
        """Saat user pilih akun (single/multi): merge cache dari semua
        akun terpilih dan render ke tree. Akun yang belum punya cache
        di-skip — user klik Load Groups untuk fetch.
        """
        phones = self._account_manager_selected_phones()
        # Simpan akun pertama yang dipilih sebagai current (untuk
        # default initial-file name di Export CSV).
        self._account_manager_current_phone = phones[0] if phones else None
        rows: list[dict] = []
        for p in phones:
            cached = self._account_manager_cache.get(p)
            if cached is not None:
                rows.extend(cached)
        self._account_manager_render_groups(rows)

    def _account_manager_render_groups(self, groups: list[dict]) -> None:
        """Render rows ke treeview kanan. Setiap dict groups harus
        berisi key 'phone' supaya kolom Akun bisa terisi.
        """
        for iid in self.account_manager_tree.get_children():
            self.account_manager_tree.delete(iid)
        self._account_manager_index_by_iid = {}
        for g in groups:
            phone = g.get("phone", "") or ""
            akun_label = mask_phone(phone) if phone else ""
            iid = self.account_manager_tree.insert(
                "",
                tk.END,
                values=(
                    akun_label,
                    g.get("title", ""),
                    g.get("type", ""),
                    g.get("username", ""),
                    g.get("members", ""),
                    g.get("id", ""),
                ),
            )
            self._account_manager_index_by_iid[iid] = g

    def _account_manager_load_selected(self) -> None:
        """Fetch list grup/channel yang sudah di-join dari SEMUA akun
        terpilih (multi-select), lalu render gabungan ke treeview.
        """
        phones = self._account_manager_selected_phones()
        if not phones:
            messagebox.showinfo(
                "Account Manager",
                "Pilih akun di list kiri dulu (Ctrl/Shift klik untuk multi-select, "
                "atau klik tombol 'Pilih Semua').",
            )
            return

        password = self.account_manager_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption password wajib diisi")
            return

        self._log(
            f"[Account Manager] Loading groups dari {len(phones)} akun (sequential)..."
        )

        async def _fetch_for_phone(phone: str) -> list[dict] | None:
            try:
                app = await self.manager.build_client(phone, password)
                await app.connect()
            except Exception as exc:
                self._post(
                    lambda p=phone, e=exc: self._log(
                        f"[Account Manager] Gagal connect {mask_phone(p)}: "
                        f"{type(e).__name__}: {e}"
                    )
                )
                return None
            groups: list[dict] = []
            try:
                async for dialog in app.get_dialogs():
                    chat = dialog.chat
                    if not chat or chat.type not in {
                        ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL,
                    }:
                        continue
                    type_label = "Group"
                    if chat.type == ChatType.SUPERGROUP:
                        type_label = "Supergroup"
                    elif chat.type == ChatType.CHANNEL:
                        type_label = "Channel"
                    title = chat.title or str(chat.id)
                    username = f"@{chat.username}" if chat.username else "(private)"
                    members = getattr(chat, "members_count", None)
                    groups.append({
                        "phone": phone,
                        "id": chat.id,
                        "title": title,
                        "type": type_label,
                        "username": username,
                        "members": members if members is not None else "-",
                    })
            except Exception as exc:
                self._post(
                    lambda p=phone, e=exc: self._log(
                        f"[Account Manager] Fetch error {mask_phone(p)}: "
                        f"{type(e).__name__}: {e}"
                    )
                )
            finally:
                try:
                    await app.disconnect()
                except Exception:
                    pass
            return groups

        async def _job():
            total_groups = 0
            for i, phone in enumerate(phones):
                self._post(
                    lambda p=phone, i=i, t=len(phones): self._log(
                        f"[Account Manager] ({i + 1}/{t}) {mask_phone(p)}: loading..."
                    )
                )
                groups = await _fetch_for_phone(phone)
                if groups is None:
                    continue
                self._account_manager_cache[phone] = groups
                total_groups += len(groups)
                self._post(
                    lambda p=phone, n=len(groups): self._log(
                        f"[Account Manager] {mask_phone(p)}: {n} grup/channel ter-load."
                    )
                )
                # Render incremental supaya user lihat progres tanpa
                # nunggu semua akun selesai.
                self._post(self._on_account_manager_account_pick)

            self._post(self._on_account_manager_account_pick)
            self._post(
                lambda n=total_groups, a=len(phones): self._log(
                    f"[Account Manager] Selesai. {n} row total dari {a} akun."
                )
            )

        self._run_async_job(_job())

    def _account_manager_leave_chats(self, chats: list[dict]) -> None:
        """Helper: leave list chats. ``chats`` adalah list dict yang
        SUDAH berisi key 'phone' (mengetahui akun mana yang harus
        leave grup mana). Grup dikelompokkan per akun supaya 1 koneksi
        per akun dipakai untuk seluruh leave-nya.
        """
        if not chats:
            messagebox.showinfo("Account Manager", "Tidak ada grup yang dipilih.")
            return

        password = self.account_manager_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption password wajib diisi")
            return

        # Kelompokkan chats per akun.
        by_phone: dict[str, list[dict]] = {}
        for c in chats:
            p = c.get("phone")
            if not p:
                continue
            by_phone.setdefault(p, []).append(c)

        if not by_phone:
            messagebox.showinfo(
                "Account Manager",
                "Row terpilih tidak punya info akun. Re-load groups dulu.",
            )
            return

        accounts_label = ", ".join(mask_phone(p) for p in by_phone.keys())
        if not messagebox.askyesno(
            "Konfirmasi Left",
            (
                f"Akan keluar dari {len(chats)} grup/channel via "
                f"{len(by_phone)} akun:\n  {accounts_label}\n\nLanjutkan?"
            ),
        ):
            return

        async def _job():
            total_ok = 0
            total_failed = 0
            for phone, items in by_phone.items():
                ok = 0
                failed = 0
                left_ids: set[int] = set()
                self._post(
                    lambda p=phone, n=len(items): self._log(
                        f"[Account Manager] Leaving {n} grup via {mask_phone(p)}..."
                    )
                )
                try:
                    app = await self.manager.build_client(phone, password)
                    await app.connect()
                except Exception as exc:
                    self._post(
                        lambda p=phone, e=exc: self._log(
                            f"[Account Manager] Connect error {mask_phone(p)}: "
                            f"{type(e).__name__}: {e}"
                        )
                    )
                    total_failed += len(items)
                    continue

                try:
                    for ch in items:
                        cid = ch.get("id")
                        title = ch.get("title", "")
                        if cid is None:
                            failed += 1
                            continue
                        try:
                            await app.leave_chat(cid)
                            ok += 1
                            left_ids.add(int(cid))
                            self._post(
                                lambda t=title, p=phone: self._log(
                                    f"[Account Manager] Left {t} via {mask_phone(p)}"
                                )
                            )
                        except Exception as exc:
                            failed += 1
                            self._post(
                                lambda t=title, p=phone, e=exc: self._log(
                                    f"[Account Manager] Gagal left {t} via "
                                    f"{mask_phone(p)}: {type(e).__name__}: {e}"
                                )
                            )
                        await asyncio.sleep(1.0)
                finally:
                    try:
                        await app.disconnect()
                    except Exception:
                        pass

                # Update cache untuk akun ini.
                cached = self._account_manager_cache.get(phone, [])
                new_cache = [
                    g for g in cached if int(g.get("id", 0)) not in left_ids
                ]
                self._account_manager_cache[phone] = new_cache
                total_ok += ok
                total_failed += failed
                self._post(
                    lambda p=phone, o=ok, f=failed: self._log(
                        f"[Account Manager] {mask_phone(p)} left selesai: ok={o}, gagal={f}"
                    )
                )

            self._post(self._on_account_manager_account_pick)
            summary = (
                f"Left selesai: ok={total_ok}, gagal={total_failed}, "
                f"akun={len(by_phone)}"
            )
            self._post(lambda s=summary: self._log(f"[Account Manager] {s}"))
            self._post(lambda s=summary: messagebox.showinfo("Left Result", s))

        self._run_async_job(_job())

    def _account_manager_leave_selected(self) -> None:
        sel_iids = self.account_manager_tree.selection()
        if not sel_iids:
            messagebox.showinfo(
                "Account Manager", "Pilih row grup yang akan di-left."
            )
            return
        chats = [
            self._account_manager_index_by_iid[i]
            for i in sel_iids
            if i in self._account_manager_index_by_iid
        ]
        self._account_manager_leave_chats(chats)

    def _account_manager_leave_all(self) -> None:
        chats = list(self._account_manager_index_by_iid.values())
        if not chats:
            messagebox.showinfo("Account Manager", "Daftar grup kosong.")
            return
        self._account_manager_leave_chats(chats)

    def _account_manager_export_csv(self) -> None:
        chats = list(self._account_manager_index_by_iid.values())
        if not chats:
            messagebox.showinfo("Account Manager", "Daftar grup kosong, tidak ada yang diekspor.")
            return
        # Default name: kalau multi-akun, pakai "multi"; kalau single, pakai phone.
        phones_in_view = {c.get("phone") for c in chats if c.get("phone")}
        if len(phones_in_view) == 1:
            phone = next(iter(phones_in_view))
            default_name = f"account_groups_{phone.replace('+', '')}.csv"
        else:
            default_name = f"account_groups_multi_{len(phones_in_view)}_akun.csv"
        path = filedialog.asksaveasfilename(
            title="Export Account Manager",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not path:
            return
        import csv as _csv
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = _csv.DictWriter(
                    f,
                    fieldnames=["Akun", "Title", "Type", "Username", "Members", "ID"],
                )
                writer.writeheader()
                for c in chats:
                    phone = c.get("phone", "") or ""
                    writer.writerow({
                        "Akun": mask_phone(phone) if phone else "",
                        "Title": c.get("title", ""),
                        "Type": c.get("type", ""),
                        "Username": c.get("username", ""),
                        "Members": c.get("members", ""),
                        "ID": c.get("id", ""),
                    })
            self._log(f"[Account Manager] Export ke {path} ({len(chats)} row)")
            messagebox.showinfo("Export", f"Berhasil disimpan ke:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export", f"Gagal simpan CSV: {exc}")

    def _pick_target_accounts_for_join(
        self,
        *,
        chats: list[dict],
        missing_per_phone: dict[str, list[dict]],
        unknown_phones: set[str],
    ) -> list[str] | None:
        """Dialog khusus untuk Account Manager → 'Joinkan ke Akun Lain'.

        Hanya menampilkan akun yang belum punya minimal 1 dari grup
        terpilih. Akun dilabel dengan jumlah grup yang missing supaya
        user tahu seberapa banyak yang akan di-join lewat akun itu.

        ``unknown_phones`` adalah akun yang belum di-load cache-nya;
        diberi badge khusus karena angka missing-nya cuma asumsi.
        """
        candidates = list(missing_per_phone.keys())
        if not candidates:
            messagebox.showinfo(
                "Akun",
                "Tidak ada akun kandidat — semua akun login sudah join semua grup terpilih.",
            )
            return None

        win = tk.Toplevel(self.root)
        win.title("Joinkan ke akun lain")
        win.transient(self.root)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.configure(bg=self.colors.get("bg", "#0b0f17"))

        ttk.Label(
            win,
            text=(
                f"Akun kandidat untuk join {len(chats)} grup terpilih "
                "(Ctrl/Shift untuk multi-select):"
            ),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        lb = tk.Listbox(
            win,
            height=min(16, max(4, len(candidates))),
            width=64,
            font=("Consolas", 10),
            selectmode=tk.MULTIPLE,
            exportselection=False,
        )
        lb.pack(fill=tk.BOTH, expand=True, padx=12)
        self._style_listbox_widget(lb)

        ok_color = self.colors.get("ok", "#34d399")
        warn_color = self.colors.get("warn", "#f5b454")
        for phone in candidates:
            rem = int(self.manager.get_cooldown_remaining(phone))
            status = self._format_cooldown(rem)
            missing_count = len(missing_per_phone.get(phone, []))
            if phone in unknown_phones:
                miss_label = f"belum di-load (max {missing_count}/{len(chats)})"
            else:
                miss_label = f"belum punya {missing_count}/{len(chats)} grup"
            idx = lb.size()
            lb.insert(
                tk.END,
                f"{mask_phone(phone)} | {status} | {miss_label}",
            )
            try:
                lb.itemconfig(idx, foreground=(warn_color if rem > 0 else ok_color))
            except tk.TclError:
                pass

        result: dict[str, list[str] | None] = {"phones": None}

        def _confirm() -> None:
            picked_idxs = list(lb.curselection())
            if not picked_idxs:
                messagebox.showwarning(
                    "Akun",
                    "Pilih minimal satu akun (atau klik Cancel).",
                    parent=win,
                )
                return
            result["phones"] = [candidates[i] for i in picked_idxs]
            win.destroy()

        def _cancel() -> None:
            result["phones"] = None
            win.destroy()

        def _all() -> None:
            lb.selection_set(0, tk.END)

        def _none() -> None:
            lb.selection_clear(0, tk.END)

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=12, pady=12)
        ttk.Button(btns, text="Pilih Semua", command=_all).pack(side=tk.LEFT)
        ttk.Button(btns, text="Kosongkan", command=_none).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text="OK", style="Accent.TButton", command=_confirm).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        win.bind("<Escape>", lambda _e: _cancel())
        win.bind("<Return>", lambda _e: _confirm())

        self.root.wait_window(win)
        return result["phones"]

    def _account_manager_join_to_other(self) -> None:
        """Joinkan grup terpilih (di tree) ke akun lain yang belum
        punya grup itu. Workflow:

        1. User pilih 1+ row di tree (boleh dari akun mana saja).
        2. Sistem hitung untuk tiap akun login: grup mana yang belum
           dia punya (berdasar cache yang sudah di-load).
        3. Tampilkan picker hanya akun yang masih missing minimal 1
           dari grup terpilih.
        4. User pilih akun target → tiap akun join grup yang belum dia
           punya. ``USER_ALREADY_PARTICIPANT`` di-anggap sukses (handle
           kasus akun yang belum di-load cache-nya).
        """
        sel_iids = self.account_manager_tree.selection()
        if not sel_iids:
            messagebox.showinfo(
                "Account Manager",
                "Pilih row grup di tree dulu (boleh multi-row).",
            )
            return
        selected_chats = [
            self._account_manager_index_by_iid[i]
            for i in sel_iids
            if i in self._account_manager_index_by_iid
        ]

        # Filter ke grup yang punya username publik (yang bisa di-join
        # via API tanpa invite link). Dedup by id supaya kalau user
        # pilih row yang sama (akun beda) cuma dihitung sekali.
        seen_ids: set[int] = set()
        joinable_chats: list[dict] = []
        skipped_titles: list[str] = []
        for c in selected_chats:
            cid = c.get("id")
            try:
                cid_int = int(cid) if cid is not None else 0
            except (TypeError, ValueError):
                cid_int = 0
            if cid_int and cid_int in seen_ids:
                continue
            username = (c.get("username") or "").strip()
            is_public = username.startswith("@") and len(username) > 1 and username != "(private)"
            if is_public:
                joinable_chats.append(c)
                if cid_int:
                    seen_ids.add(cid_int)
            else:
                skipped_titles.append(c.get("title", "") or str(cid))

        if not joinable_chats:
            messagebox.showwarning(
                "Account Manager",
                "Tidak ada grup yang punya username publik di selection.\n"
                "Grup private tidak bisa di-join via API (butuh invite link manual).",
            )
            return

        if skipped_titles:
            preview = "\n".join(f"  - {t}" for t in skipped_titles[:5])
            more = (
                ""
                if len(skipped_titles) <= 5
                else f"\n  ... +{len(skipped_titles) - 5} lainnya"
            )
            if not messagebox.askyesno(
                "Account Manager",
                f"{len(skipped_titles)} grup di-skip (private tanpa username):\n"
                f"{preview}{more}\n\n"
                f"Lanjut dengan {len(joinable_chats)} grup yang punya username?",
            ):
                return

        # Hitung missing per akun login.
        all_sessions = self.manager.list_sessions()
        if not all_sessions:
            messagebox.showinfo(
                "Account Manager", "Belum ada akun login."
            )
            return
        missing_per_phone: dict[str, list[dict]] = {}
        unknown_phones: set[str] = set()
        for sess in all_sessions:
            cached = self._account_manager_cache.get(sess.phone)
            if cached is None:
                # Belum di-load — anggap missing semua. USER_ALREADY_PARTICIPANT
                # akan di-handle sebagai sukses saat join.
                missing_per_phone[sess.phone] = list(joinable_chats)
                unknown_phones.add(sess.phone)
            else:
                joined_ids = {int(g.get("id", 0)) for g in cached}
                missing = [
                    c for c in joinable_chats
                    if int(c.get("id", 0)) not in joined_ids
                ]
                if missing:
                    missing_per_phone[sess.phone] = missing

        if not missing_per_phone:
            messagebox.showinfo(
                "Account Manager",
                "Semua akun login sudah join semua grup terpilih.",
            )
            return

        picked = self._pick_target_accounts_for_join(
            chats=joinable_chats,
            missing_per_phone=missing_per_phone,
            unknown_phones=unknown_phones,
        )
        if not picked:
            return

        password = self.account_manager_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption password wajib diisi")
            return

        # Final ops grouped per akun.
        ops_by_phone: dict[str, list[dict]] = {
            p: missing_per_phone[p] for p in picked if missing_per_phone.get(p)
        }
        total_ops = sum(len(v) for v in ops_by_phone.values())
        if total_ops == 0:
            messagebox.showinfo(
                "Account Manager",
                "Tidak ada operasi yang perlu dijalankan.",
            )
            return

        # Delay antar-join (hardcode 5-15s default, mirip Grup Scrapper).
        delay_min, delay_max = 5.0, 15.0

        if not messagebox.askyesno(
            "Konfirmasi Join",
            f"Akan join {total_ops} operasi ({len(ops_by_phone)} akun × beberapa grup) "
            f"dengan delay {delay_min:.0f}-{delay_max:.0f}s antar-join.\n\nLanjutkan?",
        ):
            return

        self._log(
            f"[Account Manager] Joinkan: {len(joinable_chats)} grup → "
            f"{len(ops_by_phone)} akun ({total_ops} ops)."
        )

        async def _job():
            total_ok = 0
            total_skipped = 0
            total_fail = 0
            for phone, chats_to_join in ops_by_phone.items():
                self._post(
                    lambda p=phone, n=len(chats_to_join): self._log(
                        f"[Account Manager] Joining {n} grup via {mask_phone(p)}..."
                    )
                )
                try:
                    app = await self.manager.build_client(phone, password)
                    await app.connect()
                except Exception as exc:
                    self._post(
                        lambda p=phone, e=exc: self._log(
                            f"[Account Manager] Gagal connect {mask_phone(p)}: "
                            f"{type(e).__name__}: {e}"
                        )
                    )
                    total_fail += len(chats_to_join)
                    continue

                newly_joined: list[dict] = []
                try:
                    for ci, ch in enumerate(chats_to_join):
                        username = (ch.get("username") or "").strip()
                        title = ch.get("title", "")
                        if not username.startswith("@"):
                            total_fail += 1
                            continue

                        try:
                            try:
                                await app.join_chat(username)
                            except FloodWait as fw:
                                wait = int(fw.value)
                                if wait >= 3600:
                                    self.manager.set_cooldown(phone=phone, seconds=wait)
                                    self._post(
                                        lambda p=phone, w=wait: self._log(
                                            f"[Account Manager] {mask_phone(p)} kena FloodWait "
                                            f"{w}s; cooldown dipasang, skip akun ini."
                                        )
                                    )
                                    total_fail += len(chats_to_join) - ci
                                    break
                                self._post(
                                    lambda w=wait: self._log(
                                        f"[Account Manager] FloodWait {w}s, retry..."
                                    )
                                )
                                await asyncio.sleep(wait + 2)
                                await app.join_chat(username)

                            total_ok += 1
                            newly_joined.append(dict(ch, phone=phone))
                            self._post(
                                lambda t=title, p=phone: self._log(
                                    f"[Account Manager] Joined {t} via {mask_phone(p)}"
                                )
                            )
                        except PeerFlood:
                            self.manager.set_cooldown(phone=phone, seconds=24 * 3600)
                            self._post(
                                lambda p=phone: self._log(
                                    f"[Account Manager] {mask_phone(p)} PEER_FLOOD; cooldown 24h, skip akun."
                                )
                            )
                            total_fail += len(chats_to_join) - ci
                            break
                        except Exception as exc:
                            err = str(exc).upper()
                            if (
                                "USER_ALREADY_PARTICIPANT" in err
                                or "ALREADY_PARTICIPANT" in err
                            ):
                                total_skipped += 1
                                newly_joined.append(dict(ch, phone=phone))
                                self._post(
                                    lambda t=title, p=phone: self._log(
                                        f"[Account Manager] Sudah join: {t} via {mask_phone(p)}"
                                    )
                                )
                            else:
                                total_fail += 1
                                self._post(
                                    lambda t=title, p=phone, e=exc: self._log(
                                        f"[Account Manager] Gagal join {t} via {mask_phone(p)}: "
                                        f"{type(e).__name__}: {e}"
                                    )
                                )

                        # Delay antar-join, kecuali di iterasi terakhir.
                        if ci < len(chats_to_join) - 1:
                            d = random_delay(delay_min, delay_max)
                            await asyncio.sleep(d)
                finally:
                    try:
                        await app.disconnect()
                    except Exception:
                        pass

                # Update cache: tambah grup baru yang berhasil di-join
                # (atau sudah di sana) supaya tidak ditawarkan lagi di
                # picker berikutnya.
                cached = list(self._account_manager_cache.get(phone, []))
                existing_ids = {int(g.get("id", 0)) for g in cached}
                for new_g in newly_joined:
                    try:
                        gid = int(new_g.get("id", 0))
                    except (TypeError, ValueError):
                        gid = 0
                    if gid and gid not in existing_ids:
                        cached.append(new_g)
                        existing_ids.add(gid)
                self._account_manager_cache[phone] = cached

            self._post(self._on_account_manager_account_pick)
            summary = (
                f"Join 'ke Akun Lain' selesai: ok={total_ok}, "
                f"sudah-ada={total_skipped}, gagal={total_fail}"
            )
            self._post(lambda s=summary: self._log(f"[Account Manager] {s}"))
            self._post(lambda s=summary: messagebox.showinfo("Join Result", s))

        self._run_async_job(_job())

    def _test_sessions(self) -> None:
        password = self.sessions_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption password wajib diisi")
            return

        async def _job():
            sessions = self.manager.list_sessions()
            if not sessions:
                self._post(lambda: self._log("No sessions to test"))
                return

            for sess in sessions:
                try:
                    app = await self.manager.build_client(sess.phone, password)
                    await app.connect()
                    me = await app.get_me()
                    await app.disconnect()
                    self._post(lambda p=sess.phone, uid=me.id: self._log(f"OK {mask_phone(p)} ({uid})"))
                except Exception as exc:
                    self._post(lambda p=sess.phone, e=exc: self._log(f"FAILED {mask_phone(p)} ({e})"))

        self._run_async_job(_job())

    def _remove_inactive_sessions(self) -> None:
        password = self.sessions_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Encryption password wajib diisi")
            return

        async def _job():
            bad: list[str] = []
            for sess in self.manager.list_sessions():
                try:
                    app = await self.manager.build_client(sess.phone, password)
                    await app.connect()
                    await app.get_me()
                    await app.disconnect()
                except Exception:
                    bad.append(sess.phone)

            if not bad:
                self._post(lambda: self._log("Tidak ada session inactive"))
                return

            for phone in bad:
                self.manager.remove_session(phone)
            self._post(lambda: self._log(f"Inactive sessions removed: {len(bad)}"))

        self._run_async_job(_job())

    @staticmethod
    def _md_to_html(text: str) -> str:
        import re

        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
        text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text, flags=re.DOTALL)
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\[(.+?)\]\((https?://[^\s\)]+)\)", r"<a href=\"\2\">\1</a>", text)
        return text


def _writable_env_path() -> Path:
    """Lokasi .env yang ditulis fallback dialog: di sebelah .exe / script."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".env"
    return Path(__file__).resolve().parent / ".env"


def _prompt_api_credentials(parent: tk.Tk) -> bool:
    """Pop-up sederhana untuk minta API_ID & API_HASH bila .env kosong.

    Mengembalikan True bila user mengisi & save, False bila batal.
    Save -> tulis ke .env di sebelah exe dan set os.environ supaya
    Config.from_env() langsung sukses tanpa restart.
    """
    import os

    dialog = tk.Toplevel(parent)
    dialog.title("Setup awal — Telegram Blaster By VibeTool.Club")
    dialog.geometry("520x320")
    dialog.transient(parent)
    dialog.grab_set()

    intro = (
        "Aplikasi belum dikonfigurasi.\n\n"
        "Masukkan API_ID dan API_HASH dari https://my.telegram.org/apps\n"
        "(login Telegram → My API Apps → buat app baru, ambil nilainya)."
    )
    ttk.Label(dialog, text=intro, justify=tk.LEFT, wraplength=480).pack(
        anchor="w", padx=18, pady=(16, 12)
    )

    form = ttk.Frame(dialog)
    form.pack(fill=tk.X, padx=18)

    ttk.Label(form, text="API_ID").grid(row=0, column=0, sticky="w", pady=4)
    api_id_var = tk.StringVar()
    ttk.Entry(form, textvariable=api_id_var, width=46).grid(
        row=0, column=1, sticky="we", pady=4, padx=(8, 0)
    )

    ttk.Label(form, text="API_HASH").grid(row=1, column=0, sticky="w", pady=4)
    api_hash_var = tk.StringVar()
    ttk.Entry(form, textvariable=api_hash_var, width=46).grid(
        row=1, column=1, sticky="we", pady=4, padx=(8, 0)
    )
    form.grid_columnconfigure(1, weight=1)

    status_var = tk.StringVar(value="")
    ttk.Label(dialog, textvariable=status_var, foreground="#ef5d6f").pack(
        anchor="w", padx=18, pady=(6, 0)
    )

    result = {"ok": False}

    def _on_save() -> None:
        api_id = api_id_var.get().strip()
        api_hash = api_hash_var.get().strip()
        if not api_id or not api_hash:
            status_var.set("API_ID dan API_HASH wajib diisi.")
            return
        if not api_id.isdigit():
            status_var.set("API_ID harus berupa angka.")
            return
        env_path = _writable_env_path()
        try:
            env_path.write_text(
                f"API_ID={api_id}\nAPI_HASH={api_hash}\n",
                encoding="utf-8",
            )
        except Exception as exc:
            status_var.set(f"Gagal menulis .env: {exc}")
            return
        os.environ["API_ID"] = api_id
        os.environ["API_HASH"] = api_hash
        result["ok"] = True
        dialog.destroy()

    def _on_cancel() -> None:
        result["ok"] = False
        dialog.destroy()

    btn_row = ttk.Frame(dialog)
    btn_row.pack(fill=tk.X, padx=18, pady=(18, 16))
    ttk.Button(btn_row, text="Batal", command=_on_cancel).pack(side=tk.RIGHT, padx=(8, 0))
    ttk.Button(btn_row, text="Simpan & Lanjut", command=_on_save).pack(side=tk.RIGHT)

    dialog.protocol("WM_DELETE_WINDOW", _on_cancel)
    parent.wait_window(dialog)
    return result["ok"]


def main() -> None:
    root = tk.Tk()
    root.withdraw()

    if not ensure_licensed(root):
        root.destroy()
        return

    root.deiconify()
    # Try to start. If env credentials missing, prompt once and retry.
    for _attempt in range(2):
        try:
            TelegramScraperGUI(root)
            break
        except ValueError as exc:
            msg = str(exc)
            if "API_ID" in msg or "API_HASH" in msg:
                if _prompt_api_credentials(root):
                    # Clear any partial widgets from failed init before retry.
                    for child in list(root.winfo_children()):
                        try:
                            child.destroy()
                        except Exception:
                            pass
                    continue
            messagebox.showerror("Startup Error", msg)
            root.destroy()
            return
        except Exception as exc:
            messagebox.showerror("Startup Error", str(exc))
            root.destroy()
            return
    root.mainloop()


if __name__ == "__main__":
    main()
