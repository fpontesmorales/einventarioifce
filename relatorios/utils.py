from typing import Tuple, List, Dict
import csv
from django.http import HttpResponse

# --- Metadados fixos da campanha (pode centralizar em outro lugar se preferir) ---
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
    """Retorna True se QUALQUER um dos campos listados for avaliado como 'verdadeiro'."""
    for n in names:
        val = getattr(obj, n, None)
        if _is_truthy(val):
            return True
    return False

def _false(obj, *names) -> bool:
    """Retorna True se algum campo listado for explicitamente falso/negativo."""
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
    """
    Valor unitário baseado no campo de aquisição do Bem.
    """
    v = get_attr(bem, "valor_aquisicao", "VALOR_AQUISICAO", default=0) or 0
    try:
        return float(v)
    except Exception:
        return 0.0


# =============================================================================
# Mapeamento de status da vistoria (tolerante a modelos diferentes)
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


# Mantido por compatibilidade (não é necessário usar diretamente, pois coletar_divergencias já é robusta)
DIVERGENCIA_FIELDS = [
    ("div_localizacao", "localização"),
    ("div_serie", "série"),
    ("div_descricao", "descrição"),
    ("div_estado", "estado"),
    ("div_responsavel", "responsável"),
    ("div_etiqueta_rasurada", "etiqueta (rasurada)"),
    ("div_etiqueta_dupla", "etiqueta (dupla)"),
    ("div_etiqueta_fora_padrao", "etiqueta (fora do padrão)"),
    ("div_etiqueta_dificil_visualizacao", "etiqueta (difícil visualização)"),
]


# =============================================================================
# Coleta de divergências (rótulos) – tolerante + compara com Bem (SUAP)
# =============================================================================
def coletar_divergencias(vb) -> List[str]:
    """
    Retorna lista de rótulos de divergências detectadas para um VistoriaBem,
    checando múltiplos nomes de campos e também diferenças em relação ao cadastro do Bem.

    Exemplos retornados:
      'localização', 'série', 'descrição', 'marca/modelo', 'responsável', 'estado',
      'etiqueta (rasurada)', 'etiqueta (dupla)', 'etiqueta (fora do padrão)',
      'etiqueta (difícil visualização)', 'tombamento divergente', 'não encontrado'.
    """
    out: List[str] = []

    # 0) Não encontrado vira um tipo específico (se você NÃO quiser isso, remova este bloco)
    if is_nao_encontrado(vb):
        out.append("não encontrado")

    bem = getattr(vb, "bem", None)

    # 1) Localização
    if _true(vb, "div_localizacao", "localizacao_divergente", "divergencia_localizacao", "loc_diferente"):
        out.append("localização")
    else:
        sala_v = _get(vb, "sala_vistoriada", "sala_lida")
        sala_b = _get(bem, "sala", "sala_atual")
        if sala_v and sala_b and str(sala_v) != str(sala_b):
            out.append("localização")

    # 2) Série
    if _true(vb, "div_serie", "serie_divergente", "serial_divergente"):
        out.append("série")
    else:
        serie_v = _get(vb, "numero_serie_lido", "serie_encontrada", "serial_encontrado")
        serie_b = _get(bem, "numero_serie", "serie")
        if serie_v and serie_b and _to_str(serie_v) != _to_str(serie_b):
            out.append("série")

    # 3) Descrição
    if _true(vb, "div_descricao", "descricao_divergente"):
        out.append("descrição")
    else:
        desc_v = _get(vb, "descricao_encontrada")
        desc_b = _get(bem, "descricao", "descricao_suap")
        if desc_v and desc_b and _to_str(desc_v) != _to_str(desc_b):
            out.append("descrição")

    # 4) Marca/Modelo
    if _true(vb, "marca_modelo_divergente", "div_marca_modelo"):
        out.append("marca/modelo")
    else:
        marca_v = _get(vb, "marca_encontrada")
        marca_b = _get(bem, "marca")
        modelo_v = _get(vb, "modelo_encontrado")
        modelo_b = _get(bem, "modelo")
        if (marca_v and marca_b and _to_str(marca_v) != _to_str(marca_b)) or \
           (modelo_v and modelo_b and _to_str(modelo_v) != _to_str(modelo_b)):
            out.append("marca/modelo")

    # 5) Responsável
    if _true(vb, "div_responsavel", "responsavel_divergente"):
        out.append("responsável")
    else:
        resp_v = _get(vb, "responsavel_lido", "responsavel_encontrado")
        resp_b = _get(bem, "responsavel")
        if resp_v and resp_b and _to_str(resp_v) != _to_str(resp_b):
            out.append("responsável")

    # 6) Estado (apenas marca quando houver indício explícito de diferença)
    if _true(vb, "div_estado", "estado_divergente"):
        out.append("estado")

    # 7) Etiquetas (marcadores)
    if _true(vb, "div_etiqueta_rasurada", "etiqueta_rasurada"):
        out.append("etiqueta (rasurada)")
    if _true(vb, "div_etiqueta_dupla", "etiqueta_dupla"):
        out.append("etiqueta (dupla)")
    if _true(vb, "div_etiqueta_fora_padrao", "etiqueta_fora_padrao"):
        out.append("etiqueta (fora do padrão)")
    if _true(vb, "div_etiqueta_dificil_visualizacao", "etiqueta_dificil_visualizacao"):
        out.append("etiqueta (difícil visualização)")
    if _false(vb, "etiqueta_possui", "tem_etiqueta", "possui_etiqueta"):
        out.append("etiqueta (ausente)")

    # 8) Tombamento divergente
    if _true(vb, "tombamento_divergente", "div_tombamento"):
        out.append("tombamento divergente")
    else:
        tombo_v = _get(vb, "tombamento_lido", "tombo_encontrado")
        tombo_b = _get(bem, "tombamento", "numero_tombamento")
        if tombo_v and tombo_b and _to_str(tombo_v) != _to_str(tombo_b):
            out.append("tombamento divergente")

    # 9) Texto livre (aceita ; , / como separadores)
    extra = _get(vb, "divergencias_texto", "divergencia_texto",
                 "observacao_divergencia", "observacoes", "observacao")
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
    Se quiser que 'não encontrado' apareça no Mapa de NC, troque pelo:
        return bool(coletar_divergencias(vb))
    """
    tipos = coletar_divergencias(vb)
    return any(t.lower() != "não encontrado" for t in tipos)


# =============================================================================
# Diferenças detalhadas SUAP × Vistoria (para o Mapa de NC)
# =============================================================================
def _nome_setor(setor) -> str:
    return _to_str(get_attr(setor, "nome", "descricao", default=""))


def _nome_sala(sala) -> str:
    return _to_str(get_attr(sala, "nome", "descricao", default=""))


def _str_localizacao(obj) -> str:
    """
    Converte (setor/sala) em 'Setor / Sala' quando possível, tolerando variações de modelo.
    """
    sala = get_attr(obj, "sala", "sala_atual", default=None)
    if sala:
        setor = get_attr(sala, "setor", default=None)
        setor_nome = _nome_setor(setor) if setor else ""
        sala_nome = _nome_sala(sala)
        if setor_nome and sala_nome:
            return f"{setor_nome} / {sala_nome}"
        return sala_nome or setor_nome
    # Alguns modelos gravam texto diretamente em campos como setor_nome/unidade/local
    return _to_str(get_attr(obj, "setor_nome", "unidade", "local", default=""))


def _str_responsavel(obj) -> str:
    """
    Recupera um nome de responsável amigável tanto do Bem quanto da Vistoria.
    """
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
    return _to_str(get_attr(obj, "responsavel_lido", "responsavel_encontrado", default=""))


def diferencas_detalhadas(vb) -> List[Dict[str, str]]:
    """
    Retorna uma lista de diffs SUAP × Vistoria para o VistoriaBem:

    Exemplo:
      [
        {'campo': 'localização', 'suap': 'Setor A / Sala 101', 'vistoria': 'Setor B / Sala 202'},
        {'campo': 'série', 'suap': 'ABC123', 'vistoria': 'XYZ987'},
        ...
      ]

    Inclui também sinais de etiqueta e 'não encontrado' quando aplicável.
    """
    out: List[Dict[str, str]] = []
    bem = getattr(vb, "bem", None)

    # 1) Localização
    suap_loc = _str_localizacao(bem) if bem else ""
    vist_loc = _str_localizacao(vb)
    if suap_loc and vist_loc and suap_loc != vist_loc:
        out.append({"campo": "localização", "suap": suap_loc, "vistoria": vist_loc})

    # 2) Série
    suap_serie = _to_str(get_attr(bem, "numero_serie", "serie", default="")) if bem else ""
    vist_serie = _to_str(get_attr(vb, "numero_serie_lido", "serie_encontrada", "serial_encontrado", default=""))
    if suap_serie and vist_serie and suap_serie != vist_serie:
        out.append({"campo": "série", "suap": suap_serie, "vistoria": vist_serie})

    # 3) Descrição
    suap_desc = _to_str(get_attr(bem, "descricao", "descricao_suap", default="")) if bem else ""
    vist_desc = _to_str(get_attr(vb, "descricao_encontrada", default=""))
    if suap_desc and vist_desc and suap_desc != vist_desc:
        out.append({"campo": "descrição", "suap": suap_desc, "vistoria": vist_desc})

    # 4) Marca/Modelo
    suap_marca = _to_str(get_attr(bem, "marca", default="")) if bem else ""
    suap_modelo = _to_str(get_attr(bem, "modelo", default="")) if bem else ""
    vist_marca = _to_str(get_attr(vb, "marca_encontrada", default=""))
    vist_modelo = _to_str(get_attr(vb, "modelo_encontrado", default=""))
    if (suap_marca and vist_marca and suap_marca != vist_marca) or (suap_modelo and vist_modelo and suap_modelo != vist_modelo):
        out.append({
            "campo": "marca/modelo",
            "suap": f"{suap_marca} {suap_modelo}".strip(),
            "vistoria": f"{vist_marca} {vist_modelo}".strip()
        })

    # 5) Responsável
    suap_resp = _str_responsavel(bem) if bem else ""
    vist_resp = _str_responsavel(vb)
    if suap_resp and vist_resp and suap_resp != vist_resp:
        out.append({"campo": "responsável", "suap": suap_resp, "vistoria": vist_resp})

    # 6) Tombamento
    suap_tomb = _to_str(get_attr(bem, "tombamento", "numero_tombamento", default="")) if bem else ""
    vist_tomb = _to_str(get_attr(vb, "tombamento_lido", "tombo_encontrado", default=""))
    if suap_tomb and vist_tomb and suap_tomb != vist_tomb:
        out.append({"campo": "tombamento", "suap": suap_tomb, "vistoria": vist_tomb})

    # 7) Estado (se comparável)
    suap_estado = _to_str(get_attr(bem, "estado", "estado_conservacao", default="")) if bem else ""
    vist_estado = _to_str(get_attr(vb, "estado", "estado_conservacao", default=""))
    if suap_estado and vist_estado and suap_estado != vist_estado:
        out.append({"campo": "estado", "suap": suap_estado, "vistoria": vist_estado})

    # 8) Etiquetas (sinais)
    etiqueta_flags = [
        ("etiqueta (rasurada)", ("div_etiqueta_rasurada", "etiqueta_rasurada")),
        ("etiqueta (dupla)", ("div_etiqueta_dupla", "etiqueta_dupla")),
        ("etiqueta (fora do padrão)", ("div_etiqueta_fora_padrao", "etiqueta_fora_padrao")),
        ("etiqueta (difícil visualização)", ("div_etiqueta_dificil_visualizacao", "etiqueta_dificil_visualizacao")),
    ]
    for rotulo, campos in etiqueta_flags:
        for c in campos:
            val = get_attr(vb, c, default=None)
            if _is_truthy(val):
                out.append({"campo": rotulo, "suap": "", "vistoria": "sinalizado"})
                break

    # 9) Não encontrado
    if is_nao_encontrado(vb):
        # suap_base útil para referência (tomb/desc/local)
        suap_base = suap_tomb or suap_desc or suap_loc
        out.append({"campo": "não encontrado", "suap": suap_base, "vistoria": "—"})

    # 10) Texto livre (observações)
    extra = get_attr(vb, "divergencias_texto", "divergencia_texto",
                     "observacao_divergencia", "observacoes", "observacao", default=None)
    if extra:
        out.append({"campo": "observação", "suap": "", "vistoria": _to_str(extra)})

    # Dedup por (campo, suap, vistoria) preservando ordem
    seen, result = set(), []
    for d in out:
        key = (d["campo"].lower(), d["suap"], d["vistoria"])
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


# =============================================================================
# Export CSV simples (sem depender de libs externas)
# =============================================================================
def export_csv(rows: List[List], headers: List[str], filename: str) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return resp
