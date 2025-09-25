from io import BytesIO
import os
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageOps
from django.core.files.base import ContentFile


# ---------- font resolver (robusto cross-plataforma) ----------
_FONT_CANDIDATES = [
    # Linux (Debian/Ubuntu)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",          # Arial Bold
    r"C:\Windows\Fonts\segoeuib.ttf",         # Segoe UI Semibold Italic (bom o bastante)
    r"C:\Windows\Fonts\seguisb.ttf",          # Segoe UI Semibold
    r"C:\Windows\Fonts\calibrib.ttf",         # Calibri Bold
    # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    # Último recurso: tenta a DejaVu embutida no Pillow (em algumas instalações funciona)
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        # Fallback mínimo (bitmap). Ainda assim vamos usar faixa grande + stroke.
        return ImageFont.load_default()


def _wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw):
    """Quebra o texto em até 2 linhas para caber em max_width."""
    words = text.split()
    if not words:
        return [""]

    lines = []
    cur = words[0]
    for w in words[1:]:
        test = cur + " " + w
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= 1:  # já temos 1 linha; a próxima será a 2ª (limite)
                break
    lines.append(cur)

    # Se ainda extrapolar a largura, coloca reticências na última linha
    if draw.textlength(lines[-1], font=font) > max_width:
        ell = "…"
        base = lines[-1]
        while base and draw.textlength(base + ell, font=font) > max_width:
            base = base[:-1]
        lines[-1] = (base + ell) if base else ell
    return lines[:2]


def watermark_and_compress(django_file, text: str) -> ContentFile:
    """
    Marca d’água ocupando ~15% do rodapé (faixa escura),
    texto grande centralizado (até 2 linhas), e JPEG otimizado.
    """
    # Carrega e corrige rotação por EXIF (fotos do celular)
    im = Image.open(django_file)
    im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size

    # Faixa inferior: 15% da altura (mínimo 80px)
    band_h = max(80, int(h * 0.15))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ov = ImageDraw.Draw(overlay)
    ov.rectangle([(0, h - band_h), (w, h)], fill=(0, 0, 0, 200))  # preto translúcido mais opaco

    # Texto e tamanho inicial (80% da faixa)
    text = (text or "").strip() or "E-Inventário"
    font_size = max(24, int(band_h * 0.80))
    font = _load_font(font_size)
    draw = ImageDraw.Draw(overlay)

    # Cabe na largura? Se não, reduz até caber em 2 linhas confortáveis
    max_text_w = int(w * 0.94)           # margens 3% de cada lado
    min_font = 18

    def total_height(lines, f):
        ascent, descent = (getattr(f, "getmetrics", lambda: (f.size, 0))())
        line_h = ascent + descent
        # Espaçamento entre linhas (20% do tamanho)
        return len(lines) * line_h + (max(4, int(f.size * 0.2)) if len(lines) > 1 else 0)

    lines = [text]
    while True:
        # Se uma linha passa da largura, quebra em até 2
        if draw.textlength(text, font=font) > max_text_w:
            lines = _wrap_text_to_width(text, font, max_text_w, draw)
        else:
            lines = [text]

        # Altura total do bloco de texto deve caber na faixa
        if total_height(lines, font) <= band_h or font_size <= min_font:
            break
        font_size -= 2
        font = _load_font(font_size)

    # Coordenadas: centralizado horizontalmente; vertical central dentro da faixa
    ascent, descent = (getattr(font, "getmetrics", lambda: (font.size, 0))())
    line_h = ascent + descent
    inter = max(4, int(font.size * 0.2))
    block_h = total_height(lines, font)

    start_y = h - band_h + max(2, (band_h - block_h) // 2)

    # Desenha com stroke (contorno) para aumentar a legibilidade
    stroke_w = max(2, int(font.size * 0.06))  # ~6% do tamanho da fonte
    for i, line in enumerate(lines):
        line_w = draw.textlength(line, font=font)
        x = max(10, (w - line_w) // 2)
        y = start_y + i * (line_h + (inter if i > 0 else 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255),
                  stroke_fill=(0, 0, 0, 255), stroke_width=stroke_w)

    # Compõe
    out = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")

    # JPEG otimizado
    buf = BytesIO()
    out.save(buf, format="JPEG", quality=85, optimize=True, progressive=True)
    buf.seek(0)
    return ContentFile(buf.read())
