from datetime import timedelta
from django import template
from django.utils import timezone
from vistoria.models import Inventario, VistoriaBem

register = template.Library()

def _inv_ativo():
    return Inventario.objects.filter(ativo=True).order_by("-ano").first()

@register.simple_tag
def dashboard_metrics():
    inv = _inv_ativo()
    if not inv:
        return {"elegiveis": 0, "vistoriados": 0, "cobertura": "0,0%", "ultimos7": 0}

    elegiveis = inv.bens_elegiveis_qs().count()
    base = VistoriaBem.objects.filter(inventario=inv)
    vist = base.count()
    cob = (vist * 100.0 / elegiveis) if elegiveis else 0.0
    ult7 = base.filter(atualizado_em__gte=timezone.now() - timedelta(days=7)).count()

    return {
        "elegiveis": elegiveis,
        "vistoriados": vist,
        "cobertura": f"{cob:.1f}%",
        "ultimos7": ult7,
    }
