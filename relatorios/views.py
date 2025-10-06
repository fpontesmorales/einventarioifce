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


# --------------------------------------------------------------------------------------
# Helpers de contexto / localização
# --------------------------------------------------------------------------------------
def _admin_ctx(request: HttpRequest, extra: dict):
    ctx = admin_site.each_context(request)
    ctx.update(extra)
    ctx.setdefault('SEI', SEI)
    ctx.setdefault('PORTARIA', PORTARIA)
    ctx.setdefault('PERIODO', PERIODO)
    return ctx


def _inventario_ativo():
    return Inventario.objects.filter(ativo=True).order_by('-ano').first()


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


# --------------------------------------------------------------------------------------
# Top listas usadas nos gráficos
# --------------------------------------------------------------------------------------
def _top_tipos_divergencia(inv, vb_qs):
    """Top 5 tipos de divergência (exclui 'não encontrado' e QUALQUER etiqueta que não seja 'etiqueta (ausente)')."""
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


# --------------------------------------------------------------------------------------
# Agregação por conta (base: Bens/SUAP) – consolidado (itens e valores por status)
# --------------------------------------------------------------------------------------
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


# --------------------------------------------------------------------------------------
# Sumários (P1/P2/P3, checklist agregado, “sem registro”, tops de não encontrados)
# --------------------------------------------------------------------------------------
def _calc_sumarios(inv: Inventario):
    """
    Retorna:
      - sum_p: {'p1': int, 'p2': int, 'p3': int}
      - no_registro: int (VistoriaExtra, se existir)
      - checklist: dict por tipo (Localização/Série/Responsável/Descrição/Marca-Modelo/Tombamento/Etiqueta)
                  com confere/diverge/na/ni
      - top_nao_contas: [{'conta': '123..', 'qtd': N}, ...]
      - top_nao_blocos: [{'bloco': 'X', 'qtd': N}, ...]
    """
    if not inv:
        return {
            "sum_p": {"p1": 0, "p2": 0, "p3": 0},
            "no_registro": 0,
            "checklist": {},
            "top_nao_contas": [],
            "top_nao_blocos": [],
        }

    vb_qs = VistoriaBem.objects.select_related('bem').filter(inventario=inv)
    bens_qs = Bem.objects.all()
    vb_map = {vb.bem_id: vb for vb in vb_qs}

    # ---------------- P1/P2/P3 por ITEM ----------------
    p1 = p2 = p3 = 0
    for bem in bens_qs:
        vb = vb_map.get(bem.id)
        if not vb or is_nao_encontrado(vb):
            p1 += 1  # não vistoriado/nao encontrado entra em P1
            continue
        tipos = [t.lower() for t in (coletar_divergencias(vb) or [])]
        if any(t in {"tombamento divergente", "tombamento", "etiqueta (ausente)"} for t in tipos):
            p1 += 1
        elif any(t in {"localização", "série", "responsável"} for t in tipos):
            p2 += 1
        elif any(t in {"descrição", "marca/modelo", "estado"} for t in tipos):
            p3 += 1

    # ---------------- Checklist agregado (Confere/Divergente/NA/NI) ----------------
    tipos_chk = [
        ("localização", "Localização"),
        ("série", "Série"),
        ("responsável", "Responsável"),
        ("descrição", "Descrição"),
        ("marca/modelo", "Marca/Modelo"),
        ("tombamento divergente", "Tombamento"),
        ("etiqueta (ausente)", "Etiqueta"),
    ]
    checklist = {label: {"confere": 0, "diverge": 0, "na": 0, "ni": 0} for _, label in tipos_chk}

    for bem in bens_qs:
        vb = vb_map.get(bem.id)
        if not vb or is_nao_encontrado(vb):
            for _, label in tipos_chk:
                checklist[label]["ni"] += 1
            continue

        tipos = [t.lower() for t in (coletar_divergencias(vb) or [])]
        for key, label in tipos_chk:
            if key == "tombamento divergente":
                diverge = ("tombamento divergente" in tipos) or ("tombamento" in tipos)
            else:
                diverge = (key in tipos)
            if diverge:
                checklist[label]["diverge"] += 1
            else:
                checklist[label]["confere"] += 1

    # ---------------- Top 5 "Não encontrados" por conta e por bloco ----------------
    nao_por_conta = defaultdict(int)
    nao_por_bloco = defaultdict(int)
    for bem in bens_qs:
        vb = vb_map.get(bem.id)
        # Não vistoriado OU marcado nao_encontrado
        if (not vb) or is_nao_encontrado(vb):
            conta_raw = getattr(bem, 'conta_contabil', None) or getattr(bem, 'CONTA_CONTABIL', '')
            cod, _ = parse_conta_contabil(conta_raw)
            nao_por_conta[cod or "(sem conta)"] += 1
            nao_por_bloco[_nome_bloco(bem) or "(sem bloco)"] += 1

    top_nao_contas = sorted(nao_por_conta.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    top_nao_blocos = sorted(nao_por_bloco.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    top_nao_contas = [{"conta": k, "qtd": v} for k, v in top_nao_contas]
    top_nao_blocos = [{"bloco": k, "qtd": v} for k, v in top_nao_blocos]

    # ---------------- VistoriaExtra: “sem registro no SUAP” ----------------
    no_registro = 0
    if VistoriaExtra:
        no_registro = VistoriaExtra.objects.filter(inventario=inv).count()

    return {
        "sum_p": {"p1": p1, "p2": p2, "p3": p3},
        "no_registro": no_registro,
        "checklist": checklist,
        "top_nao_contas": top_nao_contas,
        "top_nao_blocos": top_nao_blocos,
    }


# --------------------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------------------
def index(request: HttpRequest):
    return redirect('relatorios:final')


@staff_member_required
def relatorio_final(request: HttpRequest):
    """
    Relatório Final (executivo): textos (apresentação/metodologia),
    KPIs, gráficos e quadro por conta + sumários (P1/P2/P3, sem registro,
    checklist agregado e Top 5 "não encontrados" por conta/bloco).
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

    # KPIs + gráficos
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

        # 1) Cobertura por conta (ordenada desc) – top 12 para gráfico
        cov_sorted = sorted(
            [{"conta": l["conta_codigo"], "cobertura": float(l["cobertura"])} for l in linhas],
            key=lambda d: d["cobertura"], reverse=True
        )[:12]
        graficos["cobertura_por_conta"] = cov_sorted

        # 2) Top tipos de divergência (inventário atual)
        vb_qs = VistoriaBem.objects.select_related("bem").filter(inventario=inv)
        graficos["top_tipos"] = _top_tipos_divergencia(inv, vb_qs)

        # 3) Top blocos com pendências (base SUAP)
        bens_qs = Bem.objects.all()
        vb_map = {vb.bem_id: vb for vb in vb_qs}
        graficos["top_blocos"] = _top_blocos_pendencias(inv, bens_qs, vb_map)

    # Sumários executivos
    sumarios = _calc_sumarios(inv) if inv else {
        "sum_p": {"p1": 0, "p2": 0, "p3": 0},
        "no_registro": 0,
        "checklist": {},
        "top_nao_contas": [],
        "top_nao_blocos": [],
    }

    return render(request, "relatorios/final.html", _admin_ctx(request, {
        "title": "Relatório Final (Dashboard)",
        "cfg": cfg,
        "form": form,
        "linhas": linhas,
        "total_row": total_row,
        "kpis": kpis,
        "graficos": graficos,
        "sumarios": sumarios,   # <- novos blocos do template usam isso
    }))


# --------------------------------------------------------------------------------------
# Demais relatórios
# --------------------------------------------------------------------------------------
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

        # Diferenças SUAP × Vistoria
        diffs = diferencas_detalhadas(vb)
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
