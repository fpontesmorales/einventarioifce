from django import template
from django.template import engines
from django.utils.safestring import mark_safe


register = template.Library()

def _br_num(n, casas=2):
    try:
        v = float(n)
    except Exception:
        return "0,00"
    s = f"{v:,.{casas}f}"  # 1,234.56
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

@register.filter
def br_currency(v):
    return f"R$ {_br_num(v, 2)}"

@register.filter
def br_percent(v):
    return f"{_br_num(v, 1)} %"

@register.simple_tag(takes_context=True)
def render_vars(context, text):
    """
    Renderiza variáveis Django (ex.: {{ SEI }}, {{ PORTARIA }}, {{ PERIODO }})
    dentro de um texto salvo no banco.
    """
    if not text:
        return ""
    # base de dados segura (pode expandir se quiser expor mais chaves)
    data = {
        "SEI": context.get("SEI", ""),
        "PORTARIA": context.get("PORTARIA", ""),
        "PERIODO": context.get("PERIODO", ""),
    }
    # renderiza usando o próprio engine de templates
    tpl = engines["django"].from_string(str(text))
    rendered = tpl.render(context.flatten() | data)
    # mantém quebras de linha com CSS (no template já usamos white-space: pre-wrap)
    return mark_safe(rendered)
