"""
Genera las capturas del agente que usa el asistente de primeros pasos.

    .venv-build\\Scripts\\python.exe scripts\\capture_agent_screenshots.py

Salen en app/static/onboarding/. Volver a correrlo cuando cambie la UI del agente.

No lanza el .exe: importa agent.py y levanta las ventanas en proceso con datos
falsos. Así el panel de Cuentas se puede mostrar con las cuatro conectadas sin
servidor, sin token real y sin tocar la configuración de nadie.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "agent"))

from PIL import ImageDraw, ImageGrab  # noqa: E402

import agent  # noqa: E402

SALIDA = RAIZ / "app" / "static" / "onboarding"
ROJO = "#dc2626"


def _capturar(ventana, destino: Path, resaltar=()) -> None:
    """
    Capturar una ventana tkinter, resaltando widgets con un recuadro rojo.

    `resaltar` son widgets, no coordenadas: hay que medirlos DESPUES de mapear y
    levantar la ventana, porque antes winfo_rootx() devuelve una posicion que
    todavia no es la definitiva y el recuadro termina fuera de la imagen.
    """
    ventana.update_idletasks()
    ventana.deiconify()
    ventana.lift()
    ventana.attributes("-topmost", True)
    ventana.update()
    time.sleep(0.6)  # que el compositor termine de dibujar antes de leer la pantalla

    x, y = ventana.winfo_rootx(), ventana.winfo_rooty()
    w, h = ventana.winfo_width(), ventana.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))

    dibujo = ImageDraw.Draw(img)
    for widget in resaltar:
        rx = widget.winfo_rootx() - x - 4
        ry = widget.winfo_rooty() - y - 4
        dibujo.rectangle(
            [rx, ry, rx + widget.winfo_width() + 8, ry + widget.winfo_height() + 8],
            outline=ROJO, width=3,
        )

    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino)

    # Una captura en blanco es el modo tipico de falla silenciosa de ImageGrab.
    colores = img.convert("RGB").getcolors(maxcolors=100_000)
    distintos = len(colores) if colores else 0
    print(f"  {destino.name}: {img.width}x{img.height}px, {distintos} colores"
          f"{'  <-- SOSPECHOSA, parece vacia' if distintos < 5 else ''}")


def capturar_configuracion() -> None:
    """La ventana que se abre sola en la primera corrida del agente."""
    print("configuracion inicial...")
    win = agent.SetupWindow({"download_dir": r"C:\Users\Mateo\Music\Track Manager"})
    win.root.update_idletasks()

    # El campo del token es el unico Entry enmascarado: se lo busca por eso y no
    # con coordenadas magicas que se romperian al cambiar el layout.
    campo = None
    for hijo in win.root.winfo_children():
        for nieto in getattr(hijo, "winfo_children", lambda: [])():
            if nieto.winfo_class() == "Entry" and nieto.cget("show") == "•":
                campo = nieto
                break
    if campo is None:
        print("  aviso: no encontre el campo del token, va sin recuadro")

    _capturar(win.root, SALIDA / "agente-configuracion.png",
              [campo] if campo is not None else [])
    win.root.destroy()


def capturar_cuentas() -> None:
    """El panel de Cuentas, con las cuatro vinculadas."""
    print("panel de cuentas...")
    falso = {
        "muzpa":      {"connected": True, "ok": True, "msg": "Cuenta conectada (tu-mail@ejemplo.com)"},
        "deezer":     {"connected": True, "ok": True, "msg": "Cuenta conectada (tu-mail@ejemplo.com)"},
        "soundcloud": {"connected": True, "ok": True, "msg": "Cuenta conectada (Tu Nombre)"},
        "youtube":    {"connected": True, "ok": True, "msg": "Cuenta conectada (Tu Canal)"},
    }
    agent.api_get_credentials = lambda _cfg: falso
    agent.RunningWindow._worker = lambda self: None  # que no toque la red

    win = agent.RunningWindow({"token": "x", "download_dir": ".", "folder_organize_mode": "none"})

    # Hace falta un mainloop de verdad: el panel pinta los estados desde un hilo
    # con root.after(), que sin mainloop tira "main thread is not in main loop".
    # Y la ventana padre NO se puede ocultar: el dialogo es transient(root), asi
    # que se oculta con ella y queda sin tamaño (la captura sale de 1x1).
    def abrir():
        win._open_accounts_dialog()
        win.root.after(1500, capturar)   # que el hilo termine de pintar

    def capturar():
        dlg = next(w for w in win.root.winfo_children()
                   if w.winfo_class() == "Toplevel" and "Cuentas" in w.title())
        _capturar(dlg, SALIDA / "agente-cuentas.png")
        win.root.quit()

    win.root.after(300, abrir)
    win.root.mainloop()
    win.root.destroy()


def main() -> int:
    print(f"escribiendo en {SALIDA}\n")
    capturar_configuracion()
    capturar_cuentas()
    print("\nlisto. Abri los PNG y confirma que se vean bien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
