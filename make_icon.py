"""
Gera icon.icns (macOS) e icon.ico (Windows) a partir do Tracker-logo.svg.

Dependências:
  - Pillow  (pip install Pillow)
  - macOS: rsvg-convert  (brew install librsvg)
  - macOS: iconutil       (nativo)
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SVG       = SCRIPT_DIR / "Tracker-logo.svg"
PNG       = SCRIPT_DIR / "icon.png"
ICNS      = SCRIPT_DIR / "icon.icns"
ICO       = SCRIPT_DIR / "icon.ico"
BG        = (15, 23, 42, 255)   # #0f172a


def ensure_pillow():
    try:
        import PIL  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=True)


# ── SVG → PNG ─────────────────────────────────────────────────────────────────

def via_rsvg(width=2048) -> bool:
    """macOS/Linux: rsvg-convert renderiza o SVG com fidelidade total."""
    r = subprocess.run(["rsvg-convert", "-w", str(width), str(SVG), "-o", str(PNG)],
                       capture_output=True)
    return r.returncode == 0 and PNG.exists()


def svg_to_square_png():
    """Converte SVG → PNG quadrado 1024×1024 com fundo escuro."""
    from PIL import Image

    converted = False
    if sys.platform != "win32":
        converted = via_rsvg(width=2048)

    if converted:
        wide = Image.open(PNG).convert("RGBA")
        size = 1024
        canvas = Image.new("RGBA", (size, size), BG)
        ratio  = 920 / wide.width
        new_h  = max(1, int(wide.height * ratio))
        resized = wide.resize((920, new_h), Image.LANCZOS)
        canvas.paste(resized, ((size - 920) // 2, (size - new_h) // 2), resized)
        canvas.save(PNG)
        print("[OK] icon.png  ← rsvg-convert")
    else:
        # Fallback: fundo escuro + texto via Pillow
        from PIL import ImageDraw, ImageFont
        size   = 1024
        canvas = Image.new("RGBA", (size, size), BG)
        draw   = ImageDraw.Draw(canvas)
        font   = None
        for path in [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]:
            try:
                font = ImageFont.truetype(path, 170)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()
        text = "TRACKER"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2), text,
                  fill=(255, 255, 255), font=font)
        canvas.save(PNG)
        print("[OK] icon.png  ← Pillow (fallback)")


# ── PNG → ICNS (macOS) ────────────────────────────────────────────────────────

def to_icns():
    from PIL import Image
    iconset = SCRIPT_DIR / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    src = Image.open(PNG).convert("RGBA")
    for s in (16, 32, 64, 128, 256, 512):
        src.resize((s,    s   ), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
        src.resize((s*2,  s*2 ), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
    r = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)])
    for f in iconset.iterdir():
        f.unlink()
    iconset.rmdir()
    if r.returncode == 0:
        print(f"[OK] icon.icns ← iconutil")
    else:
        print("[AVISO] iconutil falhou — build continuará sem ícone personalizado")


# ── PNG → ICO (Windows) ───────────────────────────────────────────────────────

def to_ico():
    from PIL import Image
    src  = Image.open(PNG).convert("RGBA")
    imgs = [src.resize((s, s), Image.LANCZOS) for s in (16, 32, 48, 64, 128, 256)]
    imgs[0].save(ICO, format="ICO", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)],
                 append_images=imgs[1:])
    print(f"[OK] icon.ico  ← Pillow")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ensure_pillow()
    print("[INFO] Gerando ícone do app...")
    svg_to_square_png()
    if sys.platform == "darwin":
        to_icns()
    elif sys.platform == "win32":
        to_ico()
    print("[INFO] Ícone pronto.")


if __name__ == "__main__":
    main()
