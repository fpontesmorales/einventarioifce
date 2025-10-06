from collections import defaultdict

from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.http import HttpRequest

from patrimonio.models import Bem
from vistoria.models import Inventario, VistoriaBem
from .utils import (
    SEI, PORTARIA, PERIODO,
    parse_conta_contabil, valor_bem,
    is_encontrado, is_nao_encontrado, is_divergente,
    coletar_divergencias, diferencas_detalhadas, export_csv,
)

def _nome_bloco(obj):
    # tenta sala.bloco.nome/descricao
    sala = getattr(obj, "sala", None) or getattr(obj, "sala_atual", None)
    bloco = getattr(sala, "bloco", None)
    if bloco:
        return getattr(bloco, "nome", None) or getattr(bloco, "descricao", None) or str(bloco)
    # tenta texto solto
    return getattr(obj, "bloco_nome", None) or getattr(obj, "predio", None) or ""

def _nome_sala(obj):
    sala = getattr(obj, "sala", None) or getattr(obj, "sala_atual", None)
    if sala:
        return getattr(sala, "nome", None) or getattr(sala, "descricao", None) or str(sala)
    return getattr(obj, "sala_nome", None) or getattr(obj, "local", None) or ""

def _top_tipos_divergencia(inv, vb_qs):
    """Top 5 tipos de divergência (exclui 'não encontrado' e QUALQUER etiqueta que não seja 'etiqueta (ausente)')."""
    from .utils import coletar_divergencias  # lazy import
    cont = defaultdict(int)
    for vb in vb_qs:
        tipos = coletar_divergencias(vb) or []
        for t in tipos:
            tt = str(t).strip().lower()
            if tt == "não encontrado":
                continue
            # mantém só etiqueta ausente; ignora outras etiquetas
            if tt.startswith("etiqueta"):
                if tt != "etiqueta (ausente)":
                    continue
            cont[t] += 1
    pares = sorted(cont.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return [{"rotulo": k, "qtd": v} for k, v in pares]

def _top_blocos_pendencias(inv, bens_qs, vb_map):
    """
    Top 5 blocos com pendências (não encontrados + divergentes).
    Usa o BLOCO do SUAP (Bem) para consistência gerencial.
    """
    from .utils import is_encontrado, is_nao_encontrado, coletar_divergencias
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
        # conta como pendência se tiver qq divergência (exceto 'não encontrado' e etiquetas não-ausentes)
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

# Suporte opcional à configuração persistida do Relatório Final
try:
    from .models import RelatorioConfig
    from .forms import RelatorioConfigForm
except Exception:
    RelatorioConfig = None
    RelatorioConfigForm = None

# Opcional: pode não existir no projeto — tratamos com tolerância
try:
    from vistoria.models import VistoriaExtra  # “bens sem registro”
except Exception:
    VistoriaExtra = None


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


# -----------------------------
# Agregação por conta (base: Bens/SUAP) – já corrigida
# -----------------------------
def _agrega_por_conta_base_bem(inv: Inventario):
    bens_qs = Bem.objects.all()
    vb_qs = VistoriaBem.objects.select_related('bem').filter(inventario=inv)
    vb_by_bem_id = {vb.bem_id: vb for vb in vb_qs}

    agreg = defaultdict(lambda: {
        'codigo': '',
        'qtd_total': 0, 'val_total': 0.0,
        'qtd_vist': 0,
        'qtd_ok': 0, 'val_ok': 0.0,
        'qtd_nao': 0, 'val_nao': 0.0,
        'qtd_div': 0, 'val_div': 0.0,
    })

    for bem in bens_qs:
        conta_raw = getattr(bem, 'conta_contabil', None) or getattr(bem, 'CONTA_CONTABIL', '')
        cod, _ = parse_conta_contabil(conta_raw)
        key = cod or '(sem conta)'

        v = valor_bem(bem)
        entry = agreg[key]
        entry['codigo'] = key
        entry['qtd_total'] += 1
        entry['val_total'] += v

        vb = vb_by_bem_id.get(bem.id)
        if vb:
            entry['qtd_vist'] += 1
            if is_encontrado(vb):
                entry['qtd_ok'] += 1
                entry['val_ok'] += v
            elif is_divergente(vb):
                entry['qtd_div'] += 1
                entry['val_div'] += v
            elif is_nao_encontrado(vb):
                entry['qtd_nao'] += 1
                entry['val_nao'] += v
            else:
                entry['qtd_nao'] += 1
                entry['val_nao'] += v
        else:
            entry['qtd_nao'] += 1
            entry['val_nao'] += v

    linhas = []
    totais = {
        'qtd_total': 0, 'val_total': 0.0,
        'qtd_vist': 0,
        'qtd_ok': 0, 'val_ok': 0.0,
        'qtd_nao': 0, 'val_nao': 0.0,
        'qtd_div': 0, 'val_div': 0.0,
    }

    for key in sorted(agreg.keys()):
        e = agreg[key]
        cobertura = (e['qtd_vist'] / e['qtd_total'] * 100) if e['qtd_total'] else 0.0
        linhas.append({
            'conta_codigo': e['codigo'],
            'qtd_total': e['qtd_total'],
            'qtd_vist': e['qtd_vist'],
            'cobertura': round(cobertura, 1),
            'qtd_ok': e['qtd_ok'],
            'val_ok': e['val_ok'],
            'qtd_nao': e['qtd_nao'],
            'val_nao': e['val_nao'],
            'qtd_div': e['qtd_div'],
            'val_div': e['val_div'],
        })
        for k in totais.keys():
            if k.startswith('qtd') or k.startswith('val'):
                totais[k] += e.get(k, 0)

    cobertura_geral = (totais['qtd_vist'] / totais['qtd_total'] * 100) if totais['qtd_total'] else 0.0
    total_row = {
        'conta_codigo': 'TOTAL',
        'qtd_total': totais['qtd_total'],
        'qtd_vist': totais['qtd_vist'],
        'cobertura': round(cobertura_geral, 1),
        'qtd_ok': totais['qtd_ok'],
        'val_ok': totais['val_ok'],
        'qtd_nao': totais['qtd_nao'],
        'val_nao': totais['val_nao'],
        'qtd_div': totais['qtd_div'],
        'val_div': totais['val_div'],
    }
    return linhas, total_row


# -----------------------------
# RESUMOS – Mapa de NC e Sem Registro
# -----------------------------
def _resumo_nc(inv: Inventario):
    """Resumo enxuto do Mapa de NC: total de bens com NC e top 5 tipos."""
    if not inv:
        return {'total_bens': 0, 'total_registros': 0, 'top_tipos': []}

    qs = VistoriaBem.objects.select_related('bem').filter(inventario=inv)
    por_tipo = defaultdict(int)
    total_bens = 0

    for vb in qs:
        if not is_divergente(vb):
            continue
        total_bens += 1
        tipos = coletar_divergencias(vb) or ['divergência (não classificada)']
        for t in tipos:
            por_tipo[t] += 1

    total_registros = sum(por_tipo.values())
    top = sorted(por_tipo.items(), key=lambda x: (-x[1], x[0]))[:5]
    # retorna lista de dicts pra facilitar no template
    top_tipos = [{'tipo': k, 'qtd': v} for k, v in top]
    return {'total_bens': total_bens, 'total_registros': total_registros, 'top_tipos': top_tipos}


def _resumo_sem_registro(inv: Inventario):
    """Resumo de bens sem registro (VistoriaExtra). Tolerante a modelos sem FK 'sala'."""
    if not inv or not VistoriaExtra:
        return {'total': 0, 'top_setores': []}

    # Sem select_related, pois 'sala' pode não existir nesse modelo
    qs = VistoriaExtra.objects.filter(inventario=inv)
    total = qs.count()
    por_setor = defaultdict(int)

    for ve in qs:
        setor_nome = None

        # 1) Se houver FK 'setor' direto
        sdir = getattr(ve, 'setor', None)
        if sdir:
            setor_nome = getattr(sdir, 'nome', None) or getattr(sdir, 'descricao', None) or str(sdir)

        # 2) Se houver algo como 'sala' e 'sala.setor' (quando existir)
        if not setor_nome:
            sala = getattr(ve, 'sala', None)
            if sala:
                setor = getattr(sala, 'setor', None)
                if setor:
                    setor_nome = getattr(setor, 'nome', None) or getattr(setor, 'descricao', None) or str(setor)

        # 3) Campos de texto comuns em modelos simples
        if not setor_nome:
            setor_nome = (
                getattr(ve, 'setor_nome', None) or
                getattr(ve, 'unidade', None) or
                getattr(ve, 'local', None) or
                '—'
            )

        por_setor[setor_nome] += 1

    top = sorted(por_setor.items(), key=lambda x: (-x[1], x[0]))[:5]
    top_setores = [{'setor': k, 'qtd': v} for k, v in top]
    return {'total': total, 'top_setores': top_setores}



# -----------------------------
# Relatório Final (HTML p/ impressão)
# -----------------------------
@staff_member_required
@staff_member_required
def relatorio_final(request: HttpRequest):
    """
    Dashboard enxuto para gestão: KPIs, 3 gráficos (barras HTML/CSS) e quadro por conta.
    Continua com o formulário de configurações recolhido no final da página.
    """
    inv = _inventario_ativo()
    cfg = None
    form = None

    # Config persistida (se existir)
    if inv and RelatorioConfig and RelatorioConfigForm:
        cfg, _ = RelatorioConfig.objects.get_or_create(inventario=inv)
        if request.method == 'POST':
            form = RelatorioConfigForm(request.POST, request.FILES, instance=cfg)
            if form.is_valid():
                form.save()
                return redirect('relatorios:final')
        else:
            form = RelatorioConfigForm(instance=cfg)

    # Linhas por conta (base SUAP × Vistoria) + totais
    linhas, total_row = _agrega_por_conta_base_bem(inv) if inv else ([], None)

    # KPIs (totais gerais)
    kpis = None
    graficos = {"cobertura_por_conta": [], "top_tipos": [], "top_blocos": []}

    if inv and total_row:
        total_suap_itens = total_row["qtd_total"]
        total_suap_valor = (total_row["val_ok"] + total_row["val_nao"] + total_row["val_div"])
        kpis = {
            "suap_itens": total_suap_itens,
            "suap_valor": total_suap_valor,
            "vist_itens": total_row["qtd_vist"],
            "cobertura": total_row["cobertura"],  # já em %
            "ok_itens": total_row["qtd_ok"],
            "ok_valor": total_row["val_ok"],
            "nao_itens": total_row["qtd_nao"],
            "nao_valor": total_row["val_nao"],
            "div_itens": total_row["qtd_div"],
            "div_valor": total_row["val_div"],
        }

        # 1) Cobertura por conta (ordenada desc) – usaremos top 12 para o gráfico
        cov_sorted = sorted(
            [{"conta": l["conta_codigo"], "cobertura": float(l["cobertura"])} for l in linhas],
            key=lambda d: d["cobertura"], reverse=True
        )[:12]
        graficos["cobertura_por_conta"] = cov_sorted

        # 2) Top tipos de divergência (usa apenas o inventário atual)
        vb_qs = VistoriaBem.objects.select_related("bem").filter(inventario=inv)
        graficos["top_tipos"] = _top_tipos_divergencia(inv, vb_qs)

        # 3) Top blocos com pendências (base SUAP)
        bens_qs = Bem.objects.all()
        vb_map = {vb.bem_id: vb for vb in vb_qs}
        graficos["top_blocos"] = _top_blocos_pendencias(inv, bens_qs, vb_map)

    return render(request, "relatorios/final.html", _admin_ctx(request, {
        "title": "Relatório Final (Dashboard)",
        "cfg": cfg,
        "form": form,
        "linhas": linhas,
        "total_row": total_row,
        "kpis": kpis,
        "graficos": graficos,
    }))


# -----------------------------
# Demais relatórios (sem alterações além do cálculo já corrigido)
# -----------------------------
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

    return render(request, 'relatorios/inventario_por_conta.html', _admin_ctx(request, {
        'title': 'Inventário por Conta Contábil',
        'inventario': inv,
        'linhas': linhas,
        'total_row': total_row,
    }))


@staff_member_required
def mapa_nao_conformidades(request: HttpRequest):
    inv = _inventario_ativo()
    qs = VistoriaBem.objects.select_related('bem').filter(inventario=inv) if inv else VistoriaBem.objects.none()

    linhas = []
    for vb in qs:
        # só entra quem tem alguma divergência (ou não encontrado)
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

        # NOVO: diferenças SUAP × Vistoria
        diffs = diferencas_detalhadas(vb)

        # Se por algum motivo não detectou diffs, ainda assim registra um genérico
        if not diffs:
            diffs = [{"campo": "divergência (não classificada)", "suap": "", "vistoria": ""}]

        for d in diffs:
            linhas.append({
                'campo': d['campo'],
                'suap': d['suap'],
                'vistoria': d['vistoria'],
                'setor': setor_nome,
                'sala': sala_nome,
                'responsavel': resp_nome,
                'tombamento': tomb,
                'descricao_bem': desc_bem,
                'conta_codigo': cod,
                'estado': estado,
            })

    if request.GET.get('export') == 'csv':
        headers = [
            'Campo divergente', 'Valor (SUAP)', 'Valor (Vistoria)',
            'Setor/Unidade', 'Sala', 'Responsável',
            'Tombamento', 'Descrição do Bem', 'Conta (código)',
            'Estado de Conservação'
        ]
        rows = [
            [
                l['campo'], l['suap'], l['vistoria'],
                l['setor'], l['sala'], l['responsavel'],
                l['tombamento'], l['descricao_bem'], l['conta_codigo'],
                l['estado'],
            ] for l in linhas
        ]
        return export_csv(rows, headers, 'mapa_nao_conformidades.csv')

    return render(request, 'relatorios/mapa_nao_conformidades.html', _admin_ctx(request, {
        'title': 'Mapa de Não Conformidades',
        'inventario': inv,
        'linhas': linhas,
    }))
