from typing import Tuple, List, Dict, Optional
import os
import csv
from datetime import date, datetime

from django.http import HttpResponse
from django.conf import settings

import re
_SAFE_FS_RE = re.compile(r'[\\/:*?"<>|]+')  # Windows + Unix

# Pillow já está no requirements; ainda assim tratamos fallback
try:
    from PIL import Image, features
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    features = None  # type: ignore

# =============================================================================
# Metadados fixos (fallback) – podem ser sobrescritos pelo Inventário ativo
# =============================================================================
SEI = "23486.002518/2025-34"
PORTARIA = "PORTARIA Nº 9367/GAB-CAU/DG-CAU/CAUCAIA, DE 02 DE OUTUBRO DE 2025"
PERIODO = "02/10/2025 a 31/12/2025"


# =============================================================================
# Helpers genéricos
# =============================================================================

def safe_fs_name(s: str, maxlen: int = 80) -> str:
    s = (s or "").strip()
    s = _SAFE_FS_RE.sub("-", s)
    return s[:maxlen] or "sem-nome"

def get_attr(obj, *names, default=None):
    """Retorna o primeiro atributo existente em 'names'."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _get(obj, *names, default=None):
    """Alias de get_attr (uso interno)."""
    return get_attr(obj, *names, default=default)


def _is_truthy(v) -> bool:
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in {"1", "true", "sim", "yes", "y"}:
        return True
    return False


def _true(obj, *names) -> bool:
    """True se QUALQUER campo listado representar um 'verdadeiro'."""
    for n in names:
        val = getattr(obj, n, None)
        if _is_truthy(val):
            return True
    return False


def _false(obj, *names) -> bool:
    """True se algum campo listado representar explicitamente 'falso'."""
    for n in names:
        v = getattr(obj, n, None)
        if v is False:
            return True
        if isinstance(v, str) and v.strip().lower() in {"0", "false", "nao", "não", "no", "n"}:
            return True
    return False


def _to_str(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _fmt_date_br(d: Optional[date | datetime]) -> Optional[str]:
    if not d:
        return None
    if isinstance(d, datetime):
        d = d.date()
    try:
        return d.strftime("%d/%m/%Y")
    except Exception:
        return None


# =============================================================================
# Metadados vindos do Inventário (quando existir)
# =============================================================================
def inventario_meta(inv) -> Dict[str, str]:
    """
    Tenta obter SEI, Portaria e Período do próprio Inventário (se possuir campos),
    com fallback nos valores fixos do módulo.
    Campos tentados:
      - SEI: processso_sei | sei
      - Portaria: portaria_texto | portaria
      - Período: (periodo_inicio|inicio|data_inicio) a (periodo_fim|fim|data_fim) | periodo_texto
    """
    sei = (get_attr(inv, "processo_sei", "sei", default=None) or "").strip()
    portaria = (get_attr(inv, "portaria_texto", "portaria", default=None) or "").strip()

    ini = get_attr(inv, "periodo_inicio", "inicio", "data_inicio", default=None)
    fim = get_attr(inv, "periodo_fim", "fim", "data_fim", default=None)
    periodo_texto = (get_attr(inv, "periodo_texto", default=None) or "").strip()

    if ini and fim:
        p_ini = _fmt_date_br(ini)
        p_fim = _fmt_date_br(fim)
        periodo = f"{p_ini} a {p_fim}" if p_ini and p_fim else ""
    else:
        periodo = periodo_texto

    return {
        "SEI": sei or SEI,
        "PORTARIA": portaria or PORTARIA,
        "PERIODO": periodo or PERIODO,
    }


# =============================================================================
# Conta contábil, valores, parsing
# =============================================================================
def parse_conta_contabil(texto: str) -> Tuple[str, str]:
    """
    Recebe algo como "12311.03.03 - Equipamentos de TI" e separa em (codigo, descricao).
    """
    if not texto:
        return "", ""
    parts = [p.strip() for p in str(texto).split("-", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return str(texto).strip(), ""


def valor_bem(bem) -> float:
    """Valor unitário baseado no campo de aquisição do Bem."""
    v = get_attr(bem, "valor_aquisicao", "VALOR_AQUISICAO", default=0) or 0
    try:
        return float(v)
    except Exception:
        return 0.0


# =============================================================================
# Status da vistoria (tolerante a modelos diferentes)
# =============================================================================
def is_encontrado(vb) -> bool:
    status = (_get(vb, "status", default="") or "").strip().upper()
    if status in {"ENCONTRADO", "FOUND", "OK"}:
        return True
    if _get(vb, "encontrado", default=None) is True:
        return True
    return False


def is_nao_encontrado(vb) -> bool:
    status = (_get(vb, "status", default="") or "").strip().upper()
    if status in {"NAO_ENCONTRADO", "NÃO_ENCONTRADO", "NOT_FOUND"}:
        return True
    if _get(vb, "nao_encontrado", default=None) is True:
        return True
    return False


# =============================================================================
# Local (Sala/Bloco) – SUAP e Vistoria
# =============================================================================
def _fmt_sala_bloco(nome: Optional[str], bloco: Optional[str]) -> str:
    nome = (nome or "").strip()
    bloco = (bloco or "").strip()
    if nome and bloco:
        return f"{nome} ({bloco})"
    return nome or bloco or "—"


def _sala_bloco_suap(bem) -> str:
    """
    Interpreta texto de sala do SUAP como "Sala (Bloco)" quando possível.
    """
    try:
        from vistoria.models import split_sala_bloco  # lazy import
    except Exception:  # pragma: no cover
        split_sala_bloco = None  # type: ignore

    sala_txt = (get_attr(bem, "sala", default="") or "").strip()
    if split_sala_bloco:
        nome, bloco = split_sala_bloco(sala_txt)
        return _fmt_sala_bloco(nome, bloco)
    return sala_txt or "—"


def _sala_bloco_vist(vb) -> Optional[str]:
    """
    Usa sala/bloco OBSERVADOS na vistoria (quando houver), formato "Sala (Bloco)".
    """
    nome = (get_attr(vb, "sala_obs_nome") or "").strip() or None
    bloco = (get_attr(vb, "sala_obs_bloco") or "").strip() or None
    if not nome and not bloco:
        return None
    return _fmt_sala_bloco(nome, bloco)


# =============================================================================
# Coleta de divergências (rótulos)
# =============================================================================
def coletar_divergencias(vb) -> List[str]:
    out: List[str] = []

    if is_nao_encontrado(vb):
        out.append("não encontrado")

    bem = getattr(vb, "bem", None)

    # 1) Localização
    vist_loc = _sala_bloco_vist(vb)
    suap_loc = _sala_bloco_suap(bem) if bem else "—"
    if vist_loc and not _get(vb, "confere_local", default=True):
        if vist_loc != suap_loc:
            out.append("localização")

    # 2) Série
    suap_serie = (_get(bem, "numero_serie", "serie", default="") or "").strip() if bem else ""
    vist_serie = (_get(vb, "numero_serie_obs", "numero_serie_lido", "serie_encontrada", "serial_encontrado", default="") or "").strip()
    if vist_serie and not _get(vb, "confere_numero_serie", default=True) and vist_serie != suap_serie:
        out.append("série")

    # 3) Descrição
    suap_desc = (_get(bem, "descricao", "descricao_suap", default="") or "").strip() if bem else ""
    vist_desc = (_get(vb, "descricao_obs", "descricao_encontrada", default="") or "").strip()
    if vist_desc and not _get(vb, "confere_descricao", default=True) and vist_desc != suap_desc:
        out.append("descrição")

    # 4) Responsável
    suap_resp = (_get(bem, "carga_atual", default="") or "").strip() if bem else ""
    vist_resp = (_get(vb, "responsavel_obs", "responsavel_lido", "responsavel_encontrado", default="") or "").strip()
    if vist_resp and not _get(vb, "confere_responsavel", default=True) and vist_resp != suap_resp:
        out.append("responsável")

    # 5) Estado
    if not _get(vb, "confere_estado", default=True):
        out.append("estado")

    # 6) Etiqueta AUSENTE
    if _false(vb, "etiqueta_possui", "tem_etiqueta", "possui_etiqueta"):
        out.append("etiqueta (ausente)")

    # 7) Tombamento divergente
    suap_tombo = (_get(bem, "tombamento", "numero_tombamento", default="") or "").strip() if bem else ""
    vist_tombo = (_get(vb, "tombamento_lido", "tombo_encontrado", default="") or "").strip()
    if vist_tombo and suap_tombo and vist_tombo != suap_tombo:
        out.append("tombamento divergente")

    # 8) Observações livres
    extra = _get(vb, "divergencias_texto", "divergencia_texto", "observacao_divergencia", "observacoes", "observacao", default=None)
    if extra:
        parts = [p.strip() for p in str(extra).replace("/", ";").replace(",", ";").split(";") if p.strip()]
        out.extend(parts)

    # Dedup mantendo ordem
    seen, limp = set(), []
    for t in out:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            limp.append(t)

    if not limp:
        if is_nao_encontrado(vb):
            return ["não encontrado"]
        return ["divergência (não classificada)"]
    return limp


def is_divergente(vb) -> bool:
    tipos = coletar_divergencias(vb)
    return any(t.lower() != "não encontrado" for t in tipos)


# =============================================================================
# Diferenças detalhadas SUAP × Vistoria
# =============================================================================
def _str_responsavel(obj) -> str:
    resp = get_attr(obj, "responsavel", default=None)
    if resp:
        for n in ("nome", "get_full_name", "first_name", "username", "__str__"):
            if hasattr(resp, n):
                try:
                    return _to_str(getattr(resp, n)() if callable(getattr(resp, n)) else getattr(resp, n))
                except Exception:
                    continue
        return _to_str(resp)
    return _to_str(get_attr(obj, "responsavel_lido", "responsavel_encontrado", "responsavel_obs", default=""))


def diferencas_detalhadas(vb) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    bem = getattr(vb, "bem", None)

    # Localização
    vist_loc = _sala_bloco_vist(vb)
    suap_loc = _sala_bloco_suap(bem) if bem else "—"
    if vist_loc and not _get(vb, "confere_local", default=True) and vist_loc != suap_loc:
        out.append({"campo": "localização", "suap": suap_loc, "vistoria": vist_loc})

    # Série
    suap_serie = (_get(bem, "numero_serie", "serie", default="") or "").strip() if bem else ""
    vist_serie = (_get(vb, "numero_serie_obs", "numero_serie_lido", "serie_encontrada", "serial_encontrado", default="") or "").strip()
    if vist_serie and not _get(vb, "confere_numero_serie", default=True) and vist_serie != suap_serie:
        out.append({"campo": "série", "suap": suap_serie or "—", "vistoria": vist_serie})

    # Descrição
    suap_desc = (_get(bem, "descricao", "descricao_suap", default="") or "").strip() if bem else ""
    vist_desc = (_get(vb, "descricao_obs", "descricao_encontrada", default="") or "").strip()
    if vist_desc and not _get(vb, "confere_descricao", default=True) and vist_desc != suap_desc:
        out.append({"campo": "descrição", "suap": suap_desc or "—", "vistoria": vist_desc})

    # Responsável
    suap_resp = (_get(bem, "carga_atual", default="") or "").strip() if bem else ""
    vist_resp = (_get(vb, "responsavel_obs", "responsavel_lido", "responsavel_encontrado", default="") or "").strip()
    if vist_resp and not _get(vb, "confere_responsavel", default=True) and vist_resp != suap_resp:
        out.append({"campo": "responsável", "suap": suap_resp or "—", "vistoria": vist_resp})

    # Estado
    suap_estado = (_get(bem, "estado", "estado_conservacao", default="") or "").strip() if bem else ""
    if not _get(vb, "confere_estado", default=True):
        vist_estado = (_get(vb, "estado_obs", "estado", "estado_conservacao", default="") or "").strip()
        out.append({"campo": "estado", "suap": suap_estado or "—", "vistoria": vist_estado or "—"})

    # Tombamento
    suap_tombo = (_get(bem, "tombamento", "numero_tombamento", default="") or "").strip() if bem else ""
    vist_tombo = (_get(vb, "tombamento_lido", "tombo_encontrado", default="") or "").strip()
    if vist_tombo and suap_tombo and vist_tombo != suap_tombo:
        out.append({"campo": "tombamento", "suap": suap_tombo, "vistoria": vist_tombo})

    # Etiqueta AUSENTE
    if _false(vb, "etiqueta_possui", "tem_etiqueta", "possui_etiqueta"):
        out.append({"campo": "etiqueta (ausente)", "suap": "presente", "vistoria": "ausente"})

    # Não encontrado
    if is_nao_encontrado(vb):
        base = suap_tombo or suap_desc or suap_loc
        out.append({"campo": "não encontrado", "suap": base or "—", "vistoria": "—"})

    # Observações
    extra = _get(vb, "divergencias_texto", "divergencia_texto", "observacao_divergencia", "observacoes", "observacao", default=None)
    if extra:
        out.append({"campo": "observação", "suap": "", "vistoria": _to_str(extra)})

    # Dedup
    seen, result = set(), []
    for d in out:
        key = (d["campo"].lower(), d.get("suap", ""), d.get("vistoria", ""))
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


# =============================================================================
# Thumbnails de fotos — WEBP minúsculo + fallback JPEG (MEDIA_ROOT/vistorias/_thumbs)
# =============================================================================
def _webp_supported() -> bool:
    try:
        return bool(features and features.check("webp"))
    except Exception:
        return False

def _thumb_dst(rel_dir: str, base: str, size: Tuple[int, int], ext: str) -> str:
    thumb_name = f"{base}_{size[0]}x{size[1]}.{ext}"
    thumb_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(thumb_dir, exist_ok=True)
    return os.path.join(thumb_dir, thumb_name)

def _thumb_url(rel_dir: str, base: str, size: Tuple[int, int], ext: str) -> str:
    thumb_name = f"{base}_{size[0]}x{size[1]}.{ext}"
    return settings.MEDIA_URL + f"{rel_dir}/{thumb_name}"

def _save_thumb(im: "Image.Image", dst_path: str, fmt: str, quality: int) -> None:
    if fmt.upper() == "WEBP":
        im.save(dst_path, "WEBP", quality=quality, method=4)  # method 0..6 (melhor compressão = 6; 4 é bom/custo baixo)
    else:
        # JPEG progressivo e otmizado
        im.save(dst_path, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=2)

def thumbnail_pair(filefield, small=(320, 320), medium=(640, 640), q_small=58, q_medium=60) -> Tuple[Optional[str], Optional[str]]:
    """
    Gera/retorna (thumb_url, print_url) para um filefield.
    Preferência WEBP; se indisponível, cai para JPEG.
    """
    if not filefield:
        return None, None
    if Image is None:
        # fallback: sem Pillow -> use a URL original
        try:
            u = filefield.url
            return u, u
        except Exception:
            return None, None

    try:
        src_path = filefield.path
        base, _ext = os.path.splitext(os.path.basename(src_path))
        rel_dir = "vistorias/_thumbs"

        use_webp = _webp_supported()
        ext_small = "webp" if use_webp else "jpg"
        ext_medium = "webp" if use_webp else "jpg"

        dst_small = _thumb_dst(rel_dir, base, small, ext_small)
        dst_medium = _thumb_dst(rel_dir, base, medium, ext_medium)

        needs_small = (not os.path.exists(dst_small)) or (os.path.getmtime(dst_small) < os.path.getmtime(src_path))
        needs_medium = (not os.path.exists(dst_medium)) or (os.path.getmtime(dst_medium) < os.path.getmtime(src_path))

        if needs_small or needs_medium:
            with Image.open(src_path) as im:
                # remove metadata + normaliza cores
                im = im.convert("RGB")

                if needs_small:
                    im_small = im.copy()
                    im_small.thumbnail(small, Image.LANCZOS)
                    _save_thumb(im_small, dst_small, "WEBP" if use_webp else "JPEG", q_small)

                if needs_medium:
                    im_medium = im.copy()
                    im_medium.thumbnail(medium, Image.LANCZOS)
                    _save_thumb(im_medium, dst_medium, "WEBP" if use_webp else "JPEG", q_medium)

        return _thumb_url(rel_dir, base, small, ext_small), _thumb_url(rel_dir, base, medium, ext_medium)

    except Exception:
        # Fallback: retorna a URL original
        try:
            u = filefield.url
            return u, u
        except Exception:
            return None, None

# Retrocompat (se algum código ainda importar thumbnail_url)
def thumbnail_url(filefield, size=(320, 320), quality=58) -> Optional[str]:
    t, _ = thumbnail_pair(filefield, small=size, medium=size, q_small=quality, q_medium=quality)
    return t


# =============================================================================
# Export CSV simples
# =============================================================================
def export_csv(rows: List[List], headers: List[str], filename: str) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return resp
