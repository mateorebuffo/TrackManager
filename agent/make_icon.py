"""Generate agent_icon.ico from the same music-note image used in the app."""
from pathlib import Path
from PIL import Image, ImageDraw


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.ellipse([int(8*s), int(38*s), int(28*s), int(56*s)], fill=(255, 255, 255))
    d.ellipse([int(34*s), int(30*s), int(54*s), int(48*s)], fill=(255, 255, 255))
    d.rectangle([int(24*s), int(8*s), int(30*s), int(44*s)], fill=(255, 255, 255))
    d.rectangle([int(50*s), int(4*s), int(56*s), int(36*s)], fill=(255, 255, 255))
    d.rectangle([int(24*s), int(8*s), int(56*s), int(14*s)], fill=(255, 255, 255))

    bg = Image.new("RGBA", (size, size), (37, 99, 235, 255))
    bg.paste(img, mask=img)
    return bg.convert("RGB")


if __name__ == "__main__":
    sizes = [16, 32, 48, 256]
    images = [make_icon(s) for s in sizes]
    out = Path(__file__).parent / "agent_icon.ico"
    images[0].save(out, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"Icono generado: {out}")
