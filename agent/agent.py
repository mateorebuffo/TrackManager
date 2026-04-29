"""
Track Manager — Agente de Descarga
Descarga tracks directamente desde Muzpa/Deezer a tu PC.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from tkinter import *
from tkinter import filedialog, messagebox
from tkinter import ttk

import subprocess

import httpx
from download.orchestrator import try_download as _try_download

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk
    _TRAY_OK = True
except ImportError:
    _TRAY_OK = False


def _notify(message: str) -> None:
    """Windows toast notification via PowerShell — no extra dependencies."""
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
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("agent")

# ── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg: dict = {}
    for path in [_exe_dir() / "config.json", SAVE_PATH]:
        if path.exists():
            try:
                cfg.update(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return cfg

def save_config(cfg: dict) -> None:
    SAVE_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def is_complete(cfg: dict) -> bool:
    return bool(cfg.get("token") and cfg.get("api_url") and cfg.get("download_dir"))

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

def download_track(query: str, cfg: dict, user_settings: dict, liked_at: str | None = None, collected_at: str | None = None) -> str:
    dest = _dest_folder(Path(cfg["download_dir"]),
                        cfg.get("folder_organize_mode", "none"),
                        liked_at, collected_at)
    return _try_download(query, dest, user_settings)

# ── API ──────────────────────────────────────────────────────────────────────

def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['token']}"}

def api_reset_stuck(cfg: dict) -> None:
    try:
        r = httpx.post(f"{cfg['api_url'].rstrip('/')}/api/download-jobs/reset-stuck",
                       headers=_headers(cfg), timeout=10)
        data = r.json()
        if data.get("reset", 0) > 0:
            log.info("Reset %d stuck in_progress jobs to pending", data["reset"])
    except Exception as e:
        log.error("reset stuck: %s", e)


def api_get_jobs(cfg: dict) -> list[dict]:
    try:
        r = httpx.get(f"{cfg['api_url'].rstrip('/')}/api/download-jobs",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch jobs: %s", e)
        return []

def api_get_stats(cfg: dict) -> dict:
    try:
        r = httpx.get(f"{cfg['api_url'].rstrip('/')}/api/download-jobs/stats",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch stats: %s", e)
        return {}


def api_get_settings(cfg: dict) -> dict:
    try:
        r = httpx.get(f"{cfg['api_url'].rstrip('/')}/api/me/settings",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch settings: %s", e)
        return {}

def api_start(cfg: dict, job_id: int) -> None:
    try:
        httpx.post(f"{cfg['api_url'].rstrip('/')}/api/download-jobs/{job_id}/start",
                   headers=_headers(cfg), timeout=10)
    except Exception:
        pass

def api_complete(cfg: dict, job_id: int, status: str) -> None:
    try:
        httpx.post(f"{cfg['api_url'].rstrip('/')}/api/download-jobs/{job_id}/complete",
                   headers=_headers(cfg),
                   json={"status": status},
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


def _make_icon_image() -> "Image.Image":
    img = Image.new("RGB", (64, 64), color=(37, 99, 235))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 38, 28, 56], fill="white")
    d.ellipse([34, 30, 54, 48], fill="white")
    d.rectangle([24, 8, 30, 44], fill="white")
    d.rectangle([50, 4, 56, 36], fill="white")
    d.rectangle([24, 8, 56, 14], fill="white")
    return img


def _tray_image() -> "Image.Image":
    return _make_icon_image()

ACCENT = "#2563eb"
BG     = "#f8fafc"
FG     = "#1e293b"
MUTED  = "#64748b"

# ── Setup window ─────────────────────────────────────────────────────────────

class SetupWindow:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.result: dict | None = None

        self.root = Tk()
        self.root.title("Track Manager — Configuración")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        needs_token = not cfg.get("token")
        self._center(440, 570 if needs_token else 510)
        self._build(needs_token)
        _app_icon(self.root)

    def _center(self, w: int, h: int) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build(self, needs_token: bool) -> None:
        r = self.root
        Label(r, text="🎵 Track Manager", font=("Segoe UI", 14, "bold"),
              bg=BG, fg=FG).pack(anchor="w", padx=24, pady=(20, 2))
        Label(r, text="Configurá el agente de descarga (solo una vez)",
              font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", padx=24)
        Frame(r, height=1, bg="#e2e8f0").pack(fill="x", pady=12)

        body = Frame(r, bg=BG)
        body.pack(fill="x", padx=24)

        Label(body, text="Carpeta de descarga", font=("Segoe UI", 9, "bold"),
              bg=BG, fg=FG).pack(anchor="w")
        Label(body, text="Dónde se guardarán los MP3 en tu PC",
              font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(anchor="w", pady=(1, 5))

        row = Frame(body, bg=BG)
        row.pack(fill="x", pady=(0, 14))
        self.folder_var = StringVar(value=self.cfg.get("download_dir", ""))
        Entry(row, textvariable=self.folder_var, font=("Segoe UI", 9),
              relief="solid", bd=1).pack(side="left", fill="x", expand=True, ipady=4)
        Button(row, text="📁", font=("Segoe UI", 9), relief="solid", bd=1,
               bg="white", cursor="hand2",
               command=self._pick_folder).pack(side="left", padx=(4, 0), ipady=4, ipadx=8)

        if needs_token:
            Label(body, text="Token de acceso", font=("Segoe UI", 9, "bold"),
                  bg=BG, fg=FG).pack(anchor="w")
            Label(body, text="Copialo desde Ajustes → Agente de Descarga en la web",
                  font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(anchor="w", pady=(1, 5))
            self.token_var = StringVar()
            Entry(body, textvariable=self.token_var, show="•",
                  font=("Segoe UI", 9), relief="solid", bd=1).pack(fill="x", ipady=4, pady=(0, 14))
        else:
            self.token_var = StringVar(value=self.cfg.get("token", ""))

        Label(body, text="Organización de carpetas", font=("Segoe UI", 9, "bold"),
              bg=BG, fg=FG).pack(anchor="w")
        Label(body, text="Cómo organizar los MP3 dentro de la carpeta de descarga",
              font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(anchor="w", pady=(1, 6))

        self.organize_var = StringVar(value=self.cfg.get("folder_organize_mode", "none"))
        org_opts = [
            ("none",
             "Sin organizar",
             "Todos los MP3 se guardan directamente en la carpeta de descarga."),
            ("import_date",
             "Por fecha de importación",
             "Se crean subcarpetas AÑO/AÑO-MES-DIA según la fecha en que\n"
             "los tracks se importaron a la app."),
            ("like_date",
             "Por fecha de like",
             "Se crean subcarpetas AÑO/AÑO-MES según la fecha en que\n"
             "likeaste el track."),
        ]
        for val, label, hint in org_opts:
            Radiobutton(body, text=label, variable=self.organize_var, value=val,
                        font=("Segoe UI", 9, "bold"), bg=BG, fg=FG,
                        activebackground=BG, selectcolor=BG).pack(anchor="w", pady=(4, 0))
            Label(body, text=hint, font=("Segoe UI", 8), bg=BG, fg=MUTED,
                  justify="left").pack(anchor="w", padx=(20, 0))

        Frame(body, height=14, bg=BG).pack()
        Frame(r, height=1, bg="#e2e8f0").pack(fill="x")
        Button(r, text="Iniciar agente →", font=("Segoe UI", 10, "bold"),
               bg=ACCENT, fg="white", relief="flat", cursor="hand2",
               activebackground="#1d4ed8", activeforeground="white",
               command=self._submit).pack(fill="x", padx=24, pady=20, ipady=8)

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
            "token": token,
            "download_dir": folder,
            "folder_organize_mode": self.organize_var.get(),
        }
        save_config(self.result)
        self.root.destroy()

    def run(self) -> dict | None:
        self.root.mainloop()
        return self.result

# ── Running window ───────────────────────────────────────────────────────────

_STATUS_LABEL = {
    "pending":       ("⏳ En cola",         "#94a3b8"),
    "downloading":   ("⬇ Descargando…",    "#f59e0b"),
    "completed":     ("✅ Completado",      "#16a34a"),
    "not_found":     ("❌ No encontrado",   "#6b7280"),
    "vinyl_only":    ("💿 Solo vinilo",     "#7c3aed"),
    "bandcamp_only": ("🎸 Solo Bandcamp",   "#0ea5e9"),
    "failed":        ("⚠️ Error",           "#dc2626"),
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

        self.root = Tk()
        self.root.title("Track Manager — Agente")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(460, 340)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", lambda e: self._to_tray() if e.widget is self.root and not self._in_tray else None)
        self._center(500, 460)
        self._build()
        _app_icon(self.root)

    def _center(self, w: int, h: int) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build(self) -> None:
        r = self.root

        hdr = Frame(r, bg="#1e293b")
        hdr.pack(fill="x")
        Label(hdr, text="🎵 Track Manager", font=("Segoe UI", 11, "bold"),
              bg="#1e293b", fg="white").pack(side="left", padx=16, pady=10)
        Button(hdr, text="⚙", font=("Segoe UI", 11), bg="#1e293b", fg="white",
               relief="flat", cursor="hand2", activebackground="#334155",
               activeforeground="white", bd=0,
               command=self._open_settings_dialog).pack(side="right", padx=12, pady=6)

        bar = Frame(r, bg=BG)
        bar.pack(fill="x", padx=16, pady=(10, 6))
        self.dot = Label(bar, text="●", font=("Segoe UI", 12), bg=BG, fg="#22c55e")
        self.dot.pack(side="left")
        self.status_lbl = Label(bar, text="Corriendo", font=("Segoe UI", 9, "bold"),
                                bg=BG, fg=FG)
        self.status_lbl.pack(side="left", padx=(5, 0))
        self.count_lbl = Label(bar, text="0 completados",
                               font=("Segoe UI", 9), bg=BG, fg=MUTED)
        self.count_lbl.pack(side="right")

        Frame(r, height=1, bg="#e2e8f0").pack(fill="x")

        tree_frame = Frame(r, bg=BG)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Agent.Treeview",
                        background="white", fieldbackground="white",
                        rowheight=26, font=("Segoe UI", 9))
        style.configure("Agent.Treeview.Heading",
                        font=("Segoe UI", 8, "bold"), background="#f1f5f9",
                        foreground="#475569", relief="flat")
        style.map("Agent.Treeview", background=[("selected", "#e0f2fe")],
                  foreground=[("selected", FG)])

        self.tree = ttk.Treeview(tree_frame, columns=("track", "status"),
                                 show="headings", style="Agent.Treeview",
                                 selectmode="browse")
        self.tree.heading("track",  text="TRACK")
        self.tree.heading("status", text="ESTADO")
        self.tree.column("track",  width=310, stretch=True,  anchor="w")
        self.tree.column("status", width=150, stretch=False, anchor="w")

        self.tree.tag_configure("pending",       foreground="#94a3b8")
        self.tree.tag_configure("downloading",   foreground="#b45309")
        self.tree.tag_configure("completed",     foreground="#15803d")
        self.tree.tag_configure("not_found",     foreground="#6b7280")
        self.tree.tag_configure("vinyl_only",    foreground="#7c3aed")
        self.tree.tag_configure("bandcamp_only", foreground="#0ea5e9")
        self.tree.tag_configure("failed",        foreground="#dc2626")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        Frame(r, height=1, bg="#e2e8f0").pack(fill="x")
        footer = Frame(r, bg=BG)
        footer.pack(fill="x", padx=16, pady=8)
        Button(footer, text="📂 Abrir carpeta", font=("Segoe UI", 9),
               bg="white", fg=MUTED, relief="solid", bd=1, cursor="hand2",
               command=self._open_folder).pack(side="left", ipady=3, ipadx=8)
        self.stop_btn = Button(footer, text="⏹ Detener agente", font=("Segoe UI", 9),
               bg="white", fg="#dc2626", relief="solid", bd=1, cursor="hand2",
               command=self._stop_graceful)
        self.stop_btn.pack(side="right", ipady=3, ipadx=8)

    def _open_folder(self) -> None:
        folder = self.cfg.get("download_dir", "")
        if folder and Path(folder).exists():
            os.startfile(folder)
        else:
            messagebox.showerror("Error", "La carpeta de descarga no existe o no está configurada.")

    def _open_settings_dialog(self) -> None:
        dlg = Toplevel(self.root)
        dlg.title("Configuración")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        w, h = 440, 490
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        _app_icon(dlg)

        Label(dlg, text="Configuración", font=("Segoe UI", 12, "bold"),
              bg=BG, fg=FG).pack(anchor="w", padx=24, pady=(20, 2))
        Frame(dlg, height=1, bg="#e2e8f0").pack(fill="x", pady=10)

        body = Frame(dlg, bg=BG)
        body.pack(fill="x", padx=24)

        Label(body, text="Carpeta de descarga", font=("Segoe UI", 9, "bold"),
              bg=BG, fg=FG).pack(anchor="w")
        Label(body, text="Dónde se guardarán los MP3 en tu PC",
              font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(anchor="w", pady=(1, 5))

        folder_var = StringVar(value=self.cfg.get("download_dir", ""))
        row = Frame(body, bg=BG)
        row.pack(fill="x", pady=(0, 14))
        Entry(row, textvariable=folder_var, font=("Segoe UI", 9),
              relief="solid", bd=1).pack(side="left", fill="x", expand=True, ipady=4)

        def pick():
            path = filedialog.askdirectory(title="Seleccionar carpeta de descarga", parent=dlg)
            if path:
                folder_var.set(path)

        Button(row, text="📁", font=("Segoe UI", 9), relief="solid", bd=1,
               bg="white", cursor="hand2",
               command=pick).pack(side="left", padx=(4, 0), ipady=4, ipadx=8)

        Label(body, text="Organización de carpetas", font=("Segoe UI", 9, "bold"),
              bg=BG, fg=FG).pack(anchor="w")
        Label(body, text="Cómo organizar los MP3 dentro de la carpeta de descarga",
              font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(anchor="w", pady=(1, 6))

        organize_var = StringVar(value=self.cfg.get("folder_organize_mode", "none"))
        org_opts = [
            ("none",        "Sin organizar",         "Todos los MP3 se guardan directamente en la carpeta."),
            ("import_date", "Por fecha de importación", "Subcarpetas AÑO/AÑO-MES-DIA según fecha de importación."),
            ("like_date",   "Por fecha de like",     "Subcarpetas AÑO/AÑO-MES según la fecha de like."),
        ]
        for val, label, hint in org_opts:
            Radiobutton(body, text=label, variable=organize_var, value=val,
                        font=("Segoe UI", 9, "bold"), bg=BG, fg=FG,
                        activebackground=BG, selectcolor=BG).pack(anchor="w", pady=(4, 0))
            Label(body, text=hint, font=("Segoe UI", 8), bg=BG, fg=MUTED,
                  justify="left").pack(anchor="w", padx=(20, 0))

        Frame(dlg, height=1, bg="#e2e8f0").pack(fill="x", pady=(14, 0))

        def save():
            folder = folder_var.get().strip()
            if not folder:
                messagebox.showerror("Error", "Seleccioná una carpeta de descarga.", parent=dlg)
                return
            self.cfg["download_dir"] = folder
            self.cfg["folder_organize_mode"] = organize_var.get()
            save_config(self.cfg)
            dlg.destroy()

        Frame(dlg, height=1, bg="#e2e8f0").pack(fill="x", pady=(14, 0))

        Label(dlg, text=f"Log: {LOG_PATH}", font=("Segoe UI", 7),
              bg=BG, fg="#94a3b8").pack(anchor="w", padx=24, pady=(6, 0))

        Button(dlg, text="Guardar configuración", font=("Segoe UI", 10, "bold"),
               bg=ACCENT, fg="white", relief="flat", cursor="hand2",
               activebackground="#1d4ed8", activeforeground="white",
               command=save).pack(fill="x", padx=24, pady=(8, 16), ipady=8)

    def _stop_graceful(self) -> None:
        self.stop_btn.config(state="disabled", text="Deteniendo…")
        self.running = False

    def _restart(self) -> None:
        self.running = True
        self._worker_done = False
        self.dot.config(fg="#22c55e")
        self.status_lbl.config(text="Corriendo")
        self.stop_btn.config(state="normal", text="⏹ Detener agente", command=self._stop_graceful)
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        self.root.after(200, self._poll)

    def _to_tray(self) -> None:
        if not _TRAY_OK or self._in_tray:
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
                "Hacé clic en '⏹ Detener agente' y esperá a que termine antes de cerrar.",
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
            self.dot.config(fg="#94a3b8")
            self.status_lbl.config(text="Agente detenido")
            self.stop_btn.config(state="normal", text="▶ Reanudar agente", command=self._restart)
        elif kind == "idle":
            self.dot.config(fg="#22c55e")
            self.status_lbl.config(text="Corriendo — sin pendientes")
        elif kind == "stats":
            _, pending, in_progress = msg
            self.pending_total = pending + in_progress
            self._refresh_count_lbl()
        elif kind == "error":
            self.dot.config(fg="#ef4444")
            self.status_lbl.config(text="Error de conexión. Reintentando…")
        elif kind == "no_creds":
            self.dot.config(fg="#f59e0b")
            self.status_lbl.config(text="Sin credenciales — configurá Muzpa en Ajustes")
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
            self.dot.config(fg="#f59e0b")
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
        result = download_track(job["query"], self.cfg, user_settings, job.get("liked_at"), job.get("collected_at"))
        api_complete(self.cfg, job["id"], result)
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
    main()
