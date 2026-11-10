"""
Track Manager — Agente de Descarga
Descarga tracks directamente desde Muzpa/Deezer a tu PC.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import queue
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from tkinter import *
from tkinter import filedialog, messagebox
from tkinter import ttk

if sys.platform == "darwin":
    # Los botones nativos de macOS ignoran bg pero usan fg: el texto blanco queda
    # invisible sobre un botón claro. tkmacosx.Button es un reemplazo directo.
    from tkmacosx import Button

import subprocess

import httpx
from download.auth_error import AuthExpired
from download.orchestrator import try_download as _try_download

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk
    _TRAY_OK = True
except ImportError:
    _TRAY_OK = False


def _paste_into(entry: Entry) -> None:
    """Pega el portapapeles en la caja, reemplazando lo seleccionado."""
    try:
        text = entry.clipboard_get().strip()
    except TclError:  # portapapeles vacío o sin texto
        return
    pos = entry.index(INSERT)
    if entry.selection_present():
        pos = entry.index(SEL_FIRST)
        entry.delete(SEL_FIRST, SEL_LAST)
    entry.insert(pos, text)


def _enable_entry_menu(root: Tk) -> None:
    """
    Menú Cortar/Copiar/Pegar con clic derecho en todas las cajas de texto.

    Tk no lo trae, y en macOS Cmd+V a veces no llega a la caja (pasó con el token),
    así que "Pegar" lee el portapapeles directo en vez de depender del atajo.
    """
    menu = Menu(root, tearoff=0)
    target: dict = {}
    menu.add_command(label="Cortar", command=lambda: target["w"].event_generate("<<Cut>>"))
    menu.add_command(label="Copiar", command=lambda: target["w"].event_generate("<<Copy>>"))
    menu.add_command(label="Pegar", command=lambda: _paste_into(target["w"]))

    def show(event):
        target["w"] = event.widget
        event.widget.focus_set()
        menu.tk_popup(event.x_root, event.y_root)

    # En macOS el clic derecho es Button-2 (y Ctrl+clic); en Windows, Button-3.
    clicks = ("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ("<Button-3>",)
    for click in clicks:
        root.bind_class("Entry", click, show)


def _notify(message: str) -> None:
    """Windows toast notification via PowerShell — no extra dependencies."""
    if sys.platform == "darwin":
        try:
            # El mensaje va por argv, no interpolado en el script.
            subprocess.Popen(
                ["osascript", "-e", "on run argv",
                 "-e", 'display notification (item 1 of argv) with title "Track Manager"',
                 "-e", "end run", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.debug("Notification failed: %s", e)
        return
    try:
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager,"
            "Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
            "$t=[Windows.UI.Notifications.ToastTemplateType]::ToastText01;"
            "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);"
            "$n=$x.GetElementsByTagName('text');"
            f"$n[0].AppendChild($x.CreateTextNode('{message}'))|Out-Null;"
            "$toast=[Windows.UI.Notifications.ToastNotification]::new($x);"
            "[Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier('TrackManager').Show($toast)"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", script],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.debug("Notification failed: %s", e)

# ── Rutas ────────────────────────────────────────────────────────────────────

def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

def _appdata_dir() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) if os.name == "nt" else Path.home() / ".config"
    d = base / "TrackManager"
    d.mkdir(parents=True, exist_ok=True)
    return d

APPDATA   = _appdata_dir()
LOG_PATH  = APPDATA / "agent.log"
SAVE_PATH = APPDATA / "agent.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    # Tope de ~15 MB en total: agent.log + 2 de respaldo de 5 MB
    handlers=[logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5_000_000, backupCount=2, encoding="utf-8")],
)
# httpx loguea cada request en INFO: el polling cada 10s llenaba el archivo.
# Los errores (WARNING) se siguen viendo, y los resultados de búsqueda ya los
# loguea el propio agente.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("agent")

# ── Config ───────────────────────────────────────────────────────────────────

API_URL = "https://trackmanager.app"
_USER_KEYS = {"download_dir", "folder_organize_mode", "token"}

def load_config() -> dict:
    cfg: dict = {}
    # Base config from the zip (api_url, poll_seconds, token default)
    base = _exe_dir() / "config.json"
    if base.exists():
        try:
            cfg.update(json.loads(base.read_text(encoding="utf-8")))
        except Exception:
            pass
    # User prefs saved in AppData — only override user-specific keys
    if SAVE_PATH.exists():
        try:
            saved = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
            for k in _USER_KEYS:
                if k in saved:
                    cfg[k] = saved[k]
        except Exception:
            pass
    return cfg

def save_config(cfg: dict) -> None:
    user_prefs = {k: cfg[k] for k in _USER_KEYS if k in cfg}
    SAVE_PATH.write_text(json.dumps(user_prefs, indent=2), encoding="utf-8")

def is_complete(cfg: dict) -> bool:
    return bool(cfg.get("token") and cfg.get("download_dir"))

# ── Carpeta destino ───────────────────────────────────────────────────────────

def _dest_folder(base: Path, organize: str, liked_at: str | None = None, collected_at: str | None = None) -> Path:
    now = datetime.now(timezone.utc)
    if organize == "import_date":
        if collected_at:
            try:
                d = datetime.strptime(collected_at, "%Y-%m-%d")
                return base / d.strftime("%Y") / d.strftime("%Y-%m-%d")
            except ValueError:
                pass
        return base / now.strftime("%Y") / now.strftime("%Y-%m-%d")
    if organize == "like_date":
        if liked_at:
            try:
                d = datetime.strptime(liked_at, "%Y-%m-%d")
                return base / d.strftime("%Y") / d.strftime("%Y-%m")
            except ValueError:
                pass
        return base / now.strftime("%Y") / now.strftime("%Y-%m")
    return base

# ── Download ──────────────────────────────────────────────────────────────────

def download_track(query: str, cfg: dict, user_settings: dict, liked_at: str | None = None, collected_at: str | None = None) -> tuple[str, str | None]:
    dest = _dest_folder(Path(cfg["download_dir"]),
                        cfg.get("folder_organize_mode", "none"),
                        liked_at, collected_at)
    return _try_download(query, dest, user_settings)

# ── API ──────────────────────────────────────────────────────────────────────

def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['token']}"}

def api_reset_stuck(cfg: dict) -> None:
    try:
        r = httpx.post(f"{API_URL}/api/download-jobs/reset-stuck",
                       headers=_headers(cfg), timeout=10)
        data = r.json()
        if data.get("reset", 0) > 0:
            log.info("Reset %d stuck in_progress jobs to pending", data["reset"])
    except Exception as e:
        log.error("reset stuck: %s", e)


def api_get_jobs(cfg: dict) -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/api/download-jobs",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch jobs: %s", e)
        return []

def api_get_stats(cfg: dict) -> dict:
    try:
        r = httpx.get(f"{API_URL}/api/download-jobs/stats",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch stats: %s", e)
        return {}


def api_get_settings(cfg: dict) -> dict:
    try:
        r = httpx.get(f"{API_URL}/api/me/settings",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch settings: %s", e)
        return {}

def api_get_credentials(cfg: dict) -> dict:
    """Estado de las 3 credenciales: {'muzpa': {'ok':.., 'connected':.., 'msg':..}, ...}"""
    try:
        r = httpx.get(f"{API_URL}/api/me/credentials",
                      headers=_headers(cfg), timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch credentials: %s", e)
        return {}


def connect_account(cfg: dict, service: str, fresh: bool = False) -> tuple[bool, str]:
    """
    Abrir la ventana de login en un subproceso. El subproceso valida contra el
    servidor y guarda; acá solo se lee el resultado.

    Devuelve (ok, mensaje). ok=False con mensaje vacío = el usuario cerró la
    ventana sin completar el login.
    """
    out = Path(tempfile.gettempdir()) / f"tm_login_{service}_{os.getpid()}.json"
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--login", service, str(out)]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--login", service, str(out)]
    # El token va por entorno: argv es legible por cualquier proceso de la máquina.
    env = {**os.environ, "TM_TOKEN": cfg.get("token", ""), "TM_API_URL": API_URL}
    if fresh:
        env["TM_FRESH"] = "1"  # "Cambiar cuenta": en macOS lo usa login_window.run
    try:
        subprocess.run(cmd, env=env, timeout=900)
        if out.exists():
            data = json.loads(out.read_text(encoding="utf-8"))
            return bool(data.get("ok")), str(data.get("msg", ""))
    except Exception as e:
        log.error("connect %s: %s", service, e)
        return False, f"Error: {e}"
    finally:
        out.unlink(missing_ok=True)
    return False, ""


def api_start(cfg: dict, job_id: int) -> None:
    try:
        httpx.post(f"{API_URL}/api/download-jobs/{job_id}/start",
                   headers=_headers(cfg), timeout=10)
    except Exception:
        pass

def api_complete(cfg: dict, job_id: int, status: str, label: str | None = None) -> None:
    try:
        httpx.post(f"{API_URL}/api/download-jobs/{job_id}/complete",
                   headers=_headers(cfg),
                   json={"status": status, "label": label},
                   timeout=10)
    except Exception:
        pass

# ── Colores ──────────────────────────────────────────────────────────────────

def _app_icon(root) -> None:
    """Set the music note icon on a tkinter window."""
    if not _TRAY_OK:
        return
    try:
        img = _make_icon_image()
        photo = ImageTk.PhotoImage(img)
        root.iconphoto(True, photo)
        root._icon_ref = photo  # keep reference so GC doesn't collect it
    except Exception:
        pass


def _make_icon_image(size: int = 64) -> "Image.Image":
    from make_icon import draw_logo  # el mismo logo que la web
    return draw_logo(size)


def _tray_image() -> "Image.Image":
    return _make_icon_image()

# Ancho del diálogo de Cuentas: con "Cambiar cuenta" junto a un mail largo la
# fila pide más que los 440 de las otras ventanas.
_ACCOUNTS_DLG_W = 500

# Paleta de la web (DESIGN.md): el agente tiene que verse como la misma app.
PRIMARY   = "#5558e3"
PRIMARY_H = "#4338ca"
NAV       = "#0f172a"   # barra oscura de arriba
BG        = "#f0f2f5"   # fondo de página
CARD      = "#ffffff"
FG        = "#1e293b"
MUTED     = "#56657a"
LINE      = "#e2e8f0"
OK, WARN, ERR = "#047857", "#b45309", "#b91c1c"
ACCENT = PRIMARY
FONT = "Segoe UI"   # es el fallback de la web cuando no está Instrument Sans


def _brand_bar(parent) -> Frame:
    """La barra oscura con el logo, igual a la nav de la web."""
    hdr = Frame(parent, bg=NAV)
    hdr.pack(fill="x")
    brand = Frame(hdr, bg=NAV)
    brand.pack(side="left", padx=16, pady=9)
    if _TRAY_OK:
        try:
            photo = ImageTk.PhotoImage(_make_icon_image(26))
            Label(brand, image=photo, bg=NAV).pack(side="left", padx=(0, 8))
            brand._icon_ref = photo
        except Exception:
            pass
    Label(brand, text="Track Manager", font=(FONT, 11, "bold"), bg=NAV, fg="white").pack(side="left")
    return hdr


def _nav_button(hdr, text, command) -> Button:
    """Link de la barra oscura (Cuentas, Configuración), como los de la web."""
    return Button(hdr, text=text, font=(FONT, 9), bg=NAV, fg="#bfc3cd", relief="flat", bd=0,
                  cursor="hand2", activebackground="#1e293b", activeforeground="white",
                  padx=8, command=command)


def _page_title(parent, title, sub) -> None:
    Label(parent, text=title, font=(FONT, 14, "bold"), bg=BG, fg=FG).pack(anchor="w", padx=20, pady=(16, 0))
    Label(parent, text=sub, font=(FONT, 9), bg=BG, fg=MUTED, wraplength=400,
          justify="left").pack(anchor="w", padx=20, pady=(2, 12))


def _card(parent) -> Frame:
    """Tarjeta blanca con borde fino sobre el fondo gris, como las de la web."""
    card = Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=20, pady=16)
    card.pack(fill="x", padx=20)
    return card


def _field(card, title, hint) -> None:
    Label(card, text=title, font=(FONT, 9, "bold"), bg=CARD, fg=FG).pack(anchor="w")
    Label(card, text=hint, font=(FONT, 8), bg=CARD, fg=MUTED).pack(anchor="w", pady=(1, 5))


def _entry(parent, var, show=None) -> Entry:
    e = Entry(parent, textvariable=var, font=(FONT, 9), relief="flat", bd=0, bg=CARD, fg=FG,
              insertbackground=FG, highlightthickness=1, highlightbackground=LINE,
              highlightcolor=PRIMARY)
    if show:
        e.config(show=show)
    return e


def _primary(parent, text, command) -> Button:
    return Button(parent, text=text, font=(FONT, 10, "bold"), bg=PRIMARY, fg="white",
                  relief="flat", bd=0, cursor="hand2", activebackground=PRIMARY_H,
                  activeforeground="white", command=command)


def _secondary(parent, text, command=None, fg=FG, bg=CARD) -> Frame:
    """
    Botón blanco con borde de 1px del gris de la web. tk no deja pintar el borde
    de un Button (en Windows `relief=solid` lo dibuja negro), así que el borde es
    un Frame de 1px alrededor. Se empaqueta el Frame; el botón queda en `.btn`.
    """
    box = Frame(parent, bg=LINE, padx=1, pady=1)
    box.btn = Button(box, text=text, font=(FONT, 9), bg=bg, fg=fg, relief="flat", bd=0,
                     cursor="hand2", activebackground="#f8fafc", activeforeground=fg,
                     command=command)
    box.btn.pack(ipady=3, ipadx=10)
    return box


_ORG_OPTS = [
    ("none",        "Sin organizar",            "Todos los MP3 van directo a la carpeta de descarga."),
    ("import_date", "Por fecha de importación", "Subcarpetas AÑO/AÑO-MES-DIA según cuándo se importó el track."),
    ("like_date",   "Por fecha de like",        "Subcarpetas AÑO/AÑO-MES según cuándo le diste like."),
]


def _radios(card, var) -> None:
    for val, label, hint in _ORG_OPTS:
        Radiobutton(card, text=label, variable=var, value=val, font=(FONT, 9, "bold"),
                    bg=CARD, fg=FG, activebackground=CARD, activeforeground=FG,
                    selectcolor=CARD, highlightthickness=0).pack(anchor="w", pady=(4, 0))
        Label(card, text=hint, font=(FONT, 8), bg=CARD, fg=MUTED, wraplength=370,
              justify="left").pack(anchor="w", padx=(22, 0))


def _fit(win, w: int) -> None:
    """Ancho fijo, alto el que pida el contenido, centrada en pantalla."""
    win.update_idletasks()
    h = win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

# ── Setup window ─────────────────────────────────────────────────────────────

class SetupWindow:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.result: dict | None = None

        self.root = Tk()
        self.root.title("Track Manager — Configuración")
        self.root.configure(bg=BG)
        _enable_entry_menu(self.root)
        self.root.resizable(False, False)
        needs_setup = not cfg.get("token")
        self._build(needs_setup)
        _fit(self.root, 460)
        _app_icon(self.root)

    def _build(self, needs_setup: bool) -> None:
        r = self.root
        _brand_bar(r)
        _page_title(r, "Agente de descarga", "Configuralo una sola vez. Después arranca solo.")
        card = _card(r)

        _field(card, "Carpeta de descarga", "Dónde se guardan los MP3 en tu PC")
        row = Frame(card, bg=CARD)
        row.pack(fill="x", pady=(0, 16))
        self.folder_var = StringVar(value=self.cfg.get("download_dir", ""))
        _entry(row, self.folder_var).pack(side="left", fill="x", expand=True, ipady=5)
        _secondary(row, "Elegir…", self._pick_folder).pack(side="left", padx=(6, 0))

        self.token_var = StringVar(value=self.cfg.get("token", ""))
        if needs_setup:
            _field(card, "Token de acceso", "Copialo desde la web → Configuración → Agente de descarga")
            token_row = Frame(card, bg=CARD)
            token_row.pack(fill="x", pady=(0, 16))
            token_entry = _entry(token_row, self.token_var, show="•")
            token_entry.pack(side="left", fill="x", expand=True, ipady=5)
            _secondary(token_row, "Pegar",
                       lambda: (self.token_var.set(""), _paste_into(token_entry))
                       ).pack(side="left", padx=(6, 0))

        _field(card, "Organización de carpetas", "Cómo ordenar los MP3 dentro de la carpeta de descarga")
        self.organize_var = StringVar(value=self.cfg.get("folder_organize_mode", "none"))
        _radios(card, self.organize_var)

        _primary(card, "Iniciar agente", self._submit).pack(fill="x", pady=(20, 0), ipady=8)
        Frame(r, bg=BG, height=20).pack()

    def _pick_folder(self) -> None:
        path = filedialog.askdirectory(title="Seleccionar carpeta de descarga")
        if path:
            self.folder_var.set(path)

    def _submit(self) -> None:
        folder = self.folder_var.get().strip()
        token  = self.token_var.get().strip()
        if not folder:
            messagebox.showerror("Error", "Seleccioná una carpeta de descarga.")
            return
        if not token:
            messagebox.showerror("Error", "Ingresá el token de acceso.")
            return
        self.result = {
            **self.cfg,
            "token":               token,
            "download_dir":        folder,
            "folder_organize_mode": self.organize_var.get(),
        }
        save_config(self.result)
        self.root.destroy()

    def run(self) -> dict | None:
        self.root.mainloop()
        return self.result

# ── Running window ───────────────────────────────────────────────────────────

# Mismos colores que las etiquetas de estado de la web (--s-* en base.html).
_STATUS_LABEL = {
    "pending":       ("En cola",        "#475569"),
    "downloading":   ("Descargando…",   WARN),
    "completed":     ("Completado",     "#065f46"),
    "not_found":     ("No encontrado",  "#991b1b"),
    "vinyl_only":    ("Solo vinilo",    "#92400e"),
    "bandcamp_only": ("Solo Bandcamp",  "#1e40af"),
    "failed":        ("Error",          ERR),
}

class RunningWindow:
    def __init__(self, cfg: dict):
        self.cfg      = cfg
        self.running       = True
        self._worker_done  = False
        self.total         = 0   # completados
        self.attempted     = 0   # intentados en esta sesión
        self.pending_total = 0   # pendientes en el servidor
        self.q: queue.Queue[tuple] = queue.Queue()
        self._rows: dict[int, str] = {}
        self._tray: "pystray.Icon | None" = None
        self._in_tray = False
        self._creds_expired: str | None = None  # nombre del servicio cuya credencial venció

        self.root = Tk()
        self.root.title("Track Manager — Agente")
        self.root.configure(bg=BG)
        _enable_entry_menu(self.root)
        self.root.resizable(True, True)
        self.root.minsize(480, 360)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            # Sin esto Cmd+Q cierra de golpe, salteando el aviso de _on_close.
            self.root.createcommand("tk::mac::Quit", self._on_close)
        self.root.bind("<Unmap>", lambda e: self._to_tray() if e.widget is self.root and not self._in_tray else None)
        self._center(520, 480)
        self._build()
        _app_icon(self.root)

    def _center(self, w: int, h: int) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build(self) -> None:
        r = self.root

        hdr = _brand_bar(r)
        _nav_button(hdr, "Configuración", self._open_settings_dialog).pack(side="right", padx=(0, 10), pady=8)
        _nav_button(hdr, "Cuentas", self._open_accounts_dialog).pack(side="right", pady=8)

        bar = Frame(r, bg=BG)
        bar.pack(fill="x", padx=20, pady=(12, 8))
        self.dot = Label(bar, text="●", font=(FONT, 11), bg=BG, fg=OK)
        self.dot.pack(side="left")
        self.status_lbl = Label(bar, text="Corriendo", font=(FONT, 9, "bold"), bg=BG, fg=FG)
        self.status_lbl.pack(side="left", padx=(6, 0))
        self.count_lbl = Label(bar, text="", font=(FONT, 9), bg=BG, fg=MUTED)
        self.count_lbl.pack(side="right")

        tree_frame = Frame(r, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        tree_frame.pack(fill="both", expand=True, padx=20)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Agent.Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                        rowheight=28, font=(FONT, 9), borderwidth=0, relief="flat")
        style.layout("Agent.Treeview", [("Agent.Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Agent.Treeview.Heading", font=(FONT, 8, "bold"), background="#f8fafc",
                        foreground=MUTED, relief="flat", padding=(8, 6))
        style.map("Agent.Treeview.Heading", background=[("active", "#f8fafc")])
        style.map("Agent.Treeview", background=[("selected", "#eef2ff")], foreground=[("selected", FG)])
        style.configure("Agent.Vertical.TScrollbar", background=LINE, troughcolor=CARD,
                        bordercolor=CARD, lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED,
                        relief="flat", gripcount=0)

        self.tree = ttk.Treeview(tree_frame, columns=("track", "status"),
                                 show="headings", style="Agent.Treeview",
                                 selectmode="browse")
        self.tree.heading("track",  text="TRACK", anchor="w")
        self.tree.heading("status", text="ESTADO", anchor="w")
        self.tree.column("track",  width=320, stretch=True,  anchor="w")
        self.tree.column("status", width=140, stretch=False, anchor="w")
        for status, (_label, color) in _STATUS_LABEL.items():
            self.tree.tag_configure(status, foreground=color)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview,
                            style="Agent.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        footer = Frame(r, bg=BG)
        footer.pack(fill="x", padx=20, pady=12)
        _secondary(footer, "Abrir carpeta", self._open_folder).pack(side="left")
        stop = _secondary(footer, "Detener agente", self._stop_graceful, fg=ERR)
        stop.pack(side="right")
        self.stop_btn = stop.btn

    def _open_folder(self) -> None:
        folder = self.cfg.get("download_dir", "")
        if folder and Path(folder).exists():
            if sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                os.startfile(folder)
        else:
            messagebox.showerror("Error", "La carpeta de descarga no existe o no está configurada.")

    def _open_settings_dialog(self) -> None:
        dlg = Toplevel(self.root)
        dlg.title("Configuración")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        _app_icon(dlg)

        _brand_bar(dlg)
        _page_title(dlg, "Configuración", "Carpeta, token y organización de los MP3.")
        card = _card(dlg)

        _field(card, "Carpeta de descarga", "Dónde se guardan los MP3 en tu PC")
        folder_var = StringVar(value=self.cfg.get("download_dir", ""))
        row = Frame(card, bg=CARD)
        row.pack(fill="x", pady=(0, 16))
        _entry(row, folder_var).pack(side="left", fill="x", expand=True, ipady=5)

        def pick():
            path = filedialog.askdirectory(title="Seleccionar carpeta de descarga", parent=dlg)
            if path:
                folder_var.set(path)

        _secondary(row, "Elegir…", pick).pack(side="left", padx=(6, 0))

        _field(card, "Token de acceso", "Copialo desde la web → Configuración → Agente de descarga")
        token_var = StringVar(value=self.cfg.get("token", ""))
        token_row = Frame(card, bg=CARD)
        token_row.pack(fill="x", pady=(0, 16))
        token_entry = _entry(token_row, token_var, show="•")
        token_entry.pack(side="left", fill="x", expand=True, ipady=5)
        _secondary(token_row, "Pegar",
                   lambda: (token_var.set(""), _paste_into(token_entry))
                   ).pack(side="left", padx=(6, 0))
        show = _secondary(token_row, "Ver")
        show.pack(side="left", padx=(6, 0))

        def _toggle_token():
            hidden = token_entry.cget("show") == "•"
            token_entry.config(show="" if hidden else "•")
            show.btn.config(text="Ocultar" if hidden else "Ver")

        show.btn.config(command=_toggle_token)

        _field(card, "Organización de carpetas", "Cómo ordenar los MP3 dentro de la carpeta de descarga")
        organize_var = StringVar(value=self.cfg.get("folder_organize_mode", "none"))
        _radios(card, organize_var)

        def save():
            folder = folder_var.get().strip()
            token  = token_var.get().strip()
            if not folder:
                messagebox.showerror("Error", "Seleccioná una carpeta de descarga.", parent=dlg)
                return
            if not token:
                messagebox.showerror("Error", "El token no puede estar vacío.", parent=dlg)
                return
            self.cfg["download_dir"] = folder
            self.cfg["folder_organize_mode"] = organize_var.get()
            self.cfg["token"] = token
            save_config(self.cfg)
            dlg.destroy()

        _primary(card, "Guardar configuración", save).pack(fill="x", pady=(20, 0), ipady=8)
        Label(dlg, text=f"Log: {LOG_PATH}", font=(FONT, 7), bg=BG, fg=MUTED,
              wraplength=420, justify="left").pack(anchor="w", padx=20, pady=(8, 14))
        _fit(dlg, 460)

    # ── Cuentas conectadas ───────────────────────────────────────────────────

    _ACCOUNTS = [
        ("muzpa",      "Muzpa",      "Fuente principal de descarga"),
        ("deezer",     "Deezer",     "Fuente alternativa (MP3 320)"),
        ("soundcloud", "SoundCloud", "Importa tus likes"),
        ("youtube",    "YouTube",    "Importa tus playlists"),
    ]

    @staticmethod
    def _account_button_label(info: dict) -> str:
        """
        Mira `connected`, no `ok`: una cuenta conectada pero con la sesión vencida
        también se cambia — "Conectar" ahí daría a entender que no hay ninguna.
        """
        return "Cambiar cuenta" if info.get("connected") else "Conectar"

    @staticmethod
    def _needs_account_setup(status: dict) -> bool:
        """
        Sin ninguna cuenta vinculada el agente no sirve para nada, así que el
        panel se abre solo. Con al menos una conectada no se molesta: el aviso de
        la barra de estado alcanza.

        `status` vacío = el server no contestó. Ahí tampoco se abre: no sabemos
        nada, y abrir el panel sería culpar al usuario de un problema de red.
        """
        if not status:
            return False
        return not any(info.get("connected") for info in status.values())

    def _open_accounts_if_unconfigured(self) -> None:
        """Corre en un hilo al arrancar: hace un request, no puede ir en la UI."""
        if not self.cfg.get("token"):
            return
        if self._needs_account_setup(api_get_credentials(self.cfg)):
            self.root.after(0, self._open_accounts_dialog)

    def _open_accounts_dialog(self) -> None:
        if not self.cfg.get("token"):
            messagebox.showerror(
                "Falta el token",
                "Configurá el token de acceso en Configuración antes de conectar cuentas.",
            )
            return

        dlg = Toplevel(self.root)
        dlg.title("Cuentas conectadas")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        _app_icon(dlg)

        _brand_bar(dlg)
        # wraplength obligatorio: la ventana se fuerza a un ancho fijo y sólo se
        # calcula el alto, así que un texto que pida más ancho queda cortado.
        _page_title(dlg, "Cuentas conectadas",
                    "Conectá las cuentas que Track Manager necesita para buscar y "
                    "descargar tu música. Se abre una ventana, iniciás sesión como "
                    "siempre, y el agente la recuerda.")
        card = _card(dlg)

        self._acct_dots: dict[str, Label] = {}
        self._acct_msgs: dict[str, Label] = {}
        self._acct_btns: dict[str, Button] = {}
        self._acct_connected: dict[str, bool] = {}
        for i, (service, label, hint) in enumerate(self._ACCOUNTS):
            if i:
                Frame(card, bg=LINE, height=1).pack(fill="x", pady=8)
            row = Frame(card, bg=CARD)
            row.pack(fill="x")
            dot = Label(row, text="●", font=(FONT, 10), bg=CARD, fg="#cbd5e1")
            dot.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 8))
            Label(row, text=label, font=(FONT, 10, "bold"), bg=CARD, fg=FG).grid(row=0, column=1, sticky="w")
            msg = Label(row, text=hint, font=(FONT, 8), bg=CARD, fg=MUTED)
            msg.grid(row=1, column=1, sticky="w")
            box = _secondary(row, "Conectar")
            btn = box.btn
            btn.config(command=lambda s=service, l=label, b=btn: self._connect_account(s, l, dlg, b))
            box.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))
            row.columnconfigure(1, weight=1)
            self._acct_dots[service] = dot
            self._acct_msgs[service] = msg
            self._acct_btns[service] = btn

        Button(dlg, text="Cerrar", font=(FONT, 9), relief="flat", bd=0, bg=BG, fg=MUTED,
               activebackground=BG, activeforeground=FG, cursor="hand2",
               command=dlg.destroy).pack(pady=(10, 14))
        _fit(dlg, _ACCOUNTS_DLG_W)

        threading.Thread(target=self._refresh_account_dots, daemon=True).start()

    def _refresh_account_dots(self) -> None:
        """Pintar estado real. Corre en hilo: valida contra cada servicio."""
        for service, dot in getattr(self, "_acct_dots", {}).items():
            self.root.after(0, lambda d=dot: d.winfo_exists() and d.config(fg="#cbd5e1"))
        status = api_get_credentials(self.cfg)
        for service, label, hint in self._ACCOUNTS:
            dot = self._acct_dots.get(service)
            msg_lbl = self._acct_msgs.get(service)
            if not dot:
                continue
            info = status.get(service) or {}
            color = OK if info.get("ok") else (WARN if info.get("connected") else "#cbd5e1")
            text = info.get("msg") or hint
            btn_text = self._account_button_label(info)
            btn = self._acct_btns.get(service)
            self._acct_connected[service] = bool(info.get("connected"))
            # El diálogo pudo cerrarse mientras corrían los requests.
            self.root.after(0, lambda d=dot, c=color: d.winfo_exists() and d.config(fg=c))
            self.root.after(0, lambda m=msg_lbl, t=text: m and m.winfo_exists() and m.config(text=t))
            self.root.after(0, lambda b=btn, t=btn_text: b and b.winfo_exists() and b.config(text=t))

    def _connect_account(self, service: str, label: str, dlg, btn) -> None:
        """
        La ventana de login corre en un hilo: subprocess.run bloquea hasta que el
        usuario termina, y en el hilo de tkinter eso congela toda la UI del agente.
        """
        switching = self._acct_connected.get(service, False)
        if switching:
            # Sin borrar el perfil del navegador la ventana vuelve a entrar con la
            # sesión guardada y se cierra sola: no habría forma de cambiar de cuenta.
            import login_window
            if not login_window.forget_session(service):
                messagebox.showerror(
                    label,
                    f"No se pudo borrar la sesión guardada de {label}.\n\n"
                    "Cerrá el agente por completo (también desde la bandeja) y "
                    "volvé a intentarlo.",
                    parent=dlg,
                )
                return

        previous = btn.cget("text")
        btn.config(state="disabled", text="Conectando…")

        def work():
            ok, msg = connect_account(self.cfg, service, fresh=switching)
            self.root.after(0, lambda: done(ok, msg))

        def done(ok: bool, msg: str):
            if btn.winfo_exists():
                # El refresh de abajo pone la etiqueta definitiva; esto sólo evita
                # que quede en "Conectando…" si el usuario cerró sin loguearse.
                btn.config(state="normal", text=previous)
            if ok:
                messagebox.showinfo(label, f"{label} conectado.\n\n{msg}", parent=dlg)
            elif msg:
                messagebox.showerror(label, f"No se pudo conectar {label}.\n\n{msg}", parent=dlg)
            else:
                return  # ventana cerrada sin completar el login: sin ruido
            threading.Thread(target=self._refresh_account_dots, daemon=True).start()

        threading.Thread(target=work, daemon=True).start()

    def _stop_graceful(self) -> None:
        self.stop_btn.config(state="disabled", text="Deteniendo…")
        self.running = False

    def _restart(self) -> None:
        self.running = True
        self._worker_done = False
        self._creds_expired = None
        self.dot.config(fg=OK)
        self.status_lbl.unbind("<Button-1>")
        self.status_lbl.config(text="Corriendo", fg=FG, cursor="")
        self.stop_btn.config(state="normal", text="Detener agente", command=self._stop_graceful)
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        self.root.after(200, self._poll)

    def _to_tray(self) -> None:
        # En macOS pystray tiene que correr en el hilo principal, que ya ocupa
        # tkinter: la ventana se ocultaba sin ícono para volver. Ahí minimizar va
        # al Dock, que es el equivalente nativo de la bandeja.
        if not _TRAY_OK or self._in_tray or sys.platform == "darwin":
            return
        self._in_tray = True
        self.root.withdraw()
        if self._tray is None:
            menu = pystray.Menu(
                pystray.MenuItem("Mostrar", self._from_tray, default=True),
                pystray.MenuItem("Salir", self._quit),
            )
            self._tray = pystray.Icon(
                "TrackManager", _tray_image(), "Track Manager — corriendo", menu
            )
            threading.Thread(target=self._tray.run, daemon=True).start()
        threading.Thread(
            target=_notify,
            args=("TrackManager está minimizado",),
            daemon=True,
        ).start()

    def _from_tray(self, icon=None, item=None) -> None:
        self.root.after(0, self._restore)

    def _restore(self) -> None:
        self._in_tray = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self._tray:
            self._tray.stop()
            self._tray = None

    def _on_close(self) -> None:
        if self.running:
            messagebox.showwarning(
                "Agente activo",
                "El agente todavía está descargando tracks.\n\n"
                "Hacé clic en 'Detener agente' y esperá a que termine antes de cerrar.",
            )
        else:
            self.root.destroy()

    def _quit(self, icon=None, item=None) -> None:
        self.running = False
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.root.after(0, self.root.destroy)

    def _on_tree_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        track = self.tree.set(sel[0], "track")
        self.root.clipboard_clear()
        self.root.clipboard_append(track)
        prev = self.status_lbl.cget("text")
        short = track if len(track) <= 44 else track[:44] + "…"
        self.status_lbl.config(text=f"Copiado: {short}")
        self.root.after(1800, lambda: self.status_lbl.config(text=prev))

    def _refresh_count_lbl(self) -> None:
        parts = []
        if self.attempted > 0:
            parts.append(f"{self.total} completados")
            if self.attempted > self.total:
                parts.append(f"{self.attempted} intentados")
        if self.pending_total > 0:
            parts.append(f"{self.pending_total} pendientes")
        self.count_lbl.config(text=" · ".join(parts) if parts else "")

    def _apply(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "stopped":
            self._worker_done = True
            self.stop_btn.config(state="normal", text="Reanudar agente", command=self._restart)
            # Si paramos por una credencial vencida, ese aviso manda: decir sólo
            # "Agente detenido" esconde el motivo y el usuario no sabe qué hacer.
            if not self._creds_expired:
                self.dot.config(fg="#94a3b8")
                self.status_lbl.config(text="Agente detenido")
        elif kind == "idle":
            self.dot.config(fg=OK)
            self.status_lbl.config(text="Corriendo — sin pendientes")
        elif kind == "stats":
            _, pending, in_progress = msg
            self.pending_total = pending + in_progress
            self._refresh_count_lbl()
        elif kind == "error":
            self.dot.config(fg=ERR)
            self.status_lbl.config(text="Error de conexión. Reintentando…")
        elif kind == "no_creds":
            self.dot.config(fg=WARN)
            self.status_lbl.config(text="Sin credenciales — conectá Muzpa en Cuentas")
        elif kind == "creds_expired":
            _, service = msg
            name = {"muzpa": "Muzpa", "deezer": "Deezer", "soundcloud": "SoundCloud"}.get(service, service)
            self._creds_expired = name
            self.dot.config(fg=WARN)
            self.status_lbl.config(text=f"{name} desconectado — clic acá para reconectar",
                                   fg=ACCENT, cursor="hand2")
            self.status_lbl.bind("<Button-1>", lambda _e: self._open_accounts_dialog())
            threading.Thread(
                target=_notify,
                args=(f"{name} desconectado. Abri Cuentas para reconectar.",),
                daemon=True,
            ).start()
        elif kind == "enqueue":
            _, job_id, query = msg
            label, _ = _STATUS_LABEL["pending"]
            iid = self.tree.insert("", "end", values=(query, label), tags=("pending",))
            self._rows[job_id] = iid
        elif kind == "start":
            _, job_id = msg
            label, _ = _STATUS_LABEL["downloading"]
            iid = self._rows.get(job_id)
            if iid:
                query = self.tree.set(iid, "track")
                self.tree.item(iid, values=(query, label), tags=("downloading",))
                self.tree.see(iid)
            self.dot.config(fg=WARN)
            self.status_lbl.config(text="Descargando…")
        elif kind == "finish":
            _, job_id, status = msg
            label, _ = _STATUS_LABEL.get(status, ("— " + status, "#6b7280"))
            iid = self._rows.get(job_id)
            if iid:
                query = self.tree.set(iid, "track")
                self.tree.item(iid, values=(query, label), tags=(status,))
            self.attempted += 1
            if status == "completed":
                self.total += 1
            self.pending_total = max(0, self.pending_total - 1)
            self._refresh_count_lbl()

    def _poll(self) -> None:
        try:
            msg = self.q.get_nowait()
            self._apply(msg)
            delay = 80 if not self.q.empty() else 200
        except queue.Empty:
            delay = 200
        if not self._worker_done:
            self.root.after(delay, self._poll)

    def _process_job(self, job: dict, user_settings: dict) -> None:
        if not self.running:
            return
        self.q.put(("start", job["id"]))
        api_start(self.cfg, job["id"])
        try:
            result, label = download_track(job["query"], self.cfg, user_settings,
                                    job.get("liked_at"), job.get("collected_at"))
        except AuthExpired as e:
            # A propósito NO se llama api_complete: el job queda in_progress y el
            # reset-stuck del próximo arranque lo devuelve a pending. Marcarlo
            # not_found lo daría por descartado para siempre.
            log.warning("Credencial de %s vencida — job %d queda pendiente", e.service, job["id"])
            self.running = False
            self.q.put(("creds_expired", e.service))
            return
        api_complete(self.cfg, job["id"], result, label)
        self.q.put(("finish", job["id"], result))

    def _worker(self) -> None:
        poll = int(self.cfg.get("poll_seconds", 10))
        user_settings: dict = {}

        api_reset_stuck(self.cfg)  # reset any jobs stuck in_progress from a previous run

        while self.running:
            fresh = api_get_settings(self.cfg)
            if fresh:
                user_settings = fresh

            if not user_settings.get("muzpa_sess"):
                self.q.put(("no_creds",))
            else:
                stats = api_get_stats(self.cfg)
                if stats:
                    self.q.put(("stats", stats.get("pending", 0), stats.get("in_progress", 0)))

                jobs = api_get_jobs(self.cfg)
                if not jobs:
                    self.q.put(("idle",))
                else:
                    # Pre-load all pending jobs into the treeview
                    for job in jobs:
                        self.q.put(("enqueue", job["id"], job["query"]))
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        futures = {
                            pool.submit(self._process_job, job, user_settings): job
                            for job in jobs
                        }
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except Exception as e:
                                job = futures[future]
                                log.error("Job %d failed: %s", job["id"], e)

            for _ in range(poll * 2):
                if not self.running:
                    break
                time.sleep(0.5)

        self.q.put(("stopped",))

    def run(self) -> None:
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        self.root.after(200, self._poll)
        threading.Thread(target=self._open_accounts_if_unconfigured, daemon=True).start()
        self.root.mainloop()

# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    try:
        cfg = load_config()
        if not is_complete(cfg):
            cfg = SetupWindow(cfg).run()
            if not cfg:
                return
        RunningWindow(cfg).run()
    except Exception as e:
        log.exception("Fatal error: %s", e)
        messagebox.showerror("Error inesperado", str(e))

if __name__ == "__main__":
    # Modo captura de cookie: el mismo exe corre como subproceso con una ventana
    # WebView2 en vez de la UI del agente. webview.start() bloquea el main thread
    # y solo puede llamarse una vez por proceso, así que no convive con tkinter.
    if "--login" in sys.argv:
        import login_window
        sys.exit(login_window.main(sys.argv[1:]))
    main()
