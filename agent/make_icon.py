"""
Ícono del agente: el mismo logo de la web (app/static/icons/logo.svg), dibujado
con Pillow. `draw_logo` también lo usa agent.py para la ventana, la bandeja y la
barra de marca, así el agente y la web comparten la misma imagen.
"""
from pathlib import Path
from PIL import Image, ImageDraw

LOGO_BG = (99, 102, 241)  # #6366f1, el índigo de logo.svg


def draw_logo(size: int) -> "Image.Image":
    """Cuadrado redondeado índigo con la nota en trazo blanco, como logo.svg (32x32)."""
    S = 8  # se dibuja grande y se achica: Pillow no suaviza bordes por sí solo
    px = size * S
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = px / 32
    d.rounded_rectangle([0, 0, px - 1, px - 1], radius=int(8 * k), fill=LOGO_BG + (255,))

    # El <g> del SVG: translate(5.5 5.5) scale(.875), trazo 2.4, puntas redondas.
    def p(x, y):
        return ((x * 0.875 + 5.5) * k, (y * 0.875 + 5.5) * k)
    w = int(2.4 * 0.875 * k)
    white = (255, 255, 255, 255)
    for a, b in ((p(9, 18), p(9, 5)), (p(9, 5), p(21, 3)), (p(21, 3), p(21, 16))):
        d.line([a, b], fill=white, width=w)
        for c in (a, b):
            d.ellipse([c[0] - w / 2, c[1] - w / 2, c[0] + w / 2, c[1] + w / 2], fill=white)
    for cx, cy in ((6, 18), (18, 16)):
        x, y = p(cx, cy)
        r = 3 * 0.875 * k
        d.ellipse([x - r - w / 2, y - r - w / 2, x + r + w / 2, y + r + w / 2], fill=white)
        d.ellipse([x - r + w / 2, y - r + w / 2, x + r - w / 2, y + r - w / 2], fill=LOGO_BG + (255,))
    return img.resize((size, size), Image.LANCZOS)


def make_icon(size: int) -> Image.Image:
    return draw_logo(size).convert("RGB")


if __name__ == "__main__":
    sizes = [16, 32, 48, 256]
    images = [draw_logo(s) for s in sizes]
    out = Path(__file__).parent / "agent_icon.ico"
    images[0].save(out, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"Icono generado: {out}")

    # macOS: Pillow saca los tamaños del .icns a partir de una imagen grande.
    icns = out.with_suffix(".icns")
    draw_logo(1024).save(icns, format="ICNS")
    print(f"Icono generado: {icns}")
