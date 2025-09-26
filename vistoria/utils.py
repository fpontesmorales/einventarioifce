# utils.py
# -----------------------------------------------------------------------------
# Watermark e compressão de imagens com fonte consistente cross-plataforma.
# Prioriza uma TTF empacotada no projeto em BASE_DIR/fonts/DejaVuSans-Bold.ttf.
# Se não encontrar, tenta variáveis de ambiente e caminhos padrão por SO.
# -----------------------------------------------------------------------------

from io import BytesIO
import os
import subprocess
from pathlib import Path
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps
from django.core.files.base import ContentFile
from django.conf import settings


# ---------------------------------------------------------------------
# Configuração de fontes
# ---------------------------------------------------------------------

# 1) Diretórios locais do projeto onde você pode colocar a TTF empacotada
BASE_DIR = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[2])).resolve()
_LOCAL_FONT_DIRS = [
    BASE_DIR / "fonts",                  # << RECOMENDADO: coloque aqui DejaVuSans-Bold.ttf
    BASE_DIR / "static" / "fonts",
    BASE_DIR / "assets" / "fonts",
    Path(__file__).resolve().parent / "fonts",
    Path(__file__).resolve().parent / "fonte",
]

# 2) Candidatos por SO (fallbacks)
_FONT_CANDIDATES = [
    # Linux (Debian/Ubuntu)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",          # Arial Bold
    r"C:\Windows\Fonts\seguisb.ttf",          # Segoe UI Semibold
    r"C:\Windows\Fonts\segoeuib.ttf",         # Segoe UI Semibold Italic
    r"C:\Windows\Fonts\calibrib.ttf",         # Calibri Bold
    # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _find_local_font_path() -> str:
    """
    Procura PRIMEIRO por uma TTF empacotada no projeto.
    Preferências: nomes comuns Bold; senão, qualquer .ttf/.otf.
    """
    preferred_names = [
        "DejaVuSans-Bold.ttf",
        "Inter-SemiBold.ttf",
        "Arial-Bold.ttf",
        "Arial Bold.ttf",
    ]
    for d in _LOCAL_FONT_DIRS:
        if not d.exists():
            continue
        # 1) nomes preferidos
        for name in preferred_names:
            p = d / name
            if p.exists():
                return str(p)
        # 2) qualquer *Bold*.ttf/otf
        bolds = sorted(list(d.glob("*[Bb]old*.ttf")) + list(d.glob("*[Bb]old*.otf")))
        if bolds:
            return str(bolds[0])
        # 3) qualquer ttf/otf
        any_ttf = sorted(list(d.glob("*.ttf")) + list(d.glob("*.otf")))
        if any_ttf:
            return str(any_ttf[0])
    return ""


@lru_cache(maxsize=1)
def _resolve_font_path() -> str:
    """
    Ordem de resolução:
    1) WATERMARK_FONT_PATH (env)
    2) fonte local empacotada no projeto (./fonts/…)
    3) caminhos padrão por SO
    4) fontconfig (fc-match) no Linux
    5) tentativa por nomes soltos
    """
    # 1) override por env
    env = os.getenv("WATERMARK_FONT_PATH", "").strip()
    if env and Path(env).exists():
        return env

    # 2) local no projeto (BASE_DIR/fonts, etc.)
    local = _find_local_font_path()
    if local:
        return local

    # 3) caminhos padrão por SO
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p

    # 4) fontconfig no Linux (se disponível)
    try:
        cmd = "command -v fc-match >/dev/null 2>&1 && fc-match -f '%{file}' 'DejaVu Sans:style=Bold' || true"
        out = subprocess.run(["sh", "-lc", cmd], capture_output=True, text=True, timeout=1.5).stdout.strip()
        if out and Path(out).exists():
            return out
        cmd2 = "command -v fc-match >/dev/null 2>&1 && fc-match -f '%{file}' ':style=Bold' || true"
        out2 = subprocess.run(["sh", "-lc", cmd2], capture_output=True, text=True, timeout=1.5).stdout.strip()
        if out2 and Path(out2).exists():
            return out2
    except Exception:
        pass

    # 5) nomes soltos (raramente necessário)
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            ImageFont.truetype(name, size=32)
            return name
        except Exception:
            pass

    # Nada encontrado
    return ""


def _load_font(size: int):
    """
    Carrega uma TTF real sempre que possível.
    Só cai no bitmap (load_default) se nada existir.
    """
    path = _resolve_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass

    # Últimos recursos (podem ficar serrilhados se ampliados)
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        f = ImageFont.load_default()
        # marcar como bitmap (se quiser usar em cálculos)
        try:
            f._is_bitmap = True  # type: ignore[attr-defined]
        except Exception:
            pass
        return f


# ---------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------

def _wrap_text_to_width(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw):
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


def _line_height(font: ImageFont.ImageFont) -> int:
    """
    Altura real de uma linha usando bbox do Pillow (funciona em TTF e bitmap).
    Evita divergências entre Windows/Linux ao confiar só em getmetrics().
    """
    dummy = Image.new("L", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), "Ag", font=font)
    return max(1, bbox[3] - bbox[1])


# ---------------------------------------------------------------------
# Função principal: watermark + compress
# ---------------------------------------------------------------------

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
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([(0, h - band_h), (w, h)], fill=(0, 0, 0, 200))  # preto translúcido mais opaco

    # Texto e tamanho inicial (80% da faixa)
    text = (text or "").strip() or "E-Inventário"
    font_size = max(24, int(band_h * 0.80))
    font = _load_font(font_size)

    # Cabe na largura? Se não, reduz até caber em 2 linhas confortáveis
    max_text_w = int(w * 0.94)  # margens 3% de cada lado
    min_font = 18

    def total_height(lines, f):
        line_h = _line_height(f)
        inter = max(4, int((getattr(f, "size", line_h)) * 0.2))
        return len(lines) * line_h + (inter if len(lines) > 1 else 0)

    while True:
        if draw.textlength(text, font=font) > max_text_w:
            lines = _wrap_text_to_width(text, font, max_text_w, draw)
        else:
            lines = [text]

        if total_height(lines, font) <= band_h or font_size <= min_font:
            break
        font_size -= 2
        font = _load_font(font_size)

    # Coordenadas: centralizado horizontalmente; vertical central dentro da faixa
    line_h = _line_height(font)
    inter = max(4, int((getattr(font, "size", line_h)) * 0.2))
    block_h = total_height(lines, font)
    start_y = h - band_h + max(2, (band_h - block_h) // 2)

    # Desenha com stroke (contorno) para aumentar a legibilidade
    stroke_w = max(2, int((getattr(font, "size", line_h)) * 0.06))  # ~6% do tamanho da fonte
    for i, line in enumerate(lines):
        line_w = draw.textlength(line, font=font)
        x = max(10, (w - line_w) // 2)
        y = start_y + i * (line_h + (inter if i > 0 else 0))
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_fill=(0, 0, 0, 255),
            stroke_width=stroke_w,
        )

    # Compõe
    out = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")

    # JPEG otimizado
    buf = BytesIO()
    out.save(buf, format="JPEG", quality=85, optimize=True, progressive=True)
    buf.seek(0)
    return ContentFile(buf.read())
