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

import httpx
from download.orchestrator import try_download as _try_download

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
    for path in [_exe_dir() / "config.json", SAVE_PATH]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}

def save_config(cfg: dict) -> None:
    SAVE_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def is_complete(cfg: dict) -> bool:
    return bool(cfg.get("token") and cfg.get("api_url") and cfg.get("download_dir"))

# ── Carpeta destino ───────────────────────────────────────────────────────────

def _dest_folder(base: Path, organize: str) -> Path:
    now = datetime.now(timezone.utc)
    if organize == "import_date":
        return base / now.strftime("%Y") / now.strftime("%Y-%m-%d")
    if organize == "like_date":
        return base / now.strftime("%Y") / now.strftime("%Y-%m")
    return base

# ── Download ──────────────────────────────────────────────────────────────────

def download_track(query: str, cfg: dict, user_settings: dict) -> str:
    dest = _dest_folder(Path(cfg["download_dir"]),
                        user_settings.get("folder_organize_mode", "none"))
    return _try_download(query, dest, user_settings)

# ── API ──────────────────────────────────────────────────────────────────────

def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['token']}"}

def api_get_jobs(cfg: dict) -> list[dict]:
    try:
        r = httpx.get(f"{cfg['api_url'].rstrip('/')}/api/download-jobs",
                      headers=_headers(cfg), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("fetch jobs: %s", e)
        return []

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
        self._center(400, 320 if needs_token else 260)
        self._build(needs_token)

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

        Frame(r, height=1, bg="#e2e8f0").pack(fill="x")
        Button(r, text="Iniciar agente →", font=("Segoe UI", 10, "bold"),
               bg=ACCENT, fg="white", relief="flat", cursor="hand2",
               activebackground="#1d4ed8", activeforeground="white",
               command=self._submit).pack(fill="x", padx=24, pady=16, ipady=8)

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
        self.result = {**self.cfg, "token": token, "download_dir": folder}
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
        self.cfg     = cfg
        self.running = True
        self.total   = 0
        self.q: queue.Queue[tuple] = queue.Queue()
        self._rows: dict[int, str] = {}

        self.root = Tk()
        self.root.title("Track Manager — Agente")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(460, 340)
        self.root.protocol("WM_DELETE_WINDOW", self._stop)
        self._center(500, 460)
        self._build()

    def _center(self, w: int, h: int) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build(self) -> None:
        r = self.root

        hdr = Frame(r, bg="#1e293b")
        hdr.pack(fill="x")
        Label(hdr, text="🎵 Track Manager", font=("Segoe UI", 11, "bold"),
              bg="#1e293b", fg="white").pack(side="left", padx=16, pady=10)

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
        Label(footer, text=f"Log: {LOG_PATH}", font=("Segoe UI", 7),
              bg=BG, fg="#94a3b8").pack(side="left")
        Button(footer, text="Detener agente", font=("Segoe UI", 9),
               bg="white", fg="#ef4444", relief="solid", bd=1, cursor="hand2",
               command=self._stop).pack(side="right", ipady=3, ipadx=8)

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

    def _stop(self) -> None:
        self.running = False
        self.root.destroy()

    def _apply(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "idle":
            self.dot.config(fg="#22c55e")
            self.status_lbl.config(text="Corriendo — sin pendientes")
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
            if status == "completed":
                self.total += 1
                self.count_lbl.config(text=f"{self.total} completados")

    def _poll(self) -> None:
        try:
            msg = self.q.get_nowait()
            self._apply(msg)
            delay = 80 if not self.q.empty() else 200
        except queue.Empty:
            delay = 200
        if self.running:
            self.root.after(delay, self._poll)

    def _process_job(self, job: dict, user_settings: dict) -> None:
        if not self.running:
            return
        self.q.put(("start", job["id"]))
        api_start(self.cfg, job["id"])
        result = download_track(job["query"], self.cfg, user_settings)
        api_complete(self.cfg, job["id"], result)
        self.q.put(("finish", job["id"], result))

    def _worker(self) -> None:
        poll = int(self.cfg.get("poll_seconds", 10))
        user_settings: dict = {}

        while self.running:
            fresh = api_get_settings(self.cfg)
            if fresh:
                user_settings = fresh

            if not user_settings.get("muzpa_sess"):
                self.q.put(("no_creds",))
            else:
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
