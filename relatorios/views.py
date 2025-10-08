from collections import defaultdict
import io
import zipfile
from datetime import datetime
import unicodedata
import re
from pathlib import Path

from django.conf import settings
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.http import HttpRequest, StreamingHttpResponse, HttpResponse
from django.db.models import Q


from patrimonio.models import Bem
from vistoria.models import Inventario, VistoriaBem
from .utils import (
    SEI, PORTARIA, PERIODO,
    parse_conta_contabil, valor_bem,
    is_encontrado, is_nao_encontrado, is_divergente,
    coletar_divergencias, diferencas_detalhadas, export_csv,
    thumbnail_url, thumbnail_pair,
)

# Opcional (bens sem registro)
try:
    from vistoria.models import VistoriaExtra
except Exception:
    VistoriaExtra = None

# Config persistida do relatório final (opcional)
try:
    from .models import RelatorioConfig
    from .forms import RelatorioConfigForm
except Exception:
    RelatorioConfig = None
    RelatorioConfigForm = None


# ----------------------------- utilidades leves -----------------------------
def _split_sala_bloco_text(s: str):
    """Texto SUAP do tipo 'SALA ... (BLOCO ...)' -> (sala_nome, bloco_nome)."""
    if not s:
        return ("", "")
    s = s.strip()
    if s.endswith(")") and "(" in s:
        i = s.rfind("(")
        return (s[:i].strip(), s[i+1:-1].strip())
    return (s, "")

def _nome_bloco(obj):
    sala_txt = getattr(obj, "sala", None) or getattr(obj, "sala_atual", None) or ""
    _, bloco = _split_sala_bloco_text(sala_txt)
    if bloco:
        return bloco
    # alternativas de texto
    return getattr(obj, "bloco_nome", None) or getattr(obj, "predio", None) or ""

def _nome_sala(obj):
    sala_txt = getattr(obj, "sala", None) or getattr(obj, "sala_atual", None) or ""
    sala, _ = _split_sala_bloco_text(sala_txt)
    if sala:
        return sala
    return getattr(obj, "sala_nome", None) or getattr(obj, "local", None) or ""

def _top_tipos_divergencia(inv, vb_qs):
    cont = defaultdict(int)
    for vb in vb_qs:
        tipos = coletar_divergencias(vb) or []
        for t in tipos:
            tt = str(t).strip().lower()
            if tt == "não encontrado":
                continue
            # só consideramos etiqueta AUSENTE como divergência
            if tt.startswith("etiqueta") and tt != "etiqueta (ausente)":
                continue
            cont[t] += 1
    pares = sorted(cont.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return [{"rotulo": k, "qtd": v} for k, v in pares]

def _top_blocos_pendencias(inv, bens_qs, vb_map):
    pend = defaultdict(int)
    for bem in bens_qs:
        vb = vb_map.get(bem.id)
        bloco = _nome_bloco(bem) or "—"
        if not vb:
            pend[bloco] += 1  # não vistoriado = pendência
            continue
        if is_nao_encontrado(vb):
            pend[bloco] += 1
            continue
        tipos = coletar_divergencias(vb) or []
        has_div = False
        for t in tipos:
            tt = str(t).strip().lower()
            if tt == "não encontrado":
                continue
            if tt.startswith("etiqueta") and tt != "etiqueta (ausente)":
                continue
            has_div = True
            break
        if has_div:
            pend[bloco] += 1
    pares = sorted(pend.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return [{"bloco": k, "qtd": v} for k, v in pares]

def _admin_ctx(request: HttpRequest, extra: dict):
    ctx = admin_site.each_context(request)
    ctx.update(extra)
    ctx.setdefault('SEI', SEI)
    ctx.setdefault('PORTARIA', PORTARIA)
    ctx.setdefault('PERIODO', PERIODO)
    return ctx

def _inventario_ativo():
    return Inventario.objects.filter(ativo=True).order_by('-ano').first()

def index(request: HttpRequest):
    return redirect('relatorios:final')


# Preferir sala/bloco OBSERVADOS na vistoria; cair para SUAP se não houver
def _sala_bloco_para_relatorio(vb, bem):
    sala_obs = (getattr(vb, "sala_obs_nome", "") or "").strip()
    bloco_obs = (getattr(vb, "sala_obs_bloco", "") or "").strip()
    if sala_obs or bloco_obs:
        return (sala_obs or "—", bloco_obs or "—")
    # fallback SUAP
    sala_nome, bloco_nome = _split_sala_bloco_text(getattr(bem, "sala", "") or "")
    return (sala_nome or "—", bloco_nome or "—")

def _param_bool(v, default=True):
    if v is None:
        return default
    s = str(v).strip().lower()
    return s not in {"0", "false", "no", "n", "off"}


# ----------------------------- Conta contábil (opção A) -----------------------------
def _norm_status(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode()
    s = s.upper()
    s = re.sub(r"[^A-Z0-9]+", "_", s)
    return s.strip("_")

_OK_STATUSES = {"ENCONTRADO", "FOUND", "OK", "CONFERIDO"}
_NAO_STATUSES = {
    "NAO_ENCONTRADO", "NAOENCONTRADO", "NAO-ENCONTRADO", "NAO ENCONTRADO",
    "NAO_LOCALIZADO", "NOT_FOUND", "MISSING", "PERDIDO"
}

def _has_real_divergencia(vb) -> bool:
    """
    Divergência real = qualquer diff SUAP×Vistoria que não seja:
      - 'observação'
      - 'divergência (não classificada)'
      - 'não encontrado' (já classificado antes)
    """
    diffs = diferencas_detalhadas(vb) or []
    ignore = {"observação", "divergência (não classificada)", "não encontrado"}
    for d in diffs:
        campo = (d.get("campo") or "").strip().lower()
        if campo not in ignore:
            return True
    return False

def _classificar_vb(vb) -> str:
    """
    'ok' | 'nao' | 'div'
    - NAO: marcado como não encontrado (status/flags)
    - DIV: tem divergência real (ex.: localização, série, etiqueta ausente, etc.)
    - OK : vistoriado sem divergências
    """
    st = _norm_status(getattr(vb, "status", None) or getattr(vb, "situacao", None) or getattr(vb, "resultado", None) or "")
    if st in _NAO_STATUSES or getattr(vb, "nao_encontrado", None) is True:
        return "nao"
    if getattr(vb, "divergente", False) or _has_real_divergencia(vb):
        return "div"
    if st in _OK_STATUSES or getattr(vb, "encontrado", None) is True or is_encontrado(vb):
        return "ok"
    # Sem sinalização especial -> considerar OK (vistoriado e sem divergências reais)
    return "ok"


def _agrega_por_conta_base_bem(inv: Inventario):
    """
    Opção A (detalhado):
    - Universo: todos os bens elegíveis do SUAP.
    - Chave: código da conta contábil (antes do hífen), vazio -> '(sem conta)'.
    - Classificação por bem:
        * Vistoriado -> 'ok' ou 'div' conforme _classificar_vb
        * Não vistoriado -> 'nao'   (regra do cliente)
    """
    # 1) Base SUAP
    try:
        bens_qs = inv.bens_elegiveis_qs()
    except Exception:
        bens_qs = Bem.objects.all()

    contas = defaultdict(lambda: {
        'codigo': '',
        'qtd_total': 0, 'val_total': 0.0,
        'qtd_vist': 0,
        'qtd_ok': 0, 'val_ok': 0.0,
        'qtd_nao': 0, 'val_nao': 0.0,
        'qtd_div': 0, 'val_div': 0.0,
    })
    bem_conta, bem_valor = {}, {}

    for bem in bens_qs:
        cod, _ = parse_conta_contabil(getattr(bem, 'conta_contabil', None) or getattr(bem, 'CONTA_CONTABIL', ''))
        cod = cod or '(sem conta)'
        v = valor_bem(bem)
        c = contas[cod]
        c['codigo'] = cod
        c['qtd_total'] += 1
        c['val_total'] += v
        bem_conta[bem.id] = cod
        bem_valor[bem.id] = v

    # 2) Última vistoria por bem (mapa bem_id -> vb mais recente)
    vb_qs = (
        VistoriaBem.objects.select_related('bem')
        .filter(inventario=inv)
        .order_by('bem_id', '-id')
    )
    vb_map = {}
    for vb in vb_qs:
        if vb.bem_id not in vb_map:
            vb_map[vb.bem_id] = vb

    # 3) Classifica bem a bem (inclui não vistoriados como 'nao')
    for bem_id, cod in bem_conta.items():
        c = contas[cod]
        v = bem_valor.get(bem_id, 0.0)
        vb = vb_map.get(bem_id)
        if vb:
            c['qtd_vist'] += 1
            cls = _classificar_vb(vb)
            if cls == 'ok':
                c['qtd_ok'] += 1; c['val_ok'] += v
            elif cls == 'div':
                c['qtd_div'] += 1; c['val_div'] += v
            else:
                c['qtd_nao'] += 1; c['val_nao'] += v
        else:
            c['qtd_nao'] += 1
            c['val_nao'] += v

    # 4) Linhas + totais
    linhas = []
    totais = {
        'qtd_total': 0, 'val_total': 0.0,
        'qtd_vist': 0,
        'qtd_ok': 0, 'val_ok': 0.0,
        'qtd_nao': 0, 'val_nao': 0.0,
        'qtd_div': 0, 'val_div': 0.0,
    }
    for key in sorted(contas.keys()):
        e = contas[key]
        cobertura = (e['qtd_vist'] / e['qtd_total'] * 100) if e['qtd_total'] else 0.0
        linhas.append({
            'conta_codigo': e['codigo'],
            'qtd_total': e['qtd_total'],
            'qtd_vist': e['qtd_vist'],
            'cobertura': round(cobertura, 1),
            'qtd_ok': e['qtd_ok'],   'val_ok': e['val_ok'],
            'qtd_nao': e['qtd_nao'], 'val_nao': e['val_nao'],
            'qtd_div': e['qtd_div'], 'val_div': e['val_div'],
        })
        for k in list(totais.keys()):
            totais[k] += e.get(k, 0)

    cobertura_geral = (totais['qtd_vist'] / totais['qtd_total'] * 100) if totais['qtd_total'] else 0.0
    total_row = {
        'conta_codigo': 'TOTAL',
        'qtd_total': totais['qtd_total'],
        'qtd_vist': totais['qtd_vist'],
        'cobertura': round(cobertura_geral, 1),
        'qtd_ok': totais['qtd_ok'],   'val_ok': totais['val_ok'],
        'qtd_nao': totais['qtd_nao'], 'val_nao': totais['val_nao'],
        'qtd_div': totais['qtd_div'], 'val_div': totais['val_div'],
    }
    return linhas, total_row


# ----------------------------- Andamento por Bloco/Sala -----------------------------
def _build_andamento(inv: Inventario):
    """
    Cobertura é contra a base SUAP; extras contam separado.
    """
    try:
        bens_suap = inv.bens_elegiveis_qs()
    except Exception:
        bens_suap = Bem.objects.all()

    blocos = defaultdict(lambda: defaultdict(lambda: {
        'elegiveis': 0, 'vistoriados': 0, 'nao_encontrados': 0
    }))

    for bem in bens_suap:
        sala_txt = getattr(bem, "sala", "") or ""
        sala_nome, bloco_nome = _split_sala_bloco_text(sala_txt)
        bloco_nome = bloco_nome or "—"
        sala_nome = sala_nome or "—"
        blocos[bloco_nome][sala_nome]['elegiveis'] += 1

    vb_qs = VistoriaBem.objects.select_related("bem").filter(inventario=inv)
    for vb in vb_qs:
        b = vb.bem
        sala_txt = getattr(b, "sala", "") or ""
        sala_nome, bloco_nome = _split_sala_bloco_text(sala_txt)
        bloco_nome = bloco_nome or "—"
        sala_nome = sala_nome or "—"
        blocos[bloco_nome][sala_nome]['vistoriados'] += 1
        if is_nao_encontrado(vb):
            blocos[bloco_nome][sala_nome]['nao_encontrados'] += 1

    extras_por_bloco_sala = defaultdict(int)
    if VistoriaExtra:
        for ve in VistoriaExtra.objects.filter(inventario=inv).only("sala_obs_bloco", "sala_obs_nome"):
            b = (ve.sala_obs_bloco or "—").strip() or "—"
            s = (ve.sala_obs_nome or "—").strip() or "—"
            extras_por_bloco_sala[(b, s)] += 1

    blocos_out = []
    totals = {'elegiveis': 0, 'vistoriados': 0, 'pendentes': 0, 'nao_encontrados': 0, 'sem_registro': 0}
    for bloco_nome in sorted(blocos.keys()):
        salas = []
        b_ag = {'elegiveis': 0, 'vistoriados': 0, 'pendentes': 0, 'nao_encontrados': 0, 'sem_registro': 0}
        for sala_nome in sorted(blocos[bloco_nome].keys()):
            d = blocos[bloco_nome][sala_nome]
            pend = max(d['elegiveis'] - d['vistoriados'], 0)
            sem_reg = extras_por_bloco_sala.get((bloco_nome, sala_nome), 0)
            salas.append({
                'sala': sala_nome,
                'elegiveis': d['elegiveis'],
                'vistoriados': d['vistoriados'],
                'pendentes': pend,
                'nao_encontrados': d['nao_encontrados'],
                'sem_registro': sem_reg,
            })
            b_ag['elegiveis'] += d['elegiveis']
            b_ag['vistoriados'] += d['vistoriados']
            b_ag['pendentes'] += pend
            b_ag['nao_encontrados'] += d['nao_encontrados']
            b_ag['sem_registro'] += sem_reg

        blocos_out.append({
            'bloco': bloco_nome,
            **b_ag,
            'salas': salas,
        })
        for k in totals.keys():
            totals[k] += b_ag[k]

    return {'totais': totals, 'blocos': blocos_out}


# ----------------------------- Relatório Final -----------------------------
@staff_member_required
def relatorio_final(request: HttpRequest):
    inv = _inventario_ativo()
    cfg = None; form = None
    if inv and RelatorioConfig and RelatorioConfigForm:
        cfg, _ = RelatorioConfig.objects.get_or_create(inventario=inv)
        if request.method == 'POST':
            form = RelatorioConfigForm(request.POST, request.FILES, instance=cfg)
            if form.is_valid():
                form.save(); return redirect('relatorios:final')
        else:
            form = RelatorioConfigForm(instance=cfg)

    linhas, total_row = _agrega_por_conta_base_bem(inv) if inv else ([], None)
    andamento = _build_andamento(inv) if inv else {'totais': {}, 'blocos': []}

    kpis = None; graficos = {"cobertura_por_conta": [], "top_tipos": [], "top_blocos": []}
    if inv and total_row:
        total_suap_itens = total_row["qtd_total"]
        total_suap_valor = (total_row["val_ok"] + total_row["val_nao"] + total_row["val_div"])
        kpis = {
            "suap_itens": total_suap_itens,
            "suap_valor": total_suap_valor,
            "vist_itens": total_row["qtd_vist"],
            "cobertura": total_row["cobertura"],
            "ok_itens": total_row["qtd_ok"], "ok_valor": total_row["val_ok"],
            "nao_itens": total_row["qtd_nao"], "nao_valor": total_row["val_nao"],
            "div_itens": total_row["qtd_div"], "div_valor": total_row["val_div"],
        }
        cov_sorted = sorted(
            [{"conta": l["conta_codigo"], "cobertura": float(l["cobertura"])} for l in linhas],
            key=lambda d: d["cobertura"], reverse=True
        )[:12]
        graficos["cobertura_por_conta"] = cov_sorted
        vb_qs = VistoriaBem.objects.select_related("bem").filter(inventario=inv)
        graficos["top_tipos"] = _top_tipos_divergencia(inv, vb_qs)
        bens_qs = Bem.objects.all(); vb_map = {vb.bem_id: vb for vb in vb_qs}
        graficos["top_blocos"] = _top_blocos_pendencias(inv, bens_qs, vb_map)

    # Força exibição de gráficos no Final (mesmo com cobertura baixa)
    show_charts = True

    return render(request, "relatorios/final.html", _admin_ctx(request, {
        "title": "Relatório Final (Dashboard)",
        "cfg": cfg, "form": form,
        "linhas": linhas, "total_row": total_row,
        "kpis": kpis, "graficos": graficos,
        "andamento": andamento,
        "show_charts": show_charts,  # <-- usar no template
    }))


# ----------------------------- Relatório Operacional -----------------------------
@staff_member_required
def relatorio_operacional(request: HttpRequest):
    inv = _inventario_ativo()

    # ✅ carrega a config para o cabeçalho (logo, textos etc.)
    cfg = None
    if inv and RelatorioConfig:
        try:
            cfg, _ = RelatorioConfig.objects.get_or_create(inventario=inv)
        except Exception:
            cfg = None

    show_images = True
    for key in ("fotos", "img", "images"):
        if key in request.GET:
            show_images = _param_bool(request.GET.get(key))
            break

    if not inv:
        return render(request, "relatorios/operacional.html", _admin_ctx(request, {
            "title": "Relatório Operacional",
            "cfg": cfg,                     # <-- passa cfg aqui também
            "grupos": [], "extras": [],
            "andamento": {'totais': {}, 'blocos': []},
            "show_images": show_images,
        }))

    andamento = _build_andamento(inv)

    vb_qs = (
        VistoriaBem.objects.select_related("bem")
        .filter(inventario=inv)
        .filter(
            Q(divergente=True) |
            Q(status=VistoriaBem.Status.NAO_ENCONTRADO) |
            Q(etiqueta_possui=False)
        )
        .order_by("sala_obs_bloco", "sala_obs_nome", "bem__sala", "bem__tombamento")
    )

    grupos = []
    atual = None
    for vb in vb_qs:
        bem = vb.bem
        sala, bloco = _sala_bloco_para_relatorio(vb, bem)
        if not atual or atual["bloco"] != bloco or atual["sala"] != sala:
            atual = {"bloco": bloco, "sala": sala, "itens": []}
            grupos.append(atual)

        diffs = diferencas_detalhadas(vb)

        foto = getattr(vb, "foto_marcadagua", None)
        foto_url = None; thumb_url = None; print_url = None
        if foto and getattr(foto, "name", ""):
            try:
                if foto.storage.exists(foto.name):
                    foto_url = foto.url
                    thumb_url = thumbnail_url(foto, size=(200, 200), quality=40)
                    print_url = thumbnail_url(foto, size=(200, 200), quality=40)
            except Exception:
                foto_url = None

        atual["itens"].append({
            "tombamento": getattr(bem, "tombamento", "") or getattr(bem, "numero_tombamento", "") or "",
            "descricao": getattr(bem, "descricao", None) or getattr(bem, "descricao_suap", None) or "",
            "diffs": diffs,
            "foto_url": foto_url,
            "foto_full_url": foto_url,
            "thumb_url": thumb_url,
            "print_url": print_url,
        })

    # extras (sem registro)
    extras = []
    if VistoriaExtra:
        ve_qs = VistoriaExtra.objects.filter(inventario=inv).order_by("sala_obs_bloco", "sala_obs_nome", "id")
        atual = None
        for ve in ve_qs:
            bloco = (ve.sala_obs_bloco or "").strip() or "—"
            sala = (ve.sala_obs_nome or "").strip() or "—"
            if not atual or atual["bloco"] != bloco or atual["sala"] != sala:
                atual = {"bloco": bloco, "sala": sala, "itens": []}
                extras.append(atual)

            foto = getattr(ve, "foto_marcadagua", None)
            foto_url = None; thumb_url = None; print_url = None
            if foto and getattr(foto, "name", ""):
                try:
                    if foto.storage.exists(foto.name):
                        foto_url = foto.url
                        thumb_url = thumbnail_url(foto, size=(200, 200), quality=40)
                        print_url = thumbnail_url(foto, size=(200, 200), quality=40)
                except Exception:
                    foto_url = None

            atual["itens"].append({
                "descricao": (ve.descricao_obs or "").strip(),
                "serie": (ve.numero_serie_obs or "").strip(),
                "estado": (ve.estado_obs or "").strip(),
                "responsavel": (ve.responsavel_obs or "").strip(),
                "etiqueta_ausente": (ve.etiqueta_possui is False),
                "obs": (ve.observacoes or "").strip(),
                "foto_url": foto_url,
                "foto_full_url": foto_url,
                "thumb_url": thumb_url,
                "print_url": print_url,
            })

    return render(request, "relatorios/operacional.html", _admin_ctx(request, {
        "title": "Relatório Operacional",
        "cfg": cfg,                 # <-- e aqui no contexto final
        "grupos": grupos,
        "extras": extras,
        "andamento": andamento,
        "show_images": show_images,
    }))


# ----------------------------- Relatórios auxiliares existentes -----------------------------
@staff_member_required
def inventario_por_conta(request: HttpRequest):
    inv = _inventario_ativo()
    linhas, total_row = _agrega_por_conta_base_bem(inv) if inv else ([], None)

    if request.GET.get('export') == 'csv':
        headers = [
            'Conta (código)', 'Total de Bens', 'Vistoriados (itens)', 'Cobertura (%)',
            'Encontrados (itens)', 'Valor Encontrados (R$)',
            'Não Encontrados (itens)', 'Valor Não Encontrados (R$)',
            'Divergentes (itens)', 'Valor Divergentes (R$)',
        ]
        rows = [
            [
                l['conta_codigo'], l['qtd_total'], l['qtd_vist'], f"{l['cobertura']:.1f}",
                l['qtd_ok'], f"{l['val_ok']:.2f}",
                l['qtd_nao'], f"{l['val_nao']:.2f}",
                l['qtd_div'], f"{l['val_div']:.2f}",
            ] for l in linhas
        ]
        if total_row:
            rows.append([
                total_row['conta_codigo'], total_row['qtd_total'], total_row['qtd_vist'], f"{total_row['cobertura']:.1f}",
                total_row['qtd_ok'], f"{total_row['val_ok']:.2f}",
                total_row['qtd_nao'], f"{total_row['val_nao']:.2f}",
                total_row['qtd_div'], f"{total_row['val_div']:.2f}",
            ])
        return export_csv(rows, headers, 'inventario_por_conta.csv')

    # ---------- Dados p/ gráficos do quadro por conta ----------
    top = sorted(linhas, key=lambda l: l['qtd_total'], reverse=True)[:12]
    charts = {
        "top_labels": [l["conta_codigo"] for l in top],
        "top_ok":  [int(l["qtd_ok"])  for l in top],
        "top_div": [int(l["qtd_div"]) for l in top],
        "top_nao": [int(l["qtd_nao"]) for l in top],
        "tot_itens": {
            "ok":  int(sum(l["qtd_ok"]  for l in linhas)),
            "div": int(sum(l["qtd_div"] for l in linhas)),
            "nao": int(sum(l["qtd_nao"] for l in linhas)),
        },
        "tot_valor": {
            "ok":  float(sum(l["val_ok"]  for l in linhas)),
            "div": float(sum(l["val_div"] for l in linhas)),
            "nao": float(sum(l["val_nao"] for l in linhas)),
        }
    }

    return render(request, 'relatorios/inventario_por_conta.html', _admin_ctx(request, {
        'title': 'Inventário por Conta Contábil',
        'inventario': inv,
        'linhas': linhas,
        'total_row': total_row,
        'charts': charts,
    }))

@staff_member_required
def mapa_nao_conformidades(request: HttpRequest):
    inv = _inventario_ativo()
    qs = VistoriaBem.objects.select_related('bem').filter(inventario=inv) if inv else VistoriaBem.objects.none()

    linhas = []
    for vb in qs:
        if not is_divergente(vb) and not is_nao_encontrado(vb):
            continue
        bem = vb.bem
        conta_raw = getattr(bem, 'conta_contabil', None) or getattr(bem, 'CONTA_CONTABIL', '')
        cod, _ = parse_conta_contabil(conta_raw)

        sala = getattr(bem, 'sala', None)
        sala_nome = getattr(sala, 'nome', None) or getattr(sala, 'descricao', None) or str(sala or '')
        setor = getattr(sala, 'setor', None)
        setor_nome = getattr(setor, 'nome', None) or getattr(setor, 'descricao', None) or str(setor or '')
        resp = getattr(bem, 'responsavel', None)
        resp_nome = getattr(resp, 'nome', None) or getattr(resp, 'first_name', None) or str(resp or '')
        tomb = getattr(bem, 'tombamento', None) or getattr(bem, 'numero_tombamento', None) or ''
        desc_bem = getattr(bem, 'descricao', None) or getattr(bem, 'descricao_suap', None) or ''
        estado = getattr(vb, 'estado', None) or getattr(vb, 'estado_conservacao', None) or ''

        diffs = diferencas_detalhadas(vb) or [{"campo": "divergência (não classificada)", "suap": "", "vistoria": ""}]

        for d in diffs:
            linhas.append({
                'campo': d['campo'], 'suap': d['suap'], 'vistoria': d['vistoria'],
                'setor': setor_nome, 'sala': sala_nome, 'responsavel': resp_nome,
                'tombamento': tomb, 'descricao_bem': desc_bem, 'conta_codigo': cod, 'estado': estado,
            })

    if request.GET.get('export') == 'csv':
        headers = [
            'Campo divergente', 'Valor (SUAP)', 'Valor (Vistoria)',
            'Setor/Unidade', 'Sala', 'Responsável', 'Tombamento',
            'Descrição do Bem', 'Conta (código)', 'Estado de Conservação'
        ]
        rows = [[l['campo'], l['suap'], l['vistoria'], l['setor'], l['sala'], l['responsavel'],
                 l['tombamento'], l['descricao_bem'], l['conta_codigo'], l['estado']] for l in linhas]
        return export_csv(rows, headers, 'mapa_nao_conformidades.csv')

    return render(request, 'relatorios/mapa_nao_conformidades.html', _admin_ctx(request, {
        'title': 'Mapa de Não Conformidades',
        'inventario': inv,
        'linhas': linhas,
    }))


# ----------------------------- Exportar fotos (ZIP) -----------------------------
_re_slug = re.compile(r"[^A-Za-z0-9\-_. ]+")
def _slugify(s: str, maxlen: int = 60) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("/", "-").replace("\\", "-")
    s = _re_slug.sub("", s)
    s = " ".join(s.split())
    return s[:maxlen].strip() or "_"

def _safe_name_for_bem(bem, prefix="") -> str:
    tomb = getattr(bem, "tombamento", "") or getattr(bem, "numero_tombamento", "") or ""
    desc = (getattr(bem, "descricao", None) or getattr(bem, "descricao_suap", None) or "")[:40]
    base = f"{tomb}_{desc}".strip("_ ")
    return _slugify(f"{prefix}{base}", 80)

def _safe_folder(bloco: str, sala: str) -> str:
    b = _slugify(bloco or "sem_bloco", 60)
    s = _slugify(sala or "sem_sala", 60)
    return f"{b}/{s}"

def _is_divergente_para_zip(vb) -> bool:
    from .utils import diferencas_detalhadas, is_nao_encontrado
    if is_nao_encontrado(vb):
        return True
    if getattr(vb, "etiqueta_possui", True) is False:
        return True
    diffs = diferencas_detalhadas(vb) or []
    for d in diffs:
        campo = (d.get("campo") or "").strip().lower()
        if campo not in ("observação", "divergência (não classificada)"):
            return True
    return False


@staff_member_required
def exportar_fotos(request: HttpRequest) -> HttpResponse:
    inv = _inventario_ativo()
    if not inv:
        return HttpResponse("Inventário ativo não encontrado.", status=404)

    buffer = io.BytesIO()
    z = zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)

    # Vistoriados (cadastrados)
    vb_qs = VistoriaBem.objects.select_related("bem").filter(inventario=inv).order_by("bem__sala", "bem__tombamento")
    for vb in vb_qs:
        foto = getattr(vb, "foto_marcadagua", None)
        if not (foto and getattr(foto, "name", "")):
            continue
        # Caminho seguro sem disparar _require_file
        file_path = Path(settings.MEDIA_ROOT) / foto.name
        if not file_path.exists():
            continue

        bem = vb.bem
        sala_nome, bloco_nome = _split_sala_bloco_text(getattr(bem, "sala", "") or "")
        folder = "divergentes" if _is_divergente_para_zip(vb) else "conformes"
        sub = _safe_folder(bloco_nome, sala_nome)
        fname = _safe_name_for_bem(bem)
        ext = file_path.suffix.lower() or ".jpg"
        arcname = f"{folder}/{sub}/{fname}{ext}"
        try:
            z.write(str(file_path), arcname)
        except Exception:
            pass

    # Sem registro (extras)
    if VistoriaExtra:
        ve_qs = VistoriaExtra.objects.filter(inventario=inv).order_by("sala_obs_bloco", "sala_obs_nome", "id")
        for ve in ve_qs:
            foto = getattr(ve, "foto_marcadagua", None)
            if not (foto and getattr(foto, "name", "")):
                continue
            file_path = Path(settings.MEDIA_ROOT) / foto.name
            if not file_path.exists():
                continue
            folder = "sem_registro"
            sub = _safe_folder(ve.sala_obs_bloco or "", ve.sala_obs_nome or "")
            base = (ve.descricao_obs or "sem_descricao")[:60]
            fname = _slugify(base, 80)
            ext = file_path.suffix.lower() or ".jpg"
            arcname = f"{folder}/{sub}/{fname}_{ve.id}{ext}"
            try:
                z.write(str(file_path), arcname)
            except Exception:
                pass

    z.close()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"fotos_inventario_{getattr(inv, 'ano', 'atual')}_{stamp}.zip"
    buffer.seek(0)
    resp = StreamingHttpResponse(buffer, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
