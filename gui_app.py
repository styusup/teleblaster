from __future__ import annotations

import asyncio
import html
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
from pyrogram.errors import PeerIdInvalid, SessionPasswordNeeded
from pyrogram.types import InputMediaDocument, InputMediaPhoto, InputMediaVideo

from account_manager import AccountManager
from configs import Config
from funcs.helpers import execute_with_rotation, load_checkpoint, resolve_target_chat, save_checkpoint, save_session_string
from funcs.qr_auth import show_qr_and_wait_login
from utils import append_members_dedup, ensure_paths, mask_phone, normalize_chat_target, random_delay, read_members_csv, write_members_csv_atomic


class TelegramScraperGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TelegramScraper Rebuild GUI")
        self.root.geometry("1080x800")
        self.root.minsize(1000, 740)

        try:
            # Improve default sizing on high-DPI displays.
            self.root.tk.call("tk", "scaling", 1.15)
        except Exception:
            pass

        self.themes = {
            "dark": {
                "bg": "#111318",
                "panel": "#1a1f29",
                "panel_2": "#202736",
                "text": "#e8ecf1",
                "muted": "#a9b4c0",
                "accent": "#4ea1ff",
                "accent_hover": "#6ab2ff",
                "border": "#2a3142",
                "ok": "#61d095",
            },
            "light": {
                "bg": "#f3f6fb",
                "panel": "#ffffff",
                "panel_2": "#e8eef8",
                "text": "#17202b",
                "muted": "#4f6072",
                "accent": "#2f7df6",
                "accent_hover": "#4b90f8",
                "border": "#c8d3e4",
                "ok": "#1f9d63",
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
        self.group_candidates: list[dict] = []
        self.scrape_phone_hint: str | None = None
        self.broadcast_rows: list[dict] = []
        self.broadcast_filtered_indices: list[int] = []
        self.broadcast_attachments: list[str] = []
        self.broadcast_log_lines: list[str] = []

        self.grup_scrapper_results: list[dict] = []
        self._grup_scrapper_index_by_iid: dict[str, dict] = {}

        self._build_ui()
        self._refresh_sessions_view()

    def _setup_theme(self) -> None:
        self.colors = self.themes.get(self.theme_mode.get(), self.themes["dark"])
        c = self.colors
        self.root.configure(bg=c["bg"])

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["panel"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI Semibold", 17))
        style.configure(
            "TButton",
            background=c["panel_2"],
            foreground=c["text"],
            borderwidth=0,
            focusthickness=0,
            padding=(12, 7),
            font=("Segoe UI", 10),
        )
        style.map(
            "TButton",
            background=[("active", c["accent_hover"]), ("pressed", c["accent"])],
            foreground=[("disabled", c["muted"]), ("active", "#0f1722")],
        )
        style.configure("Accent.TButton", background=c["accent"], foreground="#0f1722", font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", c["accent_hover"]), ("pressed", c["accent"])])

        style.configure(
            "TEntry",
            fieldbackground=c["panel_2"],
            foreground=c["text"],
            insertcolor=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            padding=6,
        )
        style.configure(
            "TCombobox",
            fieldbackground=c["panel_2"],
            background=c["panel_2"],
            foreground=c["text"],
            bordercolor=c["border"],
            arrowcolor=c["text"],
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["panel_2"])],
            foreground=[("readonly", c["text"])],
            selectbackground=[("readonly", c["accent"])],
            selectforeground=[("readonly", "#0f1722")],
        )

        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("TRadiobutton", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10))
        style.map("TRadiobutton", background=[("active", c["bg"])], foreground=[("active", c["text"])])
        style.configure("TSeparator", background=c["border"])
        style.configure("TProgressbar", background=c["accent"], troughcolor=c["panel_2"], bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])

        style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"], relief="solid")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 9))

        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=c["panel_2"], foreground=c["muted"], padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.map("TNotebook.Tab", background=[("selected", c["accent"]), ("active", c["panel"])], foreground=[("selected", "#0f1722"), ("active", c["text"])])

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
        widget.configure(
            bg=c["panel_2"],
            fg=c["text"],
            insertbackground=c["text"],
            selectbackground=c["accent"],
            selectforeground="#0f1722",
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
            relief=tk.FLAT,
            font=font,
        )

    def _style_listbox_widget(self, widget: tk.Listbox, *, font: tuple[str, int] = ("Segoe UI", 10)) -> None:
        c = self.colors
        widget.configure(
            bg=c["panel_2"],
            fg=c["text"],
            selectbackground=c["accent"],
            selectforeground="#0f1722",
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
            relief=tk.FLAT,
            font=font,
            bd=0,
        )

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Label(
            frame,
            text="TelegramScraper Rebuild v0.1 - Desktop GUI",
            style="Header.TLabel",
        )
        header.pack(anchor="w")

        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(frame, textvariable=self.status_var, foreground=self.colors["ok"])
        status.pack(anchor="w", pady=(4, 10))

        notebook = ttk.Notebook(frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        login_tab, self.tab_login = self._create_scrollable_tab(notebook)
        scrape_tab, self.tab_scrape = self._create_scrollable_tab(notebook)
        grup_scrapper_tab, self.tab_grup_scrapper = self._create_scrollable_tab(notebook)
        add_tab, self.tab_add = self._create_scrollable_tab(notebook)
        broadcast_tab, self.tab_broadcast = self._create_scrollable_tab(notebook)
        sessions_tab, self.tab_sessions = self._create_scrollable_tab(notebook)
        about_tab, self.tab_about = self._create_scrollable_tab(notebook)

        notebook.add(login_tab, text="Login")
        notebook.add(scrape_tab, text="Members Scraper")
        notebook.add(grup_scrapper_tab, text="Grup Scrapper")
        notebook.add(add_tab, text="Members Adder")
        notebook.add(broadcast_tab, text="Broadcast")
        notebook.add(sessions_tab, text="Sessions")
        notebook.add(about_tab, text="About")

        self._build_login_tab()
        self._build_scrape_tab()
        self._build_grup_scrapper_tab()
        self._build_add_tab()
        self._build_broadcast_tab()
        self._build_sessions_tab()
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

        ttk.Label(frm, text="Mode").grid(row=1, column=0, sticky="w")
        self.scrape_mode = ttk.Combobox(frm, width=28, state="readonly", values=["Visible Members", "Hidden Members"])
        self.scrape_mode.set("Visible Members")
        self.scrape_mode.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Encryption Password").grid(row=2, column=0, sticky="w")
        self.scrape_password = ttk.Entry(frm, show="*", width=32)
        self.scrape_password.grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Group username/link").grid(row=3, column=0, sticky="w")
        self.scrape_target = ttk.Entry(frm, width=48)
        self.scrape_target.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Run Scrape", style="Accent.TButton", command=self._run_scrape).grid(row=4, column=1, sticky="w", padx=8, pady=8)

        ttk.Button(frm, text="Load My Joined Groups", command=self._load_joined_groups).grid(
            row=4, column=2, sticky="w", padx=6, pady=8
        )

        self.group_listbox = tk.Listbox(frm, height=9, width=78)
        self.group_listbox.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._style_listbox_widget(self.group_listbox)

        ttk.Button(frm, text="Use Selected Group", command=self._use_selected_group).grid(
            row=7, column=0, sticky="w", pady=6
        )

        ttk.Label(
            frm,
            text="Hasil disimpan ke members.csv",
            foreground="#666",
        ).grid(row=8, column=0, columnspan=3, sticky="w")

    def _build_add_tab(self) -> None:
        frm = self.tab_add

        ttk.Label(frm, text="Add Members", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Mode").grid(row=1, column=0, sticky="w")
        self.add_mode = ttk.Combobox(frm, width=28, state="readonly", values=["Rush", "Calm"])
        self.add_mode.set("Rush")
        self.add_mode.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Encryption Password").grid(row=2, column=0, sticky="w")
        self.add_password = ttk.Entry(frm, show="*", width=32)
        self.add_password.grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Target group username/link").grid(row=3, column=0, sticky="w")
        self.add_target = ttk.Entry(frm, width=48)
        self.add_target.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Run Adder", style="Accent.TButton", command=self._run_adder).grid(row=4, column=1, sticky="w", padx=8, pady=8)

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

        ttk.Label(frm, text="Keyword niche").grid(row=1, column=0, sticky="w")
        self.grup_scrapper_query = ttk.Entry(frm, width=48)
        self.grup_scrapper_query.grid(row=1, column=1, columnspan=3, sticky="ew", padx=8, pady=2)
        self.grup_scrapper_query.bind("<Return>", lambda _e: self._run_grup_scrapper_search())

        ttk.Label(frm, text="Encryption Password").grid(row=2, column=0, sticky="w")
        self.grup_scrapper_password = ttk.Entry(frm, show="*", width=32)
        self.grup_scrapper_password.grid(row=2, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(frm, text="Tipe").grid(row=3, column=0, sticky="w")
        self.grup_scrapper_type = ttk.Combobox(
            frm,
            width=28,
            state="readonly",
            values=["Semua (Group + Channel)", "Group/Supergroup saja", "Channel saja"],
        )
        self.grup_scrapper_type.set("Semua (Group + Channel)")
        self.grup_scrapper_type.grid(row=3, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(frm, text="Limit hasil").grid(row=3, column=2, sticky="e", padx=(8, 4))
        self.grup_scrapper_limit = ttk.Entry(frm, width=8)
        self.grup_scrapper_limit.insert(0, "50")
        self.grup_scrapper_limit.grid(row=3, column=3, sticky="w")

        ttk.Label(frm, text="Delay join random (sec)").grid(row=4, column=0, sticky="w")
        delay_wrap = ttk.Frame(frm)
        delay_wrap.grid(row=4, column=1, sticky="w", padx=8, pady=2)
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
        ).grid(row=4, column=2, columnspan=2, sticky="w", padx=4)

        actions = ttk.Frame(frm)
        actions.grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 4))
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
        tree_wrap.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(6, 4))
        frm.grid_rowconfigure(6, weight=1)

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
        ).grid(row=7, column=0, columnspan=4, sticky="w", pady=(2, 0))

        ttk.Label(
            frm,
            text=(
                "Tip: Telegram membatasi hasil global search ~10–50 per query, jadi pakai keyword spesifik. "
                "Double-click baris untuk salin link ke clipboard."
            ),
            style="Muted.TLabel",
            wraplength=900,
            justify=tk.LEFT,
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(2, 0))

    def _build_broadcast_tab(self) -> None:
        frm = self.tab_broadcast

        frm.grid_columnconfigure(0, minsize=200)
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_columnconfigure(2, minsize=170)
        frm.grid_columnconfigure(3, minsize=150)

        ttk.Label(frm, text="Broadcast Message", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="Encryption Password").grid(row=1, column=0, sticky="w")
        self.broadcast_password = ttk.Entry(frm, show="*", width=32)
        self.broadcast_password.grid(row=1, column=1, sticky="ew", padx=8)

        ttk.Label(frm, text="Markdown file").grid(row=2, column=0, sticky="w")
        self.broadcast_file = ttk.Entry(frm, width=52)
        self.broadcast_file.insert(0, self.config.template_file)
        self.broadcast_file.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8)
        ttk.Button(frm, text="Browse", command=self._browse_md).grid(row=2, column=3, padx=6, sticky="ew")

        ttk.Label(frm, text="Broadcast text (langsung)").grid(row=3, column=0, sticky="w")
        self.broadcast_text = tk.Text(frm, height=4, width=70, wrap=tk.WORD)
        self.broadcast_text.grid(row=3, column=1, columnspan=3, sticky="ew", padx=8)
        self._style_text_widget(self.broadcast_text, font=("Segoe UI", 10))

        ttk.Label(frm, text="Links (opsional, satu per baris)").grid(row=4, column=0, sticky="w")
        self.broadcast_links = tk.Text(frm, height=3, width=70, wrap=tk.WORD)
        self.broadcast_links.grid(row=4, column=1, columnspan=3, sticky="ew", padx=8)
        self._style_text_widget(self.broadcast_links, font=("Segoe UI", 10))

        ttk.Label(frm, text="Attachments (image/video/document)").grid(row=5, column=0, sticky="w")
        self.broadcast_attachment_box = tk.Listbox(frm, height=4, width=70)
        self.broadcast_attachment_box.grid(row=5, column=1, columnspan=2, sticky="ew", padx=8)
        self._style_listbox_widget(self.broadcast_attachment_box)
        attach_btns = ttk.Frame(frm)
        attach_btns.grid(row=5, column=3, sticky="nsew", padx=(0, 4))
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

        self.broadcast_selected_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm,
            text="Broadcast only selected members",
            variable=self.broadcast_selected_only,
        ).grid(row=6, column=0, sticky="w")

        ttk.Button(frm, text="Run Broadcast", style="Accent.TButton", command=self._run_broadcast).grid(row=6, column=1, sticky="w", padx=8, pady=8)

        ttk.Button(frm, text="Reload Scraped Members", command=self._reload_broadcast_members).grid(
            row=6, column=2, sticky="ew", padx=6, pady=8
        )

        ttk.Button(frm, text="Open Broadcast Log", command=self._open_broadcast_log_window).grid(
            row=6, column=3, sticky="ew", padx=6, pady=8
        )

        ttk.Label(frm, text="Search").grid(row=7, column=0, sticky="w")
        self.broadcast_search = ttk.Entry(frm, width=40)
        self.broadcast_search.grid(row=7, column=1, columnspan=2, sticky="ew", padx=8)
        self.broadcast_search.bind("<KeyRelease>", self._on_broadcast_search_changed)
        ttk.Button(frm, text="Clear", command=self._clear_broadcast_search).grid(row=7, column=3, sticky="ew", padx=6)

        ttk.Label(frm, text="Manual targets (opsional: username/ID/link, pisah baris atau koma)").grid(
            row=8, column=0, sticky="w", pady=(6, 0)
        )
        self.broadcast_manual_targets = tk.Text(frm, height=3, width=70, wrap=tk.WORD)
        self.broadcast_manual_targets.grid(row=8, column=1, columnspan=2, sticky="ew", padx=8, pady=(6, 0))
        self.broadcast_manual_targets.bind("<KeyRelease>", self._on_manual_targets_changed)
        self._style_text_widget(self.broadcast_manual_targets, font=("Segoe UI", 10))

        manual_btns = ttk.Frame(frm)
        manual_btns.grid(row=8, column=3, sticky="nsew", padx=6, pady=(6, 0))
        ttk.Button(manual_btns, text="Load .txt", command=self._load_manual_targets_file).pack(fill=tk.X)
        ttk.Button(manual_btns, text="Clear Targets", command=self._clear_manual_targets).pack(fill=tk.X, pady=(4, 0))

        self.broadcast_count_var = tk.StringVar(value="Contacts: 0 shown / 0 total | Selected: 0")
        ttk.Label(frm, textvariable=self.broadcast_count_var, foreground="#666").grid(
            row=9, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        self.broadcast_empty_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.broadcast_empty_var, style="Muted.TLabel").grid(
            row=9, column=3, sticky="e", pady=(6, 0)
        )

        list_wrap = ttk.Frame(frm)
        list_wrap.grid(row=10, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
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

        ttk.Button(frm, text="Select All", command=self._select_all_broadcast_members).grid(row=11, column=0, sticky="w", pady=6)
        ttk.Button(frm, text="Clear Selection", command=self._clear_broadcast_selection).grid(
            row=11, column=1, sticky="w", padx=8, pady=6
        )

        self.broadcast_last_log_var = tk.StringVar(value="Last log: -")
        ttk.Label(frm, textvariable=self.broadcast_last_log_var, foreground="#666").grid(
            row=12, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        ttk.Label(frm, text="Broadcast Activity Log", foreground="#666").grid(row=13, column=0, sticky="w", pady=(6, 0))
        self.broadcast_log_box = tk.Text(frm, height=6, width=96, wrap=tk.WORD, font=("Consolas", 9))
        self.broadcast_log_box.grid(row=14, column=0, columnspan=4, sticky="ew")
        self._style_text_widget(self.broadcast_log_box, font=("Consolas", 9))

        self.broadcast_progress_var = tk.StringVar(value="Progress: 0/0 | Sent: 0 | Failed: 0")
        ttk.Label(frm, textvariable=self.broadcast_progress_var, foreground="#0078D4").grid(
            row=15, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        self.broadcast_progress = ttk.Progressbar(frm, mode="determinate", maximum=100, value=0)
        self.broadcast_progress.grid(row=16, column=0, columnspan=4, sticky="ew", pady=(2, 0))

        frm.grid_rowconfigure(10, weight=1)

    def _build_sessions_tab(self) -> None:
        frm = self.tab_sessions

        ttk.Label(frm, text="Sessions", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.sessions_box = tk.Text(frm, height=16, width=90, wrap=tk.WORD, font=("Consolas", 10))
        self.sessions_box.grid(row=1, column=0, columnspan=4, sticky="nsew")
        self._style_text_widget(self.sessions_box)

        ttk.Button(frm, text="Refresh", command=self._refresh_sessions_view).grid(row=2, column=0, pady=8, sticky="w")

        ttk.Label(frm, text="Encryption Password").grid(row=3, column=0, sticky="w")
        self.sessions_password = ttk.Entry(frm, show="*", width=28)
        self.sessions_password.grid(row=3, column=1, sticky="w", padx=8)

        ttk.Button(frm, text="Test Sessions", command=self._test_sessions).grid(row=3, column=2, sticky="w")
        ttk.Button(frm, text="Remove Inactive", command=self._remove_inactive_sessions).grid(row=3, column=3, sticky="w", padx=6)

        frm.grid_rowconfigure(1, weight=1)
        frm.grid_columnconfigure(0, weight=1)

    def _build_about_tab(self) -> None:
        text = (
            "TelegramScraper Rebuild GUI\n\n"
            "GUI desktop untuk memudahkan user non-teknis.\n"
            "Data session disimpan lokal dan terenkripsi.\n"
            "Gunakan hanya untuk akun/grup yang Anda kelola secara legal."
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
        self.status_var.set(text)

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

        self.auth_busy = True

        async def _job():
            otp_session_name = f"otp_flow_{re.sub(r'\D+', '', phone)}"
            app = Client(
                name=otp_session_name,
                api_id=self.config.api_id,
                api_hash=self.config.api_hash,
                in_memory=False,
                workdir=self.config.logs_dir,
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
                # Persist only primitives to avoid cross-event-loop client reuse/hangs.
                self.login_state = {
                    "phone": phone,
                    "enc_pw": enc_pw,
                    "phone_code_hash": sent.phone_code_hash,
                    "otp_session_name": otp_session_name,
                }
                self._post(lambda: self._log(f"OTP sent to {phone}. Input OTP lalu klik Complete Login."))
            finally:
                try:
                    await app.disconnect()
                except Exception:
                    pass
                self._post(lambda: setattr(self, "auth_busy", False))

        self._run_async_job(_job())

    def _complete_otp_login(self) -> None:
        if self.auth_busy:
            messagebox.showinfo("Login", "Proses login sedang berjalan. Tunggu sampai selesai.")
            return

        otp = self.login_otp.get().replace(" ", "").strip()
        twofa = self.login_2fa.get().strip()
        if not self.login_state:
            messagebox.showwarning("Login", "Klik Send OTP dulu")
            return

        state_phone = (self.login_state.get("phone") or "").strip()
        input_phone = self.login_phone.get().strip()
        if state_phone and input_phone and state_phone != input_phone:
            messagebox.showwarning("Login", "Nomor berubah setelah Send OTP. Silakan klik Send OTP lagi untuk nomor terbaru.")
            self.login_state = None
            return

        if not otp:
            messagebox.showwarning("Login", "OTP wajib diisi")
            return

        self.auth_busy = True

        async def _job():
            state = self.login_state
            phone = state["phone"]
            otp_session_name = state.get("otp_session_name") or f"otp_flow_{re.sub(r'\D+', '', phone)}"
            app = Client(
                name=otp_session_name,
                api_id=self.config.api_id,
                api_hash=self.config.api_hash,
                in_memory=False,
                workdir=self.config.logs_dir,
            )
            keep_login_state = False
            try:
                self._post(lambda p=phone: self._log(f"Menyelesaikan login OTP untuk {p}..."))
                await app.connect()
                try:
                    await app.sign_in(phone_number=phone, phone_code_hash=state["phone_code_hash"], phone_code=otp)
                except SessionPasswordNeeded:
                    if not twofa:
                        raise RuntimeError("Akun butuh 2FA password")
                    await app.check_password(twofa)
                except Exception as exc:
                    err_text = str(exc).upper()
                    if "PHONE_CODE_EXPIRED" in err_text:
                        self.login_state = None
                        self._post(lambda: self.login_otp.delete(0, tk.END))
                        self._post(
                            lambda: messagebox.showwarning(
                                "OTP Expired",
                                "Kode OTP kadaluarsa. Klik Send OTP sekali lagi untuk mendapatkan kode baru.",
                            )
                        )
                        self._post(lambda: self._log("OTP kadaluarsa. Silakan klik Send OTP lagi (jangan berulang-ulang)."))
                        return
                    if "PHONE_CODE_INVALID" in err_text:
                        keep_login_state = True
                        raise RuntimeError("Kode OTP tidak valid. Periksa lagi lalu coba Complete Login.") from exc
                    wait_s = self._extract_flood_wait_seconds(err_text)
                    if wait_s:
                        self.login_state = None
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
                    password=state["enc_pw"],
                )
                self._post(lambda p=phone: self._log(f"Login sukses untuk {p}. Session terenkripsi tersimpan."))
                self._post(self._refresh_sessions_view)
            finally:
                await app.disconnect()
                if not keep_login_state:
                    self.login_state = None
                self._post(lambda: setattr(self, "auth_busy", False))

        self._run_async_job(_job())

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

        async def _job():
            if mode == "Visible Members":
                await self._scrape_visible(password, target)
            else:
                await self._scrape_hidden(password, target)

        self._run_async_job(_job())

    def _load_joined_groups(self) -> None:
        password = self.scrape_password.get().strip()
        if not password:
            messagebox.showwarning("Input", "Isi Encryption Password dulu")
            return

        async def _job():
            sessions = self.manager.list_sessions()
            if not sessions:
                raise RuntimeError("Belum ada session login")

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

        # For numeric group IDs, find an account that can actually resolve this target.
        if target.strip().lstrip("-").isdigit():
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
        self.broadcast_count_var.set(
            f"Contacts: {shown} shown / {total} total | Selected: {selected} | Manual: {manual}"
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

    async def _scrape_visible(self, password: str, target: str) -> None:
        rows: list[dict] = []

        async def _op(app, _phone: str):
            chat = await resolve_target_chat(app, target)
            async for member in app.get_chat_members(chat.id, filter=ChatMembersFilter.SEARCH):
                user = member.user
                if not user or user.is_bot:
                    continue
                access_hash = await self._resolve_access_hash(app, int(user.id))
                rows.append(
                    {
                        "Name": (user.first_name or "") + (f" {user.last_name}" if user.last_name else ""),
                        "ID": str(user.id),
                        "Username": user.username or "",
                        "Access Hash": str(access_hash or ""),
                        "Group Name": chat.title or "",
                        "Group ID": str(chat.id),
                    }
                )
            return True

        _, phone = await self._execute_with_scrape_hint(password, target, _op)
        before, after = append_members_dedup(self.config.members_csv, rows)
        summary = f"Visible scrape via {phone} selesai. before={before}, after={after}"
        self._post(lambda s=summary: self._log(s))
        self._post(lambda s=summary: messagebox.showinfo("Scrape Result", s))
        self._post(self._reload_broadcast_members)

    async def _scrape_hidden(self, password: str, target: str) -> None:
        checkpoint = load_checkpoint(self.config.checkpoint_file)
        start_from = 0
        users: dict[str, dict] = {}
        if checkpoint.get("target") == target and checkpoint.get("last_message_id"):
            start_from = int(checkpoint.get("last_message_id", 0))
            users = checkpoint.get("users", {})

        async def _op(app, _phone: str):
            chat = await resolve_target_chat(app, target)
            counter = 0
            async for msg in app.get_chat_history(chat.id):
                if start_from and msg.id >= start_from:
                    continue

                extracted: dict[str, dict] = {}
                if msg.from_user and not msg.from_user.is_bot:
                    u = msg.from_user
                    access_hash = await self._resolve_access_hash(app, int(u.id))
                    extracted[str(u.id)] = {
                        "Name": (u.first_name or "") + (f" {u.last_name}" if u.last_name else ""),
                        "ID": str(u.id),
                        "Username": u.username or "",
                        "Access Hash": str(access_hash or ""),
                        "Group Name": chat.title or "",
                        "Group ID": str(chat.id),
                    }

                if getattr(msg, "forward_from", None) and not msg.forward_from.is_bot:
                    u = msg.forward_from
                    access_hash = await self._resolve_access_hash(app, int(u.id))
                    extracted[str(u.id)] = {
                        "Name": (u.first_name or "") + (f" {u.last_name}" if u.last_name else ""),
                        "ID": str(u.id),
                        "Username": u.username or "",
                        "Access Hash": str(access_hash or ""),
                        "Group Name": chat.title or "",
                        "Group ID": str(chat.id),
                    }

                entities = msg.entities or []
                text = msg.text or msg.caption or ""
                for ent in entities:
                    if ent.type == MessageEntityType.TEXT_MENTION and getattr(ent, "user", None):
                        u = ent.user
                        if not u.is_bot:
                            access_hash = await self._resolve_access_hash(app, int(u.id))
                            extracted[str(u.id)] = {
                                "Name": (u.first_name or "") + (f" {u.last_name}" if u.last_name else ""),
                                "ID": str(u.id),
                                "Username": u.username or "",
                                "Access Hash": str(access_hash or ""),
                                "Group Name": chat.title or "",
                                "Group ID": str(chat.id),
                            }
                    elif ent.type == MessageEntityType.MENTION:
                        mention = text[ent.offset : ent.offset + ent.length].strip().lstrip("@")
                        if mention:
                            try:
                                u = await app.get_users(mention)
                                if u and not u.is_bot:
                                    access_hash = await self._resolve_access_hash(app, int(u.id))
                                    extracted[str(u.id)] = {
                                        "Name": (u.first_name or "") + (f" {u.last_name}" if u.last_name else ""),
                                        "ID": str(u.id),
                                        "Username": u.username or "",
                                        "Access Hash": str(access_hash or ""),
                                        "Group Name": chat.title or "",
                                        "Group ID": str(chat.id),
                                    }
                            except Exception:
                                pass

                if extracted:
                    users.update(extracted)
                    counter += len(extracted)
                    if counter % 50 == 0:
                        save_checkpoint(
                            self.config.checkpoint_file,
                            {"target": target, "last_message_id": msg.id, "users": users},
                        )

            return True

        _, phone = await self._execute_with_scrape_hint(password, target, _op)
        before, after = append_members_dedup(self.config.members_csv, list(users.values()))
        save_checkpoint(self.config.checkpoint_file, {})
        summary = f"Hidden scrape via {phone} selesai. before={before}, after={after}"
        self._post(lambda s=summary: self._log(s))
        self._post(lambda s=summary: messagebox.showinfo("Scrape Result", s))
        self._post(self._reload_broadcast_members)

    def _run_adder(self) -> None:
        password = self.add_password.get().strip()
        target = self.add_target.get().strip()
        mode = self.add_mode.get()
        if not password or not target:
            messagebox.showwarning("Input", "Password dan target wajib diisi")
            return

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

                    _, used_phone = await execute_with_rotation(self.manager, password, _op)
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

        async def _job():
            async def _op(app, _phone: str):
                return await app.invoke(raw.functions.contacts.Search(q=query, limit=limit))

            result, used_phone = await execute_with_rotation(self.manager, password, _op)

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

            await execute_with_rotation(self.manager, password, _op)
            self._post(lambda: self._log("Fetch member counts selesai"))

        self._run_async_job(_job())

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

        if not messagebox.askyesno(
            "Konfirmasi Join",
            f"Akan join {len(targets)} grup/channel dengan delay random {delay_min:.1f}-{delay_max:.1f} detik. Lanjutkan?",
        ):
            return

        async def _job():
            joined_count = 0
            failed_count = 0
            total = len(targets)

            for idx, item in enumerate(targets):
                username = (item.get("username") or "").strip()
                title = item.get("title", "")
                if not username:
                    failed_count += 1
                    item["status"] = "Skip: tanpa username"
                    self._post(lambda it=item: self._refresh_grup_scrapper_row(it))
                    continue

                link = f"@{username}"

                try:
                    async def _op(app, _phone: str, _link=link):
                        try:
                            await app.join_chat(_link)
                        except Exception as exc:
                            err = (str(exc) or "").upper()
                            if "USER_ALREADY_PARTICIPANT" in err or "ALREADY_PARTICIPANT" in err:
                                return True
                            raise
                        return True

                    _, used_phone = await execute_with_rotation(self.manager, password, _op)
                    joined_count += 1
                    item["joined"] = True
                    item["status"] = "Joined"
                    self._post(lambda it=item: self._refresh_grup_scrapper_row(it))
                    self._post(
                        lambda t=title, p=used_phone, n=idx + 1, tot=total: self._log(
                            f"[Grup Scrapper] Joined {t} via {p} ({n}/{tot})"
                        )
                    )
                except Exception as exc:
                    failed_count += 1
                    err_text = f"{type(exc).__name__}: {exc}"
                    item["status"] = f"Failed: {err_text[:60]}"
                    self._post(lambda it=item: self._refresh_grup_scrapper_row(it))
                    self._post(
                        lambda t=title, e=err_text: self._log(f"[Grup Scrapper] Join {t} gagal: {e}")
                    )

                if idx < total - 1:
                    d = random_delay(delay_min, delay_max)
                    self._post(lambda d=d: self._log(f"[Grup Scrapper] Sleep {d:.1f}s sebelum join berikutnya"))
                    await asyncio.sleep(d)

            summary = f"Grup Scrapper join selesai: ok={joined_count}, gagal={failed_count}, total={total}"
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

    def _build_broadcast_preview(self, recipients_count: int, direct_text: str, links: list[str], attachments: list[str]) -> str:
        lines = [
            "Konfirmasi Broadcast",
            "",
            f"Recipients: {recipients_count}",
            f"Attachments: {len(attachments)}",
            f"Links: {len(links)}",
            "",
        ]

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

    async def _send_broadcast_payload(self, app, chat_target, html: str, attachments: list[str]) -> None:
        html = (html or "").strip()
        html_for_caption = html[:1024] if html else ""

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

            if self._is_image_file(path):
                await app.send_photo(chat_target, path, caption=html_for_caption or None, parse_mode=ParseMode.HTML)
            elif self._is_video_file(path):
                await app.send_video(chat_target, path, caption=html_for_caption or None, parse_mode=ParseMode.HTML)
            else:
                await app.send_document(chat_target, path, caption=html_for_caption or None, parse_mode=ParseMode.HTML)
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

        await app.send_media_group(chat_target, media)

    async def _send_broadcast_payload_input_user(self, app, user_id: int, access_hash: int, html_text: str, attachments: list[str]) -> None:
        # Raw fallback only supports text message in this implementation.
        if attachments:
            raise RuntimeError("Raw InputUser fallback hanya mendukung text tanpa attachment")

        msg = re.sub(r"<[^>]+>", "", html_text or "").strip()
        msg = html.unescape(msg)
        if not msg:
            raise RuntimeError("Konten text kosong untuk fallback InputUser")

        rnd = random.randint(1, 2_147_483_647)
        await app.invoke(
            raw.functions.messages.SendMessage(
                peer=raw.types.InputPeerUser(user_id=user_id, access_hash=access_hash),
                message=msg,
                random_id=rnd,
                no_webpage=True,
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

        # Last fallback: probe a subset of joined groups and try to resolve member by ID.
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

                if checked >= 20:
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

        selected_indices = tuple(self.broadcast_listbox.curselection()) if hasattr(self, "broadcast_listbox") else tuple()
        selected_only = bool(self.broadcast_selected_only.get()) if hasattr(self, "broadcast_selected_only") else False

        try:
            selected_ids = self._resolve_selected_ids(selected_only, selected_indices)
        except Exception as exc:
            messagebox.showwarning("Broadcast", str(exc))
            return

        all_rows_preview = read_members_csv(self.config.members_csv)
        manual_rows_preview = self._build_manual_recipient_rows()
        manual_rows_preview = self._enrich_manual_rows_with_known_data(manual_rows_preview, all_rows_preview)
        if not all_rows_preview and not manual_rows_preview:
            messagebox.showwarning("Broadcast", "members.csv kosong")
            return

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

        confirm_text = self._build_broadcast_preview(
            recipients_count=len(preview_rows),
            direct_text=direct_text,
            links=links,
            attachments=attachments,
        )
        if not messagebox.askyesno("Confirm Broadcast", confirm_text):
            self._log_broadcast("Broadcast dibatalkan user")
            return

        async def _job():
            html = self._build_broadcast_html()
            if len(html) > 4096:
                raise RuntimeError("Message lebih dari 4096 chars setelah konversi")

            all_rows = read_members_csv(self.config.members_csv)
            manual_rows = self._build_manual_recipient_rows()
            manual_rows = self._enrich_manual_rows_with_known_data(manual_rows, all_rows)
            if not all_rows and not manual_rows:
                raise RuntimeError("members.csv kosong dan manual targets juga kosong")

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
                        except PeerIdInvalid:
                            # For ID-only recipients, try to prime peer cache using access hash then retry once.
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
                                    raise RuntimeError("Access hash tidak tersedia untuk ID target (gunakan username/link atau scrape ulang)")

                                await app.invoke(
                                    raw.functions.users.GetUsers(
                                        id=[raw.types.InputUser(user_id=int(uid), access_hash=access_hash)]
                                    )
                                )
                                try:
                                    await self._send_broadcast_payload(app, int(uid), html, attachments)
                                except PeerIdInvalid:
                                    # Final fallback for ID-only peers: direct raw send via InputPeerUser.
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

                    _, used_phone = await execute_with_rotation(self.manager, password, _op)
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

        self._run_async_job(_job())

    def _refresh_sessions_view(self) -> None:
        sessions = self.manager.list_sessions()
        self.sessions_box.delete("1.0", tk.END)
        if not sessions:
            self.sessions_box.insert(tk.END, "Belum ada akun login tersimpan.\n")
            self.sessions_box.insert(tk.END, "Silakan login dari tab Login lalu klik Complete Login/QR Login.\n")
            return

        self.sessions_box.insert(tk.END, f"Total akun login tersimpan: {len(sessions)}\n\n")
        for sess in sessions:
            rem = self.manager.get_cooldown_remaining(sess.phone)
            status = f"Cooldown {rem}s" if rem else "Active"
            self.sessions_box.insert(tk.END, f"{sess.phone} | {mask_phone(sess.phone)} | {status}\n")

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


def main() -> None:
    root = tk.Tk()
    try:
        TelegramScraperGUI(root)
    except Exception as exc:
        messagebox.showerror("Startup Error", str(exc))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
