from io import BytesIO
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont, ImageOps
import textwrap

def _load_font(px: int):
    # Tenta um TrueType legível; se falhar, usa o padrão do Pillow.
    try:
        return ImageFont.truetype("DejaVuSans.ttf", px)
    except Exception:
        try:
            return ImageFont.truetype("arial.ttf", px)
        except Exception:
            return ImageFont.load_default()

def watermark_and_compress(uploaded_file, watermark_text: str, max_width: int = 1600, quality: int = 85) -> ContentFile:
    """
    - Corrige orientação EXIF, limita largura (max_width).
    - Desenha uma faixa opaca semitransparente de ~10% da altura, na base da imagem.
    - Escreve o texto dentro da faixa, com quebra de linha automática.
    - Retorna ContentFile JPEG pronto para salvar no FileField.
    """
    img = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img).convert("RGB")

    # Resize
    w, h = img.size
    if w > max_width:
        ratio = max_width / float(w)
        img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)
        w, h = img.size

    # Faixa ~10% da altura
    band_h = max(int(h * 0.10), 60)
    pad = max(int(band_h * 0.12), 8)

    # Fonte proporcional
    font_px = max(int(band_h * 0.45), 18)
    font = _load_font(font_px)

    # Texto (quebra por largura disponível)
    text = (watermark_text or "").strip()
    if not text:
        text = "E-INVENTÁRIO"
    avail_w = w - (pad * 2)
    # wrap calculado por número de caracteres aproximando a largura média
    # (truetype mede melhor, mas textwrap por segurança)
    avg_char_w = font_px * 0.55
    max_chars = max(int(avail_w / avg_char_w), 12)
    lines = textwrap.wrap(text, width=max_chars)[:3]  # até 3 linhas
    draw = ImageDraw.Draw(img)

    # Fundo semi-opaco em toda a largura
    band = Image.new("RGBA", (w, band_h), (0, 0, 0, 140))
    img.paste(band, (0, h - band_h), band)

    # Desenha texto linha a linha (alinhado à esquerda)
    y = h - band_h + pad
    for line in lines:
        draw.text((pad, y), line, fill=(255, 255, 255), font=font)
        y += int(font_px * 1.15)

    # Exporta JPEG otimizado
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return ContentFile(buf.read())
