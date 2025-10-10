from datetime import timedelta
from django import template
from django.utils import timezone
from django.db.models import Count
from django.db.models.functions import TruncDate

from patrimonio.models import Bem
from vistoria.models import Inventario, VistoriaBem
try:
    from vistoria.models import VistoriaExtra
except Exception:
    VistoriaExtra = None

register = template.Library()

# --------- helpers ---------
def _inventario_ativo():
    return Inventario.objects.filter(ativo=True).order_by("-ano").first()

def _split_sala_bloco_text(s: str | None):
    if not s:
        return ("", "")
    s = s.strip()
    if s.endswith(")") and "(" in s:
        i = s.rfind("(")
        return (s[:i].strip(), s[i+1:-1].strip())
    return (s, "")

def _andamento_por_bloco(inv):
    """Top pendências por bloco (pendente = elegível - vistoriado + não encontrado explícito)."""
    # Base SUAP
    try:
        bens_suap = inv.bens_elegiveis_qs()
    except Exception:
        bens_suap = Bem.objects.all()

    mapa = {}
    for b in bens_suap.only("id", "sala"):
        sala, bloco = _split_sala_bloco_text(getattr(b, "sala", "") or "")
        bloco = bloco or "—"
        d = mapa.setdefault(bloco, {"elegiveis": 0, "vistoriados": 0, "nao": 0})
        d["elegiveis"] += 1

    vb_qs = VistoriaBem.objects.filter(inventario=inv).only("id", "bem_id", "status")
    for vb in vb_qs.select_related("bem"):
        sala, bloco = _split_sala_bloco_text(getattr(vb.bem, "sala", "") or "")
        bloco = bloco or "—"
        d = mapa.setdefault(bloco, {"elegiveis": 0, "vistoriados": 0, "nao": 0})
        d["vistoriados"] += 1
        if (getattr(vb, "status", "") or "").upper() == "NAO_ENCONTRADO":
            d["nao"] += 1

    out = []
    for bloco, d in mapa.items():
        pend = max(d["elegiveis"] - d["vistoriados"], 0) + d["nao"]
        out.append({"bloco": bloco, "pend": pend})
    out.sort(key=lambda x: (-x["pend"], x["bloco"]))
    return out[:8]

# --------- tag principal ---------
@register.inclusion_tag("relatorios/_admin_dashboard.html", takes_context=True)
def execucao_panel(context):
    inv = _inventario_ativo()

    elegiveis = inv.bens_elegiveis_qs().count() if inv else 0
    vb = VistoriaBem.objects.filter(inventario=inv) if inv else VistoriaBem.objects.none()
    vist = vb.count()
    cobertura = (vist / elegiveis * 100) if elegiveis else 0.0

    diverg = vb.filter(divergente=True).count() if inv else 0
    nao = vb.filter(status="NAO_ENCONTRADO").count() if inv else 0
    extras = VistoriaExtra.objects.filter(inventario=inv).count() if (inv and VistoriaExtra) else 0

    # Produção últimos 14 dias (datas contínuas)
    hoje = timezone.localdate()
    bruta = (
        vb.annotate(d=TruncDate("criado_em"))
          .values("d").annotate(qtd=Count("id"))
          .order_by("d")
    ) if inv else []
    por_data = {r["d"]: r["qtd"] for r in bruta}
    series14 = []
    max14 = 1
    for i in range(13, -1, -1):
        dia = hoje - timedelta(days=i)
        qtd = int(por_data.get(dia, 0))
        max14 = max(max14, qtd)
        series14.append({"data": dia, "qtd": qtd})
    for p in series14:
        p["pct"] = int(p["qtd"] * 100 / max14)  # para progress-bar

    ult7 = series14[-7:]
    total7 = sum(p["qtd"] for p in ult7)
    pico7 = max((p["qtd"] for p in ult7), default=0)
    vale7 = min((p["qtd"] for p in ult7), default=0)

    # Meta diária simples (se tiver fim no modelo)
    fim = getattr(inv, "periodo_fim", None) or getattr(inv, "fim", None) or getattr(inv, "data_fim", None)
    dias_rest = (fim - hoje).days + 1 if (inv and fim) else None
    meta_dia = ((elegiveis - vist) / max(dias_rest, 1)) if dias_rest else None
    status_meta = None
    if dias_rest and dias_rest > 0:
        dias_passados = (hoje - (getattr(inv, "periodo_inicio", None) or getattr(inv, "inicio", None) or getattr(inv, "data_inicio", hoje))).days + 1
        esperado = elegiveis * (dias_passados / max((dias_passados + dias_rest - 1), 1))
        status_meta = "adiantado" if vist >= esperado else "atrasado"

    top_blocos = _andamento_por_bloco(inv) if inv else []

    return {
        "inv": inv,
        "elegiveis": elegiveis,
        "vist": vist,
        "cobertura": cobertura,
        "diverg": diverg,
        "nao": nao,
        "extras": extras,
        "series14": series14,
        "total7": total7,
        "pico7": pico7,
        "vale7": vale7,
        "dias_rest": dias_rest,
        "meta_dia": meta_dia,
        "status_meta": status_meta,
        "top_blocos": top_blocos,
    }
