from typing import Tuple, List, Dict, Optional
import os
import csv
from datetime import date, datetime

from django.http import HttpResponse
from django.conf import settings

# Pillow já está no requirements; ainda assim tratamos fallback
try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore

# =============================================================================
# Metadados fixos (fallback) – podem ser sobrescritos pelo Inventário ativo
# =============================================================================
SEI = "23486.002518/2025-34"
PORTARIA = "PORTARIA Nº 9367/GAB-CAU/DG-CAU/CAUCAIA, DE 02 DE OUTUBRO DE 2025"
PERIODO = "02/10/2025 a 31/12/2025"


# =============================================================================
# Helpers genéricos
# =============================================================================
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
# Coleta de divergências (rótulos) – foco em etiqueta AUSENTE e diffs relevantes
# =============================================================================
def coletar_divergencias(vb) -> List[str]:
    """
    Retorna lista de rótulos de divergências detectadas para um VistoriaBem,
    checando múltiplos nomes de campos e também diferenças em relação ao Bem.

    Observações de regra:
      - Localização: só marca quando NÃO confere_local e há valor observado diferente do SUAP.
      - Série, Descrição, Responsável: só marcam quando NÃO conferem e há valor observado diferente.
      - Estado: marca quando NÃO confere_estado (não exige comparação textual).
      - Etiqueta: **somente 'etiqueta (ausente)'** quando etiqueta_possui == False.
      - 'não encontrado' é retornado como classificação própria.
    """
    out: List[str] = []

    if is_nao_encontrado(vb):
        out.append("não encontrado")

    bem = getattr(vb, "bem", None)

    # 1) Localização (prioriza local vistoriado quando NÃO confere)
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

    # 4) Responsável (texto)
    suap_resp = (_get(bem, "setor_responsavel", "responsavel", default="") or "").strip() if bem else ""
    vist_resp = (_get(vb, "responsavel_obs", "responsavel_lido", "responsavel_encontrado", default="") or "").strip()
    if vist_resp and not _get(vb, "confere_responsavel", default=True) and vist_resp != suap_resp:
        out.append("responsável")

    # 5) Estado (apenas quando marcado como não confere)
    if not _get(vb, "confere_estado", default=True):
        out.append("estado")

    # 6) Etiqueta (só AUSENTE)
    if _false(vb, "etiqueta_possui", "tem_etiqueta", "possui_etiqueta"):
        out.append("etiqueta (ausente)")

    # 7) Tombamento (se houver leitura que difere do SUAP)
    suap_tombo = (_get(bem, "tombamento", "numero_tombamento", default="") or "").strip() if bem else ""
    vist_tombo = (_get(vb, "tombamento_lido", "tombo_encontrado", default="") or "").strip()
    if vist_tombo and suap_tombo and vist_tombo != suap_tombo:
        out.append("tombamento divergente")

    # 8) Texto livre (observações)
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
    """
    Considera 'divergente' quando houver QUALQUER divergência além de apenas 'não encontrado'.
    """
    tipos = coletar_divergencias(vb)
    return any(t.lower() != "não encontrado" for t in tipos)


# =============================================================================
# Diferenças detalhadas SUAP × Vistoria (para o Mapa de NC)
# =============================================================================
def _str_responsavel(obj) -> str:
    """Recupera um nome de responsável amigável tanto do Bem quanto da Vistoria."""
    resp = get_attr(obj, "responsavel", default=None)
    if resp:
        for n in ("nome", "get_full_name", "first_name", "username", "__str__"):
            if hasattr(resp, n):
                try:
                    return _to_str(getattr(resp, n)() if callable(getattr(resp, n)) else getattr(resp, n))
                except Exception:
                    continue
        return _to_str(resp)
    # Campos de texto alternativos presentes na Vistoria
    return _to_str(get_attr(obj, "responsavel_lido", "responsavel_encontrado", "responsavel_obs", default=""))


def diferencas_detalhadas(vb) -> List[Dict[str, str]]:
    """
    Retorna lista de diffs SUAP × Vistoria (apenas quando há diferença real).
    Campo 'localização' usa "Sala (Bloco)" e PRIORIZA local observado quando NÃO confere_local.
    Etiqueta: acusa apenas AUSENTE.
    """
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

    # Responsável (texto)
    suap_resp = (_get(bem, "setor_responsavel", default="") or "").strip() if bem else ""
    vist_resp = (_get(vb, "responsavel_obs", "responsavel_lido", "responsavel_encontrado", default="") or "").strip()
    if vist_resp and not _get(vb, "confere_responsavel", default=True) and vist_resp != suap_resp:
        out.append({"campo": "responsável", "suap": suap_resp or "—", "vistoria": vist_resp})

    # Estado (apenas quando NÃO confere)
    suap_estado = (_get(bem, "estado", "estado_conservacao", default="") or "").strip() if bem else ""
    if not _get(vb, "confere_estado", default=True):
        vist_estado = (_get(vb, "estado_obs", "estado", "estado_conservacao", default="") or "").strip()
        out.append({"campo": "estado", "suap": suap_estado or "—", "vistoria": vist_estado or "—"})

    # Tombamento divergente (se houve leitura divergente)
    suap_tombo = (_get(bem, "tombamento", "numero_tombamento", default="") or "").strip() if bem else ""
    vist_tombo = (_get(vb, "tombamento_lido", "tombo_encontrado", default="") or "").strip()
    if vist_tombo and suap_tombo and vist_tombo != suap_tombo:
        out.append({"campo": "tombamento", "suap": suap_tombo, "vistoria": vist_tombo})

    # Etiqueta AUSENTE
    if _false(vb, "etiqueta_possui", "tem_etiqueta", "possui_etiqueta"):
        out.append({"campo": "etiqueta (ausente)", "suap": "presente", "vistoria": "ausente"})

    # Não encontrado (como registro informativo)
    if is_nao_encontrado(vb):
        base = suap_tombo or suap_desc or suap_loc
        out.append({"campo": "não encontrado", "suap": base or "—", "vistoria": "—"})

    # Observações livres
    extra = _get(vb, "divergencias_texto", "divergencia_texto", "observacao_divergencia", "observacoes", "observacao", default=None)
    if extra:
        out.append({"campo": "observação", "suap": "", "vistoria": _to_str(extra)})

    # Dedup por (campo, suap, vistoria)
    seen, result = set(), []
    for d in out:
        key = (d["campo"].lower(), d.get("suap", ""), d.get("vistoria", ""))
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


# =============================================================================
# Thumbnails de fotos (salvos em MEDIA_ROOT/vistorias/_thumbs)
# =============================================================================
def thumbnail_url(filefield, size=(640, 640), quality=75) -> Optional[str]:
    """
    Gera/usa um JPG reduzido em MEDIA_ROOT/vistorias/_thumbs.
    Recria se a origem for mais nova. Fallback para a URL original.
    """
    if not filefield:
        return None
    try:
        src_path = filefield.path
        base, _ext = os.path.splitext(os.path.basename(src_path))
        rel_dir = "vistorias/_thumbs"
        thumb_name = f"{base}_{size[0]}x{size[1]}.jpg"
        thumb_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
        os.makedirs(thumb_dir, exist_ok=True)
        dst_path = os.path.join(thumb_dir, thumb_name)

        # Se Pillow indisponível, cai para URL original
        if Image is None:
            return filefield.url

        if (not os.path.exists(dst_path)) or (os.path.getmtime(dst_path) < os.path.getmtime(src_path)):
            with Image.open(src_path) as im:
                im = im.convert("RGB")
                im.thumbnail(size, Image.LANCZOS)
                im.save(dst_path, "JPEG", quality=quality, optimize=True)

        return settings.MEDIA_URL + f"{rel_dir}/{thumb_name}"
    except Exception:
        try:
            return filefield.url
        except Exception:
            return None


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
