from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, date
from typing import Optional, Tuple, Dict, List

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.shortcuts import render
from django.utils import timezone

from vistoria.models import VistoriaBem, Inventario, split_sala_bloco
from patrimonio.models import Bem  # pode ser útil no futuro

from .views import _admin_ctx, _inventario_ativo


# -------------------- utils de datas --------------------
def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _daterange_defaults(inv: Optional[Inventario]) -> Tuple[date, date]:
    """padrão = últimos 30 dias (limitando ao período do inventário se existir)"""
    hoje = timezone.localdate()
    ini = hoje - timedelta(days=30)
    fim = hoje

    for name in ("periodo_inicio", "inicio", "data_inicio"):
        d = getattr(inv, name, None)
        if d:
            try:
                ini = max(ini, d if isinstance(d, date) else d.date())
            except Exception:
                pass
            break
    for name in ("periodo_fim", "fim", "data_fim"):
        d = getattr(inv, name, None)
        if d:
            try:
                fim = min(fim, d if isinstance(d, date) else d.date())
            except Exception:
                pass
            break
    return ini, fim


# -------------------- view --------------------
@staff_member_required
def relatorio_execucao(request):
    inv = _inventario_ativo()

    # filtros
    ini_default, fim_default = _daterange_defaults(inv)
    ini = _parse_date(request.GET.get("ini")) or ini_default
    fim = _parse_date(request.GET.get("fim")) or fim_default
    if fim < ini:
        ini, fim = fim, ini

    try:
        meta_diaria = max(0, int(request.GET.get("meta") or 0))
    except Exception:
        meta_diaria = 0

    usuario_id = request.GET.get("u")  # opcional

    if not inv:
        return render(request, "relatorios/execucao.html", _admin_ctx(request, {
            "title": "Relatório de Execução",
            "inv": None,
            "filtros": {"ini": ini, "fim": fim, "meta": meta_diaria, "usuario_id": usuario_id, "usuarios": []},
            "kpis": None, "mix": None, "mix_pct": None,
            "diarias": [], "semanais": [], "mensais": [],
            "por_usuario": [], "por_bloco": [],
            "pacing": None,
        }))

    # base
    elegiveis_qs = inv.bens_elegiveis_qs()
    elegiveis = elegiveis_qs.count()

    base_all = VistoriaBem.objects.select_related("bem").filter(inventario=inv)
    if usuario_id:
        base_all = base_all.filter(Q(atualizado_por_id=usuario_id) | Q(criado_por_id=usuario_id))

    base_periodo = base_all.filter(atualizado_em__date__gte=ini, atualizado_em__date__lte=fim)

    # KPIs (geral)
    total_vist = base_all.count()
    cobertura = (total_vist * 100.0 / elegiveis) if elegiveis else 0.0
    ult7 = base_all.filter(atualizado_em__gte=timezone.now() - timedelta(days=7)).count()
    vist_periodo = base_periodo.count()

    # Mix (geral)
    qtd_nao = base_all.filter(status=VistoriaBem.Status.NAO_ENCONTRADO).count()
    qtd_div = base_all.filter(divergente=True).count()
    qtd_ok = max(total_vist - qtd_nao - qtd_div, 0)
    qtd_sem_foto = base_all.filter(Q(foto_marcadagua__isnull=True) | Q(foto_marcadagua="")).count()
    qtd_etq_ausente = base_all.filter(etiqueta_possui=False).count()
    mix = {
        "ok": qtd_ok, "div": qtd_div, "nao": qtd_nao,
        "sem_foto": qtd_sem_foto, "etq_ausente": qtd_etq_ausente,
        "total": total_vist,
    }
    denom = mix["total"] or 1
    mix_pct = {
        "ok": round(mix["ok"] * 100 / denom),
        "div": round(mix["div"] * 100 / denom),
        "nao": round(mix["nao"] * 100 / denom),
    }

    # Agregações temporais
    diarias = list(
        base_periodo.annotate(d=TruncDate("atualizado_em"))
        .values("d").annotate(qtd=Count("id")).order_by("d")
    )
    semanais = list(
        base_all.filter(atualizado_em__date__gte=ini - timedelta(weeks=12))
        .annotate(w=TruncWeek("atualizado_em"))
        .values("w").annotate(qtd=Count("id")).order_by("w")
    )
    mensais = list(
        base_all.filter(atualizado_em__year=timezone.localdate().year)
        .annotate(m=TruncMonth("atualizado_em"))
        .values("m").annotate(qtd=Count("id")).order_by("m")
    )

    def _add_pct(rows: List[Dict], key="qtd"):
        if not rows:
            return rows
        mx = max(r[key] for r in rows) or 1
        for r in rows:
            r["pct"] = int(round(r[key] * 100 / mx))
        return rows

    diarias = _add_pct(diarias)
    semanais = _add_pct(semanais)
    mensais = _add_pct(mensais)

    # Produtividade por usuário (no período)
    counts_by_user: Dict[int, int] = defaultdict(int)
    names_by_user: Dict[int, str] = {}
    for vb in base_periodo.values(
        "atualizado_por_id", "criado_por_id",
        "atualizado_por__first_name", "atualizado_por__username",
        "criado_por__first_name", "criado_por__username"
    ):
        uid = vb["atualizado_por_id"] or vb["criado_por_id"]
        if uid is None:
            continue
        nome = (
            vb["atualizado_por__first_name"] or vb["atualizado_por__username"]
            or vb["criado_por__first_name"] or vb["criado_por__username"]
            or f"Usuário {uid}"
        )
        counts_by_user[uid] += 1
        names_by_user[uid] = nome

    por_usuario = [{"id": uid, "nome": names_by_user.get(uid, f"Usuário {uid}"), "qtd": qtd}
                   for uid, qtd in counts_by_user.items()]
    por_usuario.sort(key=lambda x: x["qtd"], reverse=True)
    _add_pct(por_usuario)

    # Pendências por bloco (top 10): elegíveis sem vistoria
    vist_ids = set(base_all.values_list("bem_id", flat=True))
    pend_por_bloco: Dict[str, int] = defaultdict(int)
    for b in elegiveis_qs.only("id", "sala"):
        if b.id not in vist_ids:
            nome, bloco = split_sala_bloco(getattr(b, "sala", "") or "")
            pend_por_bloco[(bloco or "—")] += 1
    por_bloco = [{"bloco": k, "qtd": v} for k, v in pend_por_bloco.items()]
    por_bloco.sort(key=lambda x: x["qtd"], reverse=True)
    por_bloco = por_bloco[:10]
    _add_pct(por_bloco)

    # Pacing (no período filtrado)
    dias_periodo = (fim - ini).days + 1
    ritmo_medio = (sum(x["qtd"] for x in diarias) / dias_periodo) if dias_periodo > 0 else 0.0
    hoje = timezone.localdate()
    faltam = max((fim - min(max(ini, hoje), fim)).days + 1, 0)
    proj_ate_fim = int(round(total_vist + ritmo_medio * faltam))

    progress_pct = None
    if meta_diaria > 0 and dias_periodo > 0:
        alvo_total = meta_diaria * dias_periodo
        progress_pct = int(round(min(100, max(0, (vist_periodo * 100.0 / (alvo_total or 1))))))

    pacing = {
        "periodo": {"ini": ini, "fim": fim, "dias": dias_periodo},
        "ritmo_medio": ritmo_medio,
        "faltam_dias": faltam,
        "proj_total_ate_fim": proj_ate_fim,
        "vist_periodo": vist_periodo,
        "progress_pct": progress_pct,
    }

    # lista de usuários para o filtro
    usuarios = (base_all.values("atualizado_por_id", "atualizado_por__first_name", "atualizado_por__username")
                .order_by().distinct())
    usuarios_out = []
    for u in usuarios:
        uid = u["atualizado_por_id"]
        if uid is None:
            continue
        nome = u["atualizado_por__first_name"] or u["atualizado_por__username"] or f"Usuário {uid}"
        usuarios_out.append({"id": uid, "nome": nome})
    usuarios_out.sort(key=lambda d: d["nome"].lower())

    # exportações CSV
    export = request.GET.get("export")
    if export in {"diarias", "semanais", "mensais", "usuarios", "blocos"}:
        from .utils import export_csv
        if export == "diarias":
            return export_csv([[r["d"].strftime("%Y-%m-%d"), r["qtd"]] for r in diarias], ["dia", "qtd"], "execucao_diarias.csv")
        if export == "semanais":
            return export_csv([[r["w"].strftime("%G-%V"), r["qtd"]] for r in semanais], ["semana", "qtd"], "execucao_semanais.csv")
        if export == "mensais":
            return export_csv([[r["m"].strftime("%Y-%m"), r["qtd"]] for r in mensais], ["mes", "qtd"], "execucao_mensais.csv")
        if export == "usuarios":
            return export_csv([[r["nome"], r["qtd"]] for r in por_usuario], ["usuario", "qtd"], "execucao_por_usuario.csv")
        if export == "blocos":
            return export_csv([[r["bloco"], r["qtd"]] for r in por_bloco], ["bloco", "pendencias"], "execucao_pend_por_bloco.csv")

    ctx = {
        "title": "Relatório de Execução",
        "inv": inv,
        "filtros": {"ini": ini, "fim": fim, "meta": meta_diaria, "usuario_id": usuario_id, "usuarios": usuarios_out},
        "kpis": {"elegiveis": elegiveis, "vist_total": total_vist, "cobertura": cobertura, "ult7": ult7},
        "mix": mix, "mix_pct": mix_pct,
        "diarias": diarias, "semanais": semanais, "mensais": mensais,
        "por_usuario": por_usuario, "por_bloco": por_bloco,
        "pacing": pacing,
    }
    return render(request, "relatorios/execucao.html", _admin_ctx(request, ctx))
